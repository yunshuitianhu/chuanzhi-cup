"""从已提交源码和已构建前端生成交付包；不包含本机密钥、项目或向量缓存。"""

import argparse
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.serve_web import check_frontend

RUNTIME_PREFIXES = ('.rag_index/', 'work/', 'data/projects/', '仿真数据/')
PRIVATE_NAMES = {'.env', '.mcp.json', 'secrets.toml'}


def source_allowed(name: str) -> bool:
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('源码归档包含不安全路径')
    if path.name in PRIVATE_NAMES or path.name.startswith('.env.') and path.name != '.env.example':
        raise ValueError('源码归档包含本机配置文件，请先修复Git跟踪状态')
    return not name.startswith(RUNTIME_PREFIXES)


def check_sample_entries(entries: dict) -> None:
    """交付包的示例清单/文件/哈希必须与源码一致，不能只带空目录。"""
    manifest_name = 'data/samples/manifest.json'
    readme_name = 'data/samples/README.md'
    if manifest_name not in entries or readme_name not in entries:
        raise ValueError('交付包缺少 data/samples 清单或说明')
    try:
        payload = json.loads(entries[manifest_name])
    except (ValueError, TypeError) as exc:
        raise ValueError('交付包示例清单不是有效 JSON') from exc
    if (not isinstance(payload, dict) or payload.get('manifest_version') != 1
            or payload.get('data_kind') != 'synthetic'
            or not isinstance(payload.get('samples'), list)):
        raise ValueError('交付包示例清单字段无效')
    seen = set()
    for item in payload['samples']:
        if not isinstance(item, dict):
            raise ValueError('交付包示例条目无效')
        ident = item.get('id')
        filename = item.get('file')
        if (not isinstance(ident, str) or not ident or ident in seen
                or not isinstance(filename, str) or '仿真' not in filename
                or not filename.endswith('.csv') or '/' in filename or '\\' in filename):
            raise ValueError('交付包示例编号或文件名无效')
        seen.add(ident)
        name = 'data/samples/' + filename
        raw = entries.get(name)
        if not isinstance(raw, bytes):
            raise ValueError(f'交付包缺少示例文件 {filename}')
        if len(raw) != item.get('size_bytes') or sha256(raw).hexdigest() != item.get('sha256'):
            raise ValueError(f'交付包示例文件哈希或大小不一致: {filename}')
    if not seen:
        raise ValueError('交付包示例清单为空')


def write_bundle(source: bytes, dist: Path, output: Path, revision: str) -> dict:
    check_frontend(dist)
    entries = {}
    with zipfile.ZipFile(BytesIO(source)) as archive:
        for info in archive.infolist():
            if not source_allowed(info.filename) or info.is_dir():
                continue
            if stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('源码归档含符号链接，请人工检查交付内容')
            entries[info.filename] = archive.read(info)
    check_sample_entries(entries)
    manifest = json.loads((dist / '.vite' / 'manifest.json').read_text(encoding='utf-8'))
    assets = {'index.html', '.vite/manifest.json'}
    for entry in manifest.values():
        assets.update([entry['file'], *entry.get('css', []), *entry.get('assets', [])])
    for name in assets:
        entries['frontend/dist/' + name] = (dist / name).read_bytes()
    record = dict(format_version=1, revision=revision, frontend='included built assets',
                  dependency_install='requirements-lock.txt; transitive dependencies and OS need separate validation',
                  rag='index/model cache not included; prepare separately',
                  files={name:sha256(raw).hexdigest() for name,raw in sorted(entries.items())})
    entries['delivery-manifest.json'] = (json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    # 内存内先生成成功，再排他创建目标，避免覆盖既有交付包。
    buffer = BytesIO()
    with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(entries.items()):
            archive.writestr(name,raw)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('xb') as target:
        target.write(buffer.getvalue())
    return dict(revision=revision,files=len(entries),sha256=sha256(buffer.getvalue()).hexdigest())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.suffix.lower() != '.zip':
        parser.error('--output必须是新的.zip文件路径')
    changed=subprocess.check_output(['git','diff','--name-only','-z','HEAD'],cwd=ROOT).decode('utf-8').split('\0')
    if any(name and not name.startswith(RUNTIME_PREFIXES) for name in changed):
        parser.error('存在未提交源码修改，请验证并提交后再打包；运行缓存不纳入交付')
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    source=subprocess.check_output(['git','archive','--format=zip','HEAD'],cwd=ROOT)
    result=write_bundle(source,ROOT/'frontend/dist',args.output,revision)
    print(json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    main()
