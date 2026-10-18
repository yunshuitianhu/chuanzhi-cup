"""高德官方静态底图代理：固定上游，密钥仅留在服务端，不创建分析会话。"""
from __future__ import annotations

from collections import OrderedDict
import hashlib
import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query, Response
import requests

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key

router = APIRouter(prefix="/maps", tags=["maps"])
_CACHE: OrderedDict = OrderedDict()
_LOCK = threading.Lock()
_TTL = 60
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_MAX_CACHE_BYTES = 16 * 1024 * 1024
_UPSTREAM = "https://restapi.amap.com/v3/staticmap"


@router.get("/amap-static")
def amap_static(
    lat: float = Query(ge=-85, le=85, allow_inf_nan=False),
    lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    zoom: int = Query(ge=1, le=17),
    width: int = Query(ge=1, le=1024),
    height: int = Query(ge=1, le=1024),
):
    """中心使用GCJ-02；前端对显示坐标转换，原始监测坐标不修改。"""
    key = resolve_api_key("AMAP_KEY")
    if not key:
        raise HTTPException(503, "未配置高德 Web服务 Key，请在后端配置 AMAP_KEY。")
    location = f"{lon:.6f},{lat:.6f}"
    cache_key = (hashlib.sha256(key.encode()).digest(), location, zoom, width, height)
    with _LOCK:
        now = time.monotonic()
        for expired in [k for k, v in _CACHE.items() if v[0] <= now]:
            _CACHE.pop(expired)
        cached = _CACHE.get(cache_key)
        if cached:
            _CACHE.move_to_end(cache_key)
            return _image_response(cached[1], cached[2])
    try:
        # 不跟随跳转，防止Key被携带到其他主机。禁止将requests异常（包含URL）回传。
        with requests.get(_UPSTREAM, params={
            "key": key, "location": location, "zoom": zoom,
            "size": f"{width}*{height}", "scale": 1,
        }, timeout=(5, 12), stream=True, allow_redirects=False) as upstream:
            if upstream.status_code != 200:
                raise HTTPException(502, f"高德底图服务返回 HTTP {upstream.status_code}，请重试或切换底图。")
            chunks, length = [], 0
            for chunk in upstream.iter_content(65536):
                length += len(chunk)
                if length > _MAX_IMAGE_BYTES:
                    raise HTTPException(502, "高德底图响应过大，请缩小地图窗口后重试。")
                chunks.append(chunk)
            body = b"".join(chunks)
            content_type = upstream.headers.get("Content-Type", "").split(";")[0].strip().lower()
            png = content_type == "image/png" and body.startswith(b"\x89PNG\r\n\x1a\n")
            jpeg = content_type in ("image/jpeg", "image/jpg") and body.startswith(b"\xff\xd8\xff")
            if not (png or jpeg):
                # API可能以HTTP200返回JSON权限/额度错误；仅暴露数字错误码。
                import json
                try:
                    code = str(json.loads(body).get("infocode", ""))
                except (ValueError, AttributeError, TypeError):
                    code = ""
                safe_code = f"（错误码 {code}）" if re.fullmatch(r"\d{5}", code) else ""
                raise HTTPException(502, f"高德未返回地图图片{safe_code}；请检查 Web服务 Key、静态地图权限、IP白名单和额度。")
    except requests.RequestException:
        raise HTTPException(502, "高德底图连接失败或超时，请检查后端网络后重试。") from None
    with _LOCK:
        _CACHE[cache_key] = (time.monotonic() + _TTL, body, content_type)
        while len(_CACHE) > 16 or sum(len(v[1]) for v in _CACHE.values()) > _MAX_CACHE_BYTES:
            _CACHE.popitem(last=False)
    return _image_response(body, content_type)


def _image_response(body, content_type):
    return Response(body, media_type=content_type, headers={
        "Cache-Control": f"private, max-age={_TTL}", "X-Content-Type-Options": "nosniff",
    })
