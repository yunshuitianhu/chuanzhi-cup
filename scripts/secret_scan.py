"""仅识别验证元数据中的SHA-256值；同值出现在正文/配置中仍扫描。"""
import json
import re
from pathlib import PurePosixPath

HASH = re.compile(r'[0-9a-f]{64}\Z')
# 跳过字符串内部转义的JSON片段；返回值位置按bytes计，兼容中文前缀。
PAIR = re.compile(rb'(?<!\\)"((?:[^"\\]|\\.)*)"\s*:\s*"([0-9a-f]{64})"')


def metadata_hash_spans(relative: str, raw: bytes) -> set[tuple[int, int]]:
    if not relative.endswith('.json') or not (relative.startswith('docs/validation/') or
            relative.startswith('evaluation/') and '/results/' in relative):
        return set()
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return set()
    pairs = set()

    def walk(value, path=()):
        if isinstance(value, dict):
            for key,item in value.items():
                location = (*path,key)
                declared = (not path and (key == 'sha256' or key.endswith('_sha256'))) or location in (
                    ('tests','log_sha256'),('offline_tests','log_sha256')) or (
                    len(location)==3 and location[0]=='applied_files' and isinstance(location[1],int) and key=='sha256')
                if declared and isinstance(item, str) and HASH.fullmatch(item):
                    pairs.add((key,item))
                elif declared and isinstance(item,dict) and key.endswith('_sha256'):
                    for filename,digest in item.items():
                        if PurePosixPath(filename).suffix in ('.py','.js','.ts','.vue','.json','.log','.md','.yml','.txt','.zip') \
                                and isinstance(digest,str) and HASH.fullmatch(digest):
                            pairs.add((filename,digest))
                walk(item,location)
        elif isinstance(value, list):
            for index,item in enumerate(value):
                walk(item,(*path,index))

    walk(data)
    spans = set()
    for match in PAIR.finditer(raw):
        key = json.loads(b'"'+match[1]+b'"')
        if (key,match[2].decode('ascii')) in pairs:
            spans.add(match.span(2))
    return spans
