"""FastAPI 依赖项。"""

from __future__ import annotations

from fastapi import Request, Depends, HTTPException

from .session import Session, STORE


def get_session(request: Request) -> Session:
    """取当前请求的会话。

    会话 id 由 main.py 的 session_middleware 从请求头解析并写入
    `request.state.session_id` —— 那个中间件同时负责把它回写到响应头，
    前端才能把它记住并在后续请求带上。

    **不要在这里直接读请求头并 STORE.get()**：那样当中间件因故未生效时，
    每个请求都会静默新建一个会话，表现为"上传成功但状态永远是空的"。
    """
    sid = getattr(request.state, "session_id", None)
    if sid is None:
        # 兜底：中间件未生效（例如单独构造 Request 的单元测试）
        sid = request.headers.get("X-Session-Id")
    return STORE.get(sid)


def get_exclusive_session(session: Session = Depends(get_session)):
    """同一会话的分析与数据替换互斥，流式响应结束后释放。"""
    if not session.operation_lock.acquire(blocking=False):
        raise HTTPException(409, "当前会话正在处理任务，请完成后再重试",headers={'X-Session-Conflict':'busy'})
    try:
        yield session
    finally:
        session.operation_lock.release()
