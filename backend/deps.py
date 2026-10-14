"""FastAPI 依赖项。"""

from __future__ import annotations

from fastapi import Header

from .session import Session, STORE


def get_session(x_session_id: str | None = Header(default=None)) -> Session:
    """按 X-Session-Id 请求头取会话；不存在则新建。

    前端在首次响应里拿到 session id，之后每个请求带上即可。
    """
    return STORE.get(x_session_id)
