"""统一本地启动与预检；不自动安装依赖、不修改密钥、不终止其它服务。"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import socket
import sys
import tempfile
import webbrowser
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
HOST = '127.0.0.1'
DEFAULT_PORT = 5174
AUTO_PORT_COUNT = 20
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check_frontend(dist: Path) -> None:
    index = dist / 'index.html'
    if not index.is_file() or not (dist / 'assets').is_dir():
        raise RuntimeError('缺少前端构建：请在frontend目录执行 npm ci，再执行 npm run build。')
    assets = re.findall(r'''(?:src|href)=["'](/assets/[^"']+)["']''', index.read_text(encoding='utf-8'))
    if not assets:
        raise RuntimeError('前端index.html没有构建资源引用，请重新执行 npm run build。')
    for asset in assets:
        target = (dist / asset.lstrip('/')).resolve()
        if not target.is_relative_to(dist.resolve()) or not target.is_file():
            raise RuntimeError('前端构建资源不完整，请重新执行 npm run build。')
    try:
        manifest = json.loads((dist / '.vite' / 'manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError()
        for entry in manifest.values():
            for name in [entry['file'], *entry.get('css', []), *entry.get('assets', [])]:
                target = (dist / name).resolve()
                if not target.is_relative_to(dist.resolve()) or not target.is_file():
                    raise ValueError()
            if any(key not in manifest for key in [*entry.get('imports', []), *entry.get('dynamicImports', [])]):
                raise ValueError()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise RuntimeError('前端构建清单缺失或资源不完整，请重新执行 npm run build。') from exc


def bind_port(port: int, *, auto_port: bool = False) -> socket.socket:
    last_port = min(65535, port + AUTO_PORT_COUNT - 1) if auto_port else port
    for candidate in range(port, last_port + 1):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == 'win32':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((HOST, candidate))
        except OSError as exc:
            sock.close()
            if candidate == last_port:
                label = f'{port}–{last_port}' if auto_port else str(port)
                raise RuntimeError(f'端口{label}不可绑定或已占用。请使用 --port 指定其它端口。') from exc
        else:
            return sock
    raise ValueError('无可选端口')


def preflight():
    if sys.version_info < (3, 11):
        raise RuntimeError('请使用Python 3.11或更新版本；本机已在Python 3.14.0完成回归和交付副本验收。')
    check_frontend(ROOT / 'frontend' / 'dist')
    try:
        importlib.import_module('uvicorn')
        from backend.web import create_app
        from backend.projects import project_home
        from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
    except ImportError as exc:
        raise RuntimeError('运行依赖缺失或不兼容，请用当前Python执行 python -m pip install -r requirements-lock.txt。'
                           f' 缺失模块：{getattr(exc, "name", None) or "请检查依赖版本"}') from exc
    home = project_home()
    try:
        home.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=home) as probe:
            probe.write(b'wq-startup-check')
    except OSError as exc:
        raise RuntimeError('项目目录不可写，请将WQ_PROJECT_HOME设置到有写权限的目录。') from exc
    print(f'[OK] Python {sys.version.split()[0]}；前端构建、API导入和项目目录检查通过。', flush=True)
    print(f'[INFO] 项目目录：{home}', flush=True)
    for key, label in (('DEEPSEEK_API_KEY', '智能体/报告'), ('AMAP_KEY', '高德查询'), ('CLOUD_TOKEN', '星图影像')):
        print(f'[{"OK" if resolve_api_key(key) else "WARN"}] {label}：'
              f'{"已配置" if resolve_api_key(key) else "未配置，相关能力不可用或降级"}', flush=True)
    from water_quality_mcp.src.water_quality_mcp.rag_engine import configured_index_dir, optional_dependencies
    missing = [name for name, found in optional_dependencies().items() if not found]
    print(f'[INFO] RAG索引目录：{configured_index_dir()}', flush=True)
    if missing:
        print('[WARN] 知识库依赖缺失：' + '、'.join(missing) + '；请确认当前Python安装了完整依赖。', flush=True)
    print('[INFO] RAG模型按需加载；索引存在和依赖探测不代表真实检索已通过。', flush=True)
    return create_app()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help=f'本地网页和API共用端口，默认{DEFAULT_PORT}')
    parser.add_argument('--auto-port', action='store_true', help='未指定端口时，默认端口占用则继续查找空闲端口')
    parser.add_argument('--open-browser', action='store_true', help='服务启动后打开默认浏览器')
    parser.add_argument('--check', action='store_true', help='只检查，不启动服务')
    args = parser.parse_args(argv)
    port = DEFAULT_PORT if args.port is None else args.port
    if not 1 <= port <= 65535:
        parser.error('--port必须为1到65535之间的整数')
    try:
        app = preflight()
        listener = bind_port(port, auto_port=args.auto_port and args.port is None)
    except Exception as exc:
        print(f'[ERROR] {exc}', file=sys.stderr, flush=True)
        return 1
    with listener:
        actual_port = listener.getsockname()[1]
        if actual_port != port:
            print(f'[INFO] 端口{port}已占用或不可用，已选用{actual_port}。', flush=True)
        print(f'[OK] 本机端口 {actual_port} 检查通过。', flush=True)
        if args.check:
            return 0
        import uvicorn

        url = f'http://{HOST}:{actual_port}/'

        class LocalServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    print(f'[START] {url}', flush=True)
                    print('[INFO] 请保持此终端运行；关闭终端将停止服务。', flush=True)
                    if args.open_browser:
                        Thread(target=webbrowser.open, args=(url,), daemon=True).start()

        server = LocalServer(uvicorn.Config(app, host=HOST, port=actual_port, workers=1))
        server.run(sockets=[listener])
        if not server.started:
            return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
