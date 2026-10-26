"""本地交付入口：同一服务提供启动时固定的Vue产物与既有API。"""

from hashlib import sha256
import mimetypes
from pathlib import Path
import re

from fastapi import FastAPI, Request
from fastapi.responses import Response

from . import PROJECT_ROOT
from .main import app as api_app


def _asset_snapshot(dist):
    """只读启动产物；运行中的构建不会删除已打开页面所需的懒加载资源。"""
    index = dist / 'index.html'
    asset_root = (dist / 'assets').resolve()
    if not index.is_file() or not asset_root.is_dir():
        raise RuntimeError('缺少前端构建产物，请在frontend目录执行 npm ci 和 npm run build。')
    try:
        html = index.read_bytes()
        assets = {}
        for path in asset_root.rglob('*'):
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(asset_root):
                raise ValueError('资源路径超出构建目录')
            name = path.relative_to(asset_root).as_posix()
            content = path.read_bytes()
            assets[name] = (content, mimetypes.guess_type(name)[0] or 'application/octet-stream',
                '"' + sha256(content).hexdigest() + '"')
        links = re.findall(r"(?:src|href)=[\"'](/assets/[^\"']+)[\"']", html.decode('utf-8'))
        if any(link[len('/assets/'):] not in assets for link in links) or index.read_bytes() != html:
            raise ValueError('构建资源正在更新或不完整')
    except (OSError, UnicodeError, ValueError) as exc:
        raise RuntimeError('前端构建正在变化或资源不完整，请等构建完成后再启动服务。') from exc
    return html, assets


def create_app(frontend_dir: Path | None = None) -> FastAPI:
    """内存快照只包含index/assets；不公开源码、配置、上传文件或项目数据库。"""
    dist = Path(frontend_dir or Path(PROJECT_ROOT) / 'frontend' / 'dist').resolve()
    html, assets = _asset_snapshot(dist)
    web = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    async def page(request: Request):
        return Response(b'' if request.method == 'HEAD' else html, media_type='text/html',
            headers={'Cache-Control': 'no-cache', 'Content-Length': str(len(html))})

    async def asset(path: str, request: Request):
        item = assets.get(path)
        if item is None:
            return Response(status_code=404)
        content, media_type, etag = item
        headers = {'Cache-Control': 'public, max-age=31536000, immutable', 'ETag': etag}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        headers['Content-Length'] = str(len(content))
        return Response(b'' if request.method == 'HEAD' else content, media_type=media_type, headers=headers)

    for path in ('/', '/chat', '/charts', '/river', '/tech'):
        web.add_api_route(path, page, methods=['GET', 'HEAD'], include_in_schema=False)
    web.add_api_route('/assets/{path:path}', asset, methods=['GET', 'HEAD'], include_in_schema=False)
    web.mount('/', api_app)
    return web
