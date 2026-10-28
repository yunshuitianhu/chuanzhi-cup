"""识别验证元数据中的内容哈希；同值出现在正文/配置中仍扫描。

只豁免明确声明的摘要字段：示例清单的 ``sha256``、验证记录顶层的
``*_sha256``、图片/视频证据对象里的 ``sha256``/``asset_revision``/
``video_revision``，以及 32 位 ``asset_id``。模型正文里的同值（例如
``answer.sha256``）不豁免，真实密钥仍会命中。
"""
import json
import re
from pathlib import PurePosixPath

HASH64 = re.compile(r'[0-9a-f]{64}\Z')
HASH32 = re.compile(r'[0-9a-f]{32}\Z')
VISION_KEYS = {'asset_revision', 'video_revision', 'asset_id'}
TEXT_BODY_KEYS = {'answer', 'content', 'text', 'response', 'message'}
# 已独立核实来源的内容哈希（data_version、交付包摘要），不是凭证。
# 仅按完整值豁免；新增项必须能说明来源，且 .env 真实值仍由第 2 项检查。
KNOWN_TEXT_HASHES = {
    'c5acfb7e952f3bdb0e4c9c01a57b2a12d9fca1f3d53a961fbceaf5139dcb7c1b':
        'response_quality_20261027 回答中引用的 data_version',
    '7577e57813350d10281144d9c1751e37d2e92a16c353364681b334192a3602e2':
        'work/delivery_20261028_r2.zip 的 SHA-256；2026-10-28 对实际 ZIP 重新计算核实',
}


def is_known_text_hash(value: str) -> bool:
    return value in KNOWN_TEXT_HASHES


def _is_metadata(relative: str) -> bool:
    return (relative == 'data/samples/manifest.json'
            or relative.startswith('docs/validation/')
            or relative.startswith('evaluation/') and '/results/' in relative)


def _pair_pattern(width: int) -> re.Pattern:
    # 跳过字符串内部转义的JSON片段；返回值位置按bytes计，兼容中文前缀。
    return re.compile(rb'(?<!\\)"((?:[^"\\]|\\.)*)"\s*:\s*"([0-9a-f]{' +
                      str(width).encode('ascii') + rb'})"')


def metadata_hash_spans(relative: str, raw: bytes, width: int = 64) -> set[tuple[int, int]]:
    """返回可豁免的摘要值在原始 bytes 中的位置；默认只处理 64 位 SHA-256。"""
    if width not in (32, 64) or not _is_metadata(relative):
        return set()
    digest = HASH64 if width == 64 else HASH32
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return set()
    pairs = set()
    is_manifest = relative == 'data/samples/manifest.json'
    is_validation = relative.startswith('docs/validation/') or (
        relative.startswith('evaluation/') and '/results/' in relative)

    def walk(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                location = (*path, key)
                vision_path = any(
                    part in ('vision', 'images', 'image_asset') for part in path)
                text_path = any(part in TEXT_BODY_KEYS for part in path)
                if width == 64:
                    declared = (
                        (is_manifest and key == 'sha256' and len(path) >= 2
                         and path[0] == 'samples' and isinstance(path[1], int))
                        or (is_validation and not path and (
                            key == 'sha256' or key.endswith('_sha256')))
                        or (is_validation and key.endswith('_sha256') and not text_path)
                        or (is_validation and vision_path
                            and key in ('sha256', 'asset_revision', 'video_revision'))
                        or location in (('tests', 'log_sha256'), ('offline_tests', 'log_sha256'))
                        or (len(location) == 3 and location[0] == 'applied_files'
                            and isinstance(location[1], int) and key == 'sha256'))
                else:
                    # 32 位只豁免图片/视频证据对象里的 asset_id；sha256 截断值仍扫描。
                    declared = (is_validation and vision_path and key == 'asset_id'
                                and isinstance(item, str))
                if declared and isinstance(item, str) and digest.fullmatch(item):
                    pairs.add((key, item))
                elif declared and isinstance(item, dict) and key.endswith('_sha256'):
                    for filename, file_digest in item.items():
                        if PurePosixPath(filename).suffix in (
                                '.py', '.js', '.ts', '.vue', '.json', '.log', '.md',
                                '.yml', '.txt', '.zip') \
                                and isinstance(file_digest, str) and digest.fullmatch(file_digest):
                            pairs.add((filename, file_digest))
                walk(item, location)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, (*path, index))

    walk(data)
    spans = set()
    for match in _pair_pattern(width).finditer(raw):
        key = json.loads(b'"' + match[1] + b'"')
        if (key, match[2].decode('ascii')) in pairs:
            spans.add(match.span(2))
    return spans
