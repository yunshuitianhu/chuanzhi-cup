"""统一本地启动与预检；不自动安装依赖、不修改密钥、不终止其它服务。"""

from __future__ import annotations

import argparse
import importlib
import json
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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


def check_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(('127.0.0.1', port))
        except OSError as exc:
            raise RuntimeError(f'端口{port}不可绑定或已占用。请使用 --port 其它端口；原服务不会被终止。') from exc


def preflight(port: int):
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
    check_port(port)
    print(f'[OK] Python {sys.version.split()[0]}；前端构建、API导入、项目目录和端口检查通过。', flush=True)
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
    parser.add_argument('--port', type=int, default=5174, help='本地网页和API共用端口，默认5174')
    parser.add_argument('--check', action='store_true', help='只检查，不启动服务')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('--port必须为1到65535之间的整数')
    try:
        app = preflight(args.port)
    except Exception as exc:
        print(f'[ERROR] {exc}', file=sys.stderr, flush=True)
        return 1
    if args.check:
        return 0
    print(f'[START] http://127.0.0.1:{args.port}/chat', flush=True)
    print('[INFO] 请保持此终端运行；关闭终端将停止服务。仅监听本机，单worker。', flush=True)
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=args.port, workers=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
