"""内置仿真示例的清单校验、白名单读取与公开元数据。

运行时只信任 `data/samples/manifest.json` 中的 ID、文件名和哈希；客户端不能
传入文件名、路径或 URL。清单损坏、重复 ID、哈希不符或资源缺失统一作为
服务资源错误返回，不静默变成空列表。
"""
from __future__ import annotations

from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = PROJECT_ROOT / 'data' / 'samples'
MANIFEST_PATH = SAMPLE_DIR / 'manifest.json'
MAX_SAMPLE_BYTES = 20 * 1024 * 1024
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
SHA_RE = re.compile(r'^[0-9a-f]{64}$')

TOP_KEYS = {'manifest_version', 'data_kind', 'notice', 'samples'}
SAMPLE_KEYS = {'id', 'title', 'description', 'file', 'data_kind', 'version', 'sha256',
               'size_bytes', 'rows', 'columns', 'time_range', 'time_zone',
               'coordinate_system', 'source_reference', 'has_video', 'notice',
               'field_units', 'missing_rule'}
REQUIRED = {'id', 'title', 'description', 'file', 'data_kind', 'version', 'sha256',
            'size_bytes', 'rows', 'columns', 'time_range', 'coordinate_system',
            'source_reference', 'has_video'}
PUBLIC_KEYS = ('id', 'title', 'description', 'data_kind', 'version', 'rows', 'columns',
               'time_range', 'time_zone', 'coordinate_system', 'source_reference',
               'has_video', 'notice', 'size_bytes', 'field_units', 'missing_rule')


class SampleCatalogError(Exception):
    """示例目录不可用；调用方应作为服务资源错误处理。"""


class SampleNotFound(SampleCatalogError):
    """请求的示例 ID 不在清单中。"""


class SampleResourceError(SampleCatalogError):
    """清单或示例文件损坏、缺失、哈希不符。"""


def _fail(message: str):
    raise SampleResourceError(message)


def _text(value, key, limit, *, required=True):
    if value is None and not required:
        return ''
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        _fail(f'示例字段 {key} 无效')
    return value.strip()


def _validate_sample(item: Any, seen: set[str]) -> dict:
    if not isinstance(item, dict):
        _fail('示例条目不是对象')
    if not REQUIRED <= set(item) or set(item) - SAMPLE_KEYS:
        missing = sorted(REQUIRED - set(item))
        extra = sorted(set(item) - SAMPLE_KEYS)
        _fail('示例条目的字段不完整或包含未知字段'
              + (f'，缺少: {",".join(missing)}' if missing else '')
              + (f'，未知: {",".join(extra)}' if extra else ''))
    ident = _text(item['id'], 'id', 32)
    if not ID_RE.fullmatch(ident):
        _fail('示例 ID 无效')
    if ident in seen:
        _fail('示例 ID 重复')
    seen.add(ident)
    if item['data_kind'] != 'synthetic':
        _fail(f'示例 {ident} 的 data_kind 必须为 synthetic')
    if item['has_video'] is not False:
        _fail(f'示例 {ident} 当前不支持视频，has_video 必须为 false')
    filename = _text(item['file'], 'file', 200)
    if (filename != Path(filename).name or '/' in filename or '\\' in filename
            or not filename.lower().endswith('.csv') or '仿真' not in filename):
        _fail(f'示例 {ident} 的文件名必须是含“仿真”的普通 CSV')
    directory = SAMPLE_DIR.resolve()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or path.is_symlink() or not path.is_file():
        _fail(f'示例 {ident} 的资源文件不存在或路径无效')
    size = item['size_bytes']
    if type(size) is not int or not 0 < size <= MAX_SAMPLE_BYTES:
        _fail(f'示例 {ident} 的 size_bytes 无效')
    digest = item['sha256']
    if not isinstance(digest, str) or not SHA_RE.fullmatch(digest):
        _fail(f'示例 {ident} 的 sha256 无效')
    actual = path.read_bytes()
    if len(actual) != size:
        _fail(f'示例 {ident} 的实际大小与清单不一致')
    if sha256(actual).hexdigest() != digest:
        _fail(f'示例 {ident} 的内容哈希与清单不一致')
    rows = item['rows']
    if type(rows) is not int or rows <= 0:
        _fail(f'示例 {ident} 的 rows 无效')
    columns = item['columns']
    if (not isinstance(columns, list) or not columns
            or any(not isinstance(column, str) or not column or len(column) > 100 for column in columns)):
        _fail(f'示例 {ident} 的 columns 无效')
    if not isinstance(item['time_range'], list) or len(item['time_range']) != 2 \
            or any(not isinstance(value, str) or not value for value in item['time_range']):
        _fail(f'示例 {ident} 的 time_range 无效')
    for key, limit in (('title', 200), ('description', 1000), ('version', 100),
                       ('time_zone', 200), ('coordinate_system', 300),
                       ('source_reference', 300), ('notice', 2000), ('missing_rule', 1000)):
        _text(item.get(key), key, limit, required=key in REQUIRED)
    source = item['source_reference']
    if re.match(r'^(?:[A-Za-z]:|[\\/])', source):
        _fail(f'示例 {ident} 的 source_reference 不能是绝对路径')
    units = item.get('field_units', {})
    if units is not None and (not isinstance(units, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or len(v) > 30 for k, v in units.items())):
        _fail(f'示例 {ident} 的 field_units 无效')
    return item


def load_manifest() -> dict:
    try:
        raw = MANIFEST_PATH.read_bytes()
        payload = json.loads(raw)
    except (OSError, ValueError, TypeError) as exc:
        raise SampleResourceError('内置示例清单不可用；请检查 data/samples/manifest.json') from exc
    if not isinstance(payload, dict) or set(payload) != TOP_KEYS:
        _fail('内置示例清单字段不完整或包含未知字段')
    if payload.get('manifest_version') != 1 or payload.get('data_kind') != 'synthetic':
        _fail('内置示例清单版本或数据性质无效')
    _text(payload.get('notice'), 'notice', 3000)
    samples = payload.get('samples')
    if not isinstance(samples, list) or not samples:
        _fail('内置示例清单没有示例')
    seen = set()
    validated = [_validate_sample(item, seen) for item in samples]
    return {'manifest_version': 1, 'data_kind': 'synthetic',
            'notice': payload['notice'], 'samples': validated}


def _public(item: dict) -> dict:
    return {key: item[key] for key in PUBLIC_KEYS if key in item}


def list_samples() -> dict:
    manifest = load_manifest()
    return {'manifest_version': manifest['manifest_version'],
            'data_kind': manifest['data_kind'], 'notice': manifest['notice'],
            'samples': [_public(item) for item in manifest['samples']]}


def get_sample(sample_id: str) -> tuple[dict, bytes]:
    if not isinstance(sample_id, str) or not ID_RE.fullmatch(sample_id):
        raise SampleNotFound('没有该示例')
    manifest = load_manifest()
    item = next((entry for entry in manifest['samples'] if entry['id'] == sample_id), None)
    if item is None:
        raise SampleNotFound('没有该示例')
    path = (SAMPLE_DIR.resolve() / item['file']).resolve()
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SampleResourceError('示例文件无法读取；请检查 data/samples 资源') from exc
    if len(raw) != item['size_bytes'] or sha256(raw).hexdigest() != item['sha256']:
        raise SampleResourceError('示例文件大小或哈希校验失败；请重新准备示例数据')
    return item, raw


def public_sample(item: dict) -> dict:
    return _public(item)
