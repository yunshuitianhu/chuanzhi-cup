"""本地交付入口：同一服务提供构建后的Vue页面与既有API。"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import PROJECT_ROOT
from .main import app as api_app


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    """只公开前端构建目录；未知API路径保持JSON 404，不回退成网页。"""
    dist = Path(frontend_dir or Path(PROJECT_ROOT) / 'frontend' / 'dist').resolve()
    if not (dist / 'index.html').is_file() or not (dist / 'assets').is_dir():
        raise RuntimeError('缺少前端构建产物，请在frontend目录执行 npm ci 和 npm run build。')
    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def page():
        return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache'})

    # 显式列出Vue路由，避免把拼错的API、密钥路径或缺失资源当成首页。
    for path in ('/', '/chat', '/charts', '/river', '/tech'):
        web.add_api_route(path, page, methods=['GET', 'HEAD'], include_in_schema=False)
    web.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')
    web.mount('/', api_app)
    return web
