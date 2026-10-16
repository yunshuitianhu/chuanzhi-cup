"""Agent 路由 —— SSE 流式对话。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..agent.loop import run_agent
from ..deps import get_session, get_exclusive_session
from ..session import Session

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/chat")
def chat(payload: dict, session: Session = Depends(get_exclusive_session)):
    """与 Agent 对话，SSE 流式返回。

    Body:
        message: 用户输入
        auto:    是否为「一键自动分析」（使用带 SOP 的 system prompt）
    """
    message = str(payload.get("message", "") or "").strip()
    auto = bool(payload.get("auto", False))
    if not message:
        message = "请开始自动分析。" if auto else "你好"

    return StreamingResponse(
        run_agent(session, message, auto=auto),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",     # 关掉 nginx 缓冲，否则流会被攒起来
            "X-Session-Id": session.id,
        },
    )


@router.get('/history')
def history(session: Session = Depends(get_session)) -> dict:
    return {'messages': session.messages, 'analysis_revision': session.analysis_revision}
