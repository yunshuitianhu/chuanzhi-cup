"""FastAPI 应用入口。

启动：
    uvicorn backend.main:app --reload --port 8000

接口文档：http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import math

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import PROJECT_ROOT  # noqa: F401  —— 导入即完成 sys.path 与 .env 初始化
from .routers import agent, analysis, charts, data, rag, river, stations, system, trace, projects, maps, investigations, video
from .session import STORE
from .routers import side

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="水质监测与污染溯源 API",
    description="把既有 Python 分析内核包装为 HTTP 服务，供 Vue 前端调用。",
    version="0.1.0",
)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """NaN/Infinity等非法输入也必须返回有效JSON，不能在构造422时再次报500。"""
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key:finite(item) for key,item in value.items()}
        if isinstance(value, list):
            return [finite(item) for item in value]
        return value
    return JSONResponse(status_code=422, content={'detail':finite(jsonable_encoder(exc.errors()))})


# ── 会话中间件 ────────────────────────────────────────────────────────────
# 必须放在 CORSMiddleware **之前**注册：Starlette 中最后注册的中间件在最外层，
# 而 CORS 需要包在最外面才能保证它的响应头不被后续中间件干扰。
@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """为每个请求解析/创建会话，并在响应头回传会话 id。

    为什么必须回写响应头
    --------------------
    HTTP 是无状态的，会话靠前端在 `X-Session-Id` 请求头里带回来。
    如果后端不告诉前端"这次用的是哪个会话"，前端就永远拿不到 id，
    于是**每个请求都新建一个会话** —— 表现为「上传成功，但状态始终空的」。
    这是本模块此前真实发生过的缺陷，不是假想。

    会话在中间件层解析（而非依赖里），保证**所有**响应（含 SSE、错误响应）
    都带上这个头，前端只需在一处读取。
    """
    # 底图是公共背景，不读取分析数据；图片请求不应不断创建空会话。
    if request.url.path.startswith("/api/maps/"):
        return await call_next(request)
    session = STORE.get(request.headers.get("X-Session-Id"))
    request.state.session_id = session.id
    response = await call_next(request)
    response.headers["X-Session-Id"] = session.id
    return response


# CORS：开发期前端跑在 5173。注：Vite 已配 /api 代理，联调时是同源请求，
# 这一层主要是为了直连后端的场景（如直接用 curl 或其它端口的调试页）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://[::1]:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id", "X-Session-Conflict"],
)

app.include_router(system.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(stations.router, prefix="/api")
app.include_router(trace.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(river.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(maps.router, prefix="/api")
app.include_router(investigations.router, prefix="/api")
app.include_router(video.router, prefix="/api")
app.include_router(side.router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "service": "水质监测与污染溯源 API",
        "docs": "/docs",
        "active_sessions": STORE.count,
        "endpoints": [r.path for r in app.routes if getattr(r, "path", "").startswith("/api")],
    }
