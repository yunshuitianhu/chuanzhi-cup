"""FastAPI 应用入口。

启动：
    uvicorn backend.main:app --reload --port 8000

接口文档：http://localhost:8000/docs
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import PROJECT_ROOT  # noqa: F401  —— 导入即完成 sys.path 与 .env 初始化
from .routers import agent, analysis, charts, data, system

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

app = FastAPI(
    title="水质监测与污染溯源 API",
    description="把既有 Python 分析内核包装为 HTTP 服务，供 Vue 前端调用。",
    version="0.1.0",
)

# 开发期前端跑在 5173，生产由后端或 Nginx 直接托管静态文件。
# 这里放开 localhost 便于联调；部署时按实际域名收紧。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:4173", "http://127.0.0.1:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Session-Id"],
)

app.include_router(system.router, prefix="/api")
app.include_router(data.router, prefix="/api")
app.include_router(analysis.router, prefix="/api")
app.include_router(charts.router, prefix="/api")
app.include_router(agent.router, prefix="/api")


@app.get("/")
def root() -> dict:
    return {
        "service": "水质监测与污染溯源 API",
        "docs": "/docs",
        "endpoints": [r.path for r in app.routes if getattr(r, "path", "").startswith("/api")],
    }
