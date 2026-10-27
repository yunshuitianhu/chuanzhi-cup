"""会话内图片附件：真实解码、规范化、哈希与按会话隔离。

设计约束
--------
- 只接受可以实际解码的栅格图片；扩展名和客户端 MIME 都不是依据。
- 保存规范化后的 JPEG/PNG，限制原始大小、像素与规范化后大小。
- 元数据与会话绑定；图片内容单独保存，不进入对话正文或 SSE。
- 项目包以 ``media/<asset_id>.<ext>`` 条目保存，清单校验哈希。
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import re

import cv2
import numpy as np

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 24_000_000
MAX_SIDE = 1600
MAX_STORED_BYTES = 4 * 1024 * 1024
MAX_ASSETS = 32
MAX_TOTAL_BYTES = 64 * 1024 * 1024
ASSET_ID = re.compile(r'^[0-9a-f]{32}$')
MEDIA_TYPES = {'image/jpeg', 'image/png'}
EXTENSIONS = {'image/jpeg': 'jpg', 'image/png': 'png'}
META_KEYS = {'id', 'sha256', 'media_type', 'width', 'height', 'bytes',
             'source', 'source_name', 'created_at'}


class MediaError(ValueError):
    pass


def _encode(image: np.ndarray) -> tuple[bytes, str]:
    """优先无损 PNG（截图文字清晰）；过大时退回高质量 JPEG。"""
    ok, png = cv2.imencode('.png', image)
    if ok and len(png) <= MAX_STORED_BYTES:
        return png.tobytes(), 'image/png'
    ok, jpg = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    if not ok:
        raise MediaError('图片无法编码为可发送的格式。')
    raw = jpg.tobytes()
    if len(raw) > MAX_STORED_BYTES:
        raise MediaError('图片在压缩后仍超过单张大小上限，请缩小截图后重试。')
    return raw, 'image/jpeg'


def _declared_dimensions(raw: bytes) -> tuple[int, int] | None:
    """在解码前读取常见格式的声明尺寸，避免超大像素图先分配内存。"""
    try:
        if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) >= 24:
            return int.from_bytes(raw[16:20], 'big'), int.from_bytes(raw[20:24], 'big')
        if raw.startswith(b'BM') and len(raw) >= 26:
            width = int.from_bytes(raw[18:22], 'little', signed=True)
            height = int.from_bytes(raw[22:26], 'little', signed=True)
            return abs(width), abs(height)
        if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP' and raw[12:16] == b'VP8X' and len(raw) >= 30:
            width = 1 + int.from_bytes(raw[24:27], 'little')
            height = 1 + int.from_bytes(raw[27:30], 'little')
            return width, height
        if raw.startswith(b'\xff\xd8'):
            index = 2
            while index + 9 < len(raw):
                if raw[index] != 0xFF:
                    index += 1
                    continue
                marker = raw[index + 1]
                index += 2
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                length = int.from_bytes(raw[index:index + 2], 'big')
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    if index + 7 <= len(raw):
                        height = int.from_bytes(raw[index + 3:index + 5], 'big')
                        width = int.from_bytes(raw[index + 5:index + 7], 'big')
                        return width, height
                if length < 2:
                    return None
                index += length
    except (IndexError, ValueError):
        return None
    return None

def normalize_image(raw: bytes, source_name: str = '') -> tuple[bytes, dict]:
    """解码并规范化一张图片；失败时给出可操作原因。"""
    if not isinstance(raw, (bytes, bytearray)) or not raw:
        raise MediaError('图片文件为空。')
    raw = bytes(raw)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise MediaError('图片超过8MB上限，请压缩或裁剪后重试。')
    declared = _declared_dimensions(raw)
    if declared is not None:
        width, height = declared
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise MediaError('图片声明像素过多，请缩小后重试（上限2400万像素）。')
    try:
        buffer = np.frombuffer(raw, dtype=np.uint8)
        image = cv2.imdecode(buffer, cv2.IMREAD_UNCHANGED)
    except Exception as exc:  # cv2 对损坏输入可能抛出不同异常
        raise MediaError('图片无法解码，请确认是常见的PNG/JPEG截图。') from exc
    if image is None or not isinstance(image, np.ndarray) or image.size == 0:
        raise MediaError('图片无法解码，请确认是常见的PNG/JPEG截图。')
    height, width = image.shape[:2]
    if width <= 0 or height <= 0:
        raise MediaError('图片尺寸无效。')
    if width * height > MAX_PIXELS:
        raise MediaError('图片像素过多，请缩小后重试（上限2400万像素）。')
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    elif image.ndim != 3 or image.shape[2] != 3:
        raise MediaError('图片通道格式不受支持，请转为PNG/JPEG后重试。')
    longest = max(width, height)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / float(longest)
        image = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    encoded, media_type = _encode(image)
    if not encoded:
        raise MediaError('图片编码结果为空，请重试。')
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(source_name or '截图'))[:100] or '截图'
    meta = {'id': '',  # 由 store_media 分配
            'sha256': sha256(encoded).hexdigest(),
            'media_type': media_type,
            'width': int(image.shape[1]),
            'height': int(image.shape[0]),
            'bytes': len(encoded),
            'source': 'upload',
            'source_name': name,
            'created_at': datetime.now(timezone.utc).isoformat()}
    return encoded, meta


def _total(session) -> int:
    return sum(int(meta.get('bytes', 0)) for meta in getattr(session, 'media_assets', {}).values())


def store_media(session, raw: bytes, source_name: str = '') -> dict:
    """保存附件；相同内容复用既有条目，避免重复占用。"""
    encoded, meta = normalize_image(raw, source_name)
    assets = session.media_assets
    for asset_id, existing in assets.items():
        if existing.get('sha256') == meta['sha256'] and asset_id in getattr(session, '_media_bytes', {}):
            return public_asset(existing)
    if len(assets) >= MAX_ASSETS:
        raise MediaError('本项目图片附件已达32张上限；请先清理不再使用的图片或新建项目。')
    if _total(session) + meta['bytes'] > MAX_TOTAL_BYTES:
        raise MediaError('本项目图片附件总量已达64MB上限；请先清理不再使用的图片。')
    from uuid import uuid4
    asset_id = uuid4().hex
    meta['id'] = asset_id
    assets[asset_id] = meta
    session._media_bytes[asset_id] = encoded
    return public_asset(meta)


def public_asset(meta: dict) -> dict:
    if not isinstance(meta, dict):
        raise MediaError('图片附件元数据无效。')
    return {'asset_id': meta['id'], 'sha256': meta['sha256'], 'media_type': meta['media_type'],
            'width': meta['width'], 'height': meta['height'], 'bytes': meta['bytes'],
            'source': meta.get('source', 'upload'), 'source_name': meta.get('source_name', ''),
            'created_at': meta.get('created_at', ''), 'preview_url': '/api/media/' + meta['id']}


def get_media(session, asset_id: str, expected_sha: str | None = None) -> tuple[dict, bytes]:
    if not isinstance(asset_id, str) or not ASSET_ID.fullmatch(asset_id):
        raise MediaError('图片附件编号无效。')
    meta = getattr(session, 'media_assets', {}).get(asset_id)
    if meta is None:
        raise MediaError('当前会话没有这张图片，可能已移除或来自另一个项目。')
    data = getattr(session, '_media_bytes', {}).get(asset_id)
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise MediaError('图片内容未随会话加载，请重新上传或恢复项目。')
    data = bytes(data)
    actual = sha256(data).hexdigest()
    if actual != meta.get('sha256'):
        raise MediaError('图片内容校验失败，不能发送；请重新上传。')
    if expected_sha is not None and expected_sha != actual:
        raise MediaError('该图片已是新版本，请重新选择附件。')
    return deepcopy(meta), data


def delete_media(session, asset_id: str) -> None:
    """仅删除未被历史回答、侧边线程或当前上下文引用的附件。"""
    meta = getattr(session, 'media_assets', {}).get(asset_id)
    if meta is None:
        raise MediaError('当前会话没有这张图片。')
    if referenced(session, asset_id):
        raise MediaError('该图片已用于历史回答或侧边线程，不能删除；移除输入区引用不会删除已保存媒体。')
    session.media_assets.pop(asset_id, None)
    session._media_bytes.pop(asset_id, None)


def referenced(session, asset_id: str) -> bool:
    """永久历史引用才阻止删除；未发送的临时选择不算永久引用。"""
    def in_card(card):
        return (isinstance(card, dict) and card.get('kind') == 'image_asset'
                and isinstance(card.get('selection'), dict)
                and card['selection'].get('asset_id') == asset_id)

    def in_vision(message):
        vision = message.get('vision') if isinstance(message, dict) else None
        images = vision.get('images') if isinstance(vision, dict) else None
        return isinstance(images, list) and any(
            isinstance(image, dict) and image.get('asset_id') == asset_id for image in images)

    for message in getattr(session, 'messages', []):
        if not isinstance(message, dict):
            continue
        if in_card(message.get('question_context')) or in_vision(message):
            return True
    for thread in getattr(session, 'side_threads', []):
        if not isinstance(thread, dict):
            continue
        if in_card(thread.get('quote')):
            return True
        for message in thread.get('messages', []):
            if in_vision(message):
                return True
    return False


def validate_metadata(meta, asset_id: str | None = None) -> dict:
    """项目包导入时的严格元数据校验。"""
    if not isinstance(meta, dict) or set(meta) != META_KEYS:
        raise MediaError('图片附件元数据字段无效。')
    ident = meta.get('id')
    if not isinstance(ident, str) or not ASSET_ID.fullmatch(ident):
        raise MediaError('图片附件编号无效。')
    if asset_id is not None and ident != asset_id:
        raise MediaError('图片附件编号与内容不一致。')
    digest = meta.get('sha256')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise MediaError('图片附件哈希无效。')
    if meta.get('media_type') not in MEDIA_TYPES:
        raise MediaError('图片附件类型无效。')
    for key in ('width', 'height', 'bytes'):
        value = meta.get(key)
        if type(value) is not int or value <= 0:
            raise MediaError('图片附件尺寸无效。')
    if meta['width'] * meta['height'] > MAX_PIXELS or meta['bytes'] > MAX_STORED_BYTES:
        raise MediaError('图片附件超出大小限制。')
    if not isinstance(meta.get('source'), str) or len(meta['source']) > 40:
        raise MediaError('图片附件来源无效。')
    if not isinstance(meta.get('source_name'), str) or len(meta['source_name']) > 100:
        raise MediaError('图片附件名称无效。')
    if not isinstance(meta.get('created_at'), str) or len(meta['created_at']) > 100:
        raise MediaError('图片附件时间无效。')
    return meta


def load_bytes(data: bytes, media_type: str, width: int, height: int) -> np.ndarray:
    """重新解码已保存图片，用于确认校验后的内容仍可发送。"""
    try:
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    except Exception as exc:
        raise MediaError('保存的图片无法重新解码。') from exc
    if image is None or image.shape[1] != width or image.shape[0] != height:
        raise MediaError('保存的图片尺寸与元数据不一致。')
    if image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] not in (3, 4)):
        raise MediaError('保存的图片通道格式无效。')
    return image


def validate_media_entry(data: bytes, meta: dict) -> None:
    if len(data) != meta['bytes']:
        raise MediaError('图片附件大小与元数据不一致。')
    if sha256(data).hexdigest() != meta['sha256']:
        raise MediaError('图片附件哈希校验失败。')
    if meta['media_type'] == 'image/png' and not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise MediaError('图片附件类型与内容不一致。')
    if meta['media_type'] == 'image/jpeg' and not data.startswith(b'\xff\xd8\xff'):
        raise MediaError('图片附件类型与内容不一致。')
    load_bytes(data, meta['media_type'], meta['width'], meta['height'])
