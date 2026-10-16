"""污染溯源路由：多渠道融合、周边 POI、逆地理编码。

内核契约：
  1. `reverse_geocode` 返回的是 **JSON 字符串**（中文键），不是 dict
  2. 输入统一为 WGS-84，内核在请求高德前转为 GCJ-02，POI 返回点转回 WGS-84。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
from water_quality_mcp.src.water_quality_mcp.geo_tools import reverse_geocode
from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace

from ..deps import get_session
from ..session import Session

router = APIRouter(prefix="/trace", tags=["trace"])

# 说明坐标转换与数据本身的精度边界。
COORD_NOTE = (
    "输入搜索中心为 WGS-84，请求高德前已转换为 GCJ-02，返回 POI 点位已转回 WGS-84。"
    "地图资料点位不等于实测排口，候选线索仍需现场核实。"
)


def _default_center(session: Session) -> tuple[float, float] | None:
    """未给坐标时，用黑点最密集的聚类中心（与原 app.py 行为一致）。

    注意排除 cluster_id == -1（DBSCAN 噪声点）—— 噪声点常常数量最多，
    不排除的话搜索中心会落在离散噪声上。
    """
    black = session.black_spots
    if black is None or len(black) == 0:
        return None
    valid = black[black["cluster_id"] != -1]
    if len(valid) == 0:
        return None
    cid = valid["cluster_id"].value_counts().idxmax()
    c = valid[valid["cluster_id"] == cid]
    return float(c["latitude"].mean()), float(c["longitude"].mean())


@router.post("/multi-source")
def multi_source(payload: dict | None = None,
                 session: Session = Depends(get_session)) -> dict:
    """多渠道污染溯源：高德 POI + 排污许可证 + 国控站点，加权融合去重。

    未传坐标时自动取黑点最密集的聚类中心。
    """
    payload = payload or {}
    lat, lon = payload.get("lat"), payload.get("lon")
    if lat is None or lon is None:
        c = _default_center(session)
        if c is None:
            raise HTTPException(400, "未提供坐标，且当前会话没有可用于推断的黑点聚类")
        lat, lon = c

    cfg = session.analysis_config
    if payload.get("radius_m"):
        cfg.search_radius_m = int(payload["radius_m"])

    raw = multi_source_trace(lat, lon, resolve_api_key("AMAP_KEY"), cfg)
    data = json.loads(raw) if isinstance(raw, str) else raw

    # ⚠️ 展开必须放在前面。内核返回的 "搜索中心" 是**字符串**
    # ("23.175316, 113.122026")，若放在后面展开会覆盖掉这里的数组形式，
    # 前端按数组取值时会崩（实测：`"2".toFixed is not a function`，
    # 整个结果表格渲染失败）。
    return {
        **data,
        "搜索中心": [lat, lon],           # 数组形式，覆盖内核的字符串版本
        "搜索中心文本": data.get("搜索中心", ""),   # 内核原值仍保留
        "坐标系说明": COORD_NOTE,
    }


@router.get("/reverse-geocode")
def regeo(lat: float = Query(...), lon: float = Query(...)) -> dict:
    """逆地理编码：经纬度 → 地址 / 省市区。"""
    key = resolve_api_key("AMAP_KEY")
    if not key:
        raise HTTPException(400, "未配置高德 API Key")
    raw = reverse_geocode(lat, lon, key)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(502, f"高德接口返回异常：{str(raw)[:100]}")
    return {**data, "坐标系说明": COORD_NOTE}
