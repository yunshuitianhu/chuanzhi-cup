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

from .tools import (
    SYSTEM_PROMPT_AUTO, SYSTEM_PROMPT_CHAT, TOOLS,
    execute_tool, truncate,
)

logger = logging.getLogger(__name__)

MODEL_NAME = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"
MAX_TURNS = 15
TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def run_agent(session, user_text: str, auto: bool = False) -> Iterator[str]:
    """执行一轮 Agent 交互，逐个 yield SSE 帧。

    流式实现要点：每一轮都用 stream=True，因为**事先不知道模型这一轮
    是返回工具调用还是最终回答**。工具调用的 arguments 在流里是分片到达的，
    必须按 index 累积拼接后才能解析。
    """
    api_key = resolve_api_key("DEEPSEEK_API_KEY")
    if not api_key:
        yield _sse("error", {"message": "未配置 DeepSeek API Key"})
        return

    client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=TIMEOUT)

    msgs = list(session.full_messages)
    system_prompt = SYSTEM_PROMPT_AUTO if auto else SYSTEM_PROMPT_CHAT
    if not msgs or (isinstance(msgs[0], dict) and msgs[0].get("role") != "system"):
        msgs.insert(0, {"role": "system", "content": system_prompt})
    msgs.append({"role": "user", "content": user_text})

    session.messages.append({"role": "user", "content": user_text})

    final_text = ""
    turn = 0

    try:
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

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
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
                    args = {}
                # 工具参数可能很大（如 summary），事件里只带键名
                yield _sse("tool", {"name": name, "args": list(args.keys())})

                result, is_err = execute_tool(name, args, session, api_key)
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
            for chunk in resp:
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    final_text += chunk.choices[0].delta.content
                    yield _sse("delta", {"text": chunk.choices[0].delta.content})

        session.full_messages = msgs
        if final_text:
            session.messages.append({"role": "assistant", "content": final_text})
        if auto:
            session.analysis_done = True

        yield _sse("done", {"turns": turn})

    except Exception as e:                       # noqa: BLE001
        logger.exception("Agent 运行失败")
        msg = str(e)
        if "api_key" in msg.lower() or "401" in msg or "auth" in msg.lower():
            msg = "DeepSeek API Key 无效或未配置"
        elif "timeout" in msg.lower() or "connect" in msg.lower():
            msg = "无法连接 DeepSeek API，请检查网络"
        yield _sse("error", {"message": msg[:300]})
