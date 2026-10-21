"""Agent 主循环 —— DeepSeek Function Calling + SSE 事件流。

与原 app.py 的差异
------------------
原版是同步阻塞（Streamlit 不接受增量输出），用户点完只能干等。
这里改为**流式**：每调用一个工具就吐一个事件，最终回答逐字推送。
体验差距很大 —— 用户能看见 Agent 正在干什么，而不是盯着转圈。

事件协议（SSE）
---------------
    event: turn      data: {"turn": 1}                 回合开始
    event: tool      data: {"name": "...", "args": {}} 开始调用工具
    event: tool_done data: {"name": "...", "ok": true} 工具返回
    event: delta     data: {"text": "..."}             回答增量
    event: done      data: {"turns": 3}                回合结束
    event: error     data: {"message": "..."}          出错
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
from openai import OpenAI

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
from water_quality_mcp.src.water_quality_mcp.data_quality import measurement_error

from .tools import (
    SYSTEM_PROMPT_AUTO, SYSTEM_PROMPT_CHAT, TOOLS,
    execute_tool, truncate,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"
MAX_TURNS = 15
TIMEOUT = httpx.Timeout(120.0, connect=10.0)
REQUIRED_ANALYSIS_STEPS = {'get_data_summary', 'find_black_spots',
                           'get_comprehensive_summary', 'generate_final_report'}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_agent(session, user_text: str, auto: bool = False) -> Iterator[str]:
    """执行一轮 Agent 交互，逐个 yield SSE 帧。

    流式实现要点：每一轮都用 stream=True，因为**事先不知道模型这一轮
    是返回工具调用还是最终回答**。工具调用的 arguments 在流里是分片到达的，
    必须按 index 累积拼接后才能解析。
    """
    run_revision = session.analysis_revision
    if auto:
        session.analysis_done = False
        session.analysis_status = 'running'
        session.analysis_steps = {}
        session.tool_evidence = {}
        error = measurement_error(session.df)
        if error:
            session.analysis_status = 'failed'
            yield _sse('error', {'message': error, 'status': 'failed'})
            return
    api_key = resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        if auto:
            session.analysis_status = 'failed'
        yield _sse("error", {"message": "未配置 DeepSeek API Key"})
        return

    client = None

    # 自动复跑从当前数据重新取证，避免历史工具输出或旧报告干扰本轮结论。
    msgs = [] if auto else list(session.full_messages)
    system_prompt = SYSTEM_PROMPT_AUTO if auto else SYSTEM_PROMPT_CHAT
    system_prompt += '\n当前数据身份（服务器核对）：' + json.dumps(session.data_identity, ensure_ascii=False)
    if session.analysis_status == 'stale':
        system_prompt += (' 当前检测或视频已更新，历史报告和工具记录可能过期。'
                          '请先调用当前数据摘要、检测及综合摘要工具，勿直接沿用历史统计或视觉结论。')
    # 每次请求按模式重建 system 消息，避免继承上一轮的自动分析 SOP。
    msgs = [m for m in msgs if not (isinstance(m, dict) and m.get('role') == 'system')]
    msgs.insert(0, {"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_text})

    session.messages.append({"role": "user", "content": user_text})

    final_text = ""
    turn = 0

    try:
        client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=TIMEOUT)
        tool_errors = 0
        while turn < MAX_TURNS:
            turn += 1
            yield _sse("turn", {"turn": turn})

            stream = client.chat.completions.create(
                model=MODEL_NAME, messages=msgs, tools=TOOLS,
                tool_choice="auto", temperature=0.1, stream=True,
            )

            content_parts: list[str] = []
            # index -> {id, name, arguments}；分片按 index 归并
            calls: dict[int, dict] = {}
            finish_reason = None

            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if getattr(choice, 'finish_reason', None) is not None:
                    finish_reason = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue

                if delta.content:
                    content_parts.append(delta.content)
                    yield _sse("delta", {"text": delta.content})

                for tc in (delta.tool_calls or []):
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function.arguments:
                            slot["arguments"] += tc.function.arguments

            expected_finish = 'tool_calls' if calls else 'stop'
            if finish_reason != expected_finish:
                raise RuntimeError(f'模型输出未完整结束（{finish_reason or "缺少结束标记"}），请重试；本轮不标记完成。')
            if run_revision != session.analysis_revision:
                raise RuntimeError('监测数据版本已变化，已停止旧分析回合，请重新分析。')
            content = "".join(content_parts)

            # ---- 没有工具调用 → 这就是最终回答 ----
            if not calls:
                final_text = content
                msgs.append({"role": "assistant", "content": content})
                break

            # ---- 有工具调用 → 记录 assistant 消息，逐个执行 ----
            assistant_msg = {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"] or f"call_{i}",
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                    }
                    for i, c in sorted(calls.items())
                ],
            }
            msgs.append(assistant_msg)

            for call in assistant_msg["tool_calls"]:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = None
                # 工具参数可能很大（如 summary），事件里只带键名
                yield _sse("tool", {"name": name, "args": list(args) if isinstance(args, dict) else []})

                result, is_err = execute_tool(name, args, session, api_key)
                tool_errors += int(is_err)
                if auto:
                    outcome = json.loads(result)
                    session.analysis_steps[name] = ('failed' if is_err else
                        'skipped' if outcome.get('skipped') else 'succeeded')
                yield _sse("tool_done", {"name": name, "ok": not is_err,
                                         "chars": len(result)})

                msgs.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": truncate(name, result),
                })

        else:
            # 达到轮次上限：强制收口，不再允许调用工具
            yield _sse("tool", {"name": "(达到最大轮次，收口中)", "args": []})
            msgs.append({"role": "user",
                         "content": "已达到最大分析轮次。请基于已完成的分析给出最终结论，禁止再调用工具。"})
            resp = client.chat.completions.create(
                model=MODEL_NAME, messages=msgs, temperature=0.1, stream=True)
            finish_reason = None
            for chunk in resp:
                if chunk.choices and getattr(chunk.choices[0], 'finish_reason', None) is not None:
                    finish_reason = chunk.choices[0].finish_reason
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    final_text += chunk.choices[0].delta.content
                    yield _sse("delta", {"text": chunk.choices[0].delta.content})
            if finish_reason != 'stop':
                raise RuntimeError(f'收口回答未完整结束（{finish_reason or "缺少结束标记"}），请重试。')
            msgs.append({'role': 'assistant', 'content': final_text})

        session.full_messages = msgs
        if final_text:
            session.messages.append({"role": "assistant", "content": final_text})
        if auto:
            required = REQUIRED_ANALYSIS_STEPS | ({'analyze_video', 'cross_validate_video'} if session.video_frames else set())
            complete = (bool(final_text) and run_revision == session.analysis_revision
                        and session.black_spots is not None
                        and all(session.analysis_steps.get(name) == 'succeeded'
                                for name in required)
                        and 'failed' not in session.analysis_steps.values())
            session.analysis_done = complete
            session.analysis_status = ('succeeded' if complete else 'partial'
                if final_text and 'succeeded' in session.analysis_steps.values() else 'failed')

        yield _sse("done", {"turns": turn, "tool_errors": tool_errors,
                           "status": session.analysis_status if auto else 'succeeded'})

    except Exception as e:                       # noqa: BLE001
        if auto:
            session.analysis_done = False
            session.analysis_status = 'failed'
        logger.exception("Agent 运行失败")
        msg = str(e)
        if "api_key" in msg.lower() or "401" in msg or "auth" in msg.lower():
            msg = "DeepSeek API Key 无效或未配置"
        elif "timeout" in msg.lower() or "connect" in msg.lower():
            msg = "无法连接 DeepSeek API，请检查网络"
        yield _sse("error", {"message": msg[:300]})
    finally:
        if auto and session.analysis_status == 'running':
            session.analysis_status = 'cancelled'
            session.analysis_done = False
        if client is not None:
            client.close()
