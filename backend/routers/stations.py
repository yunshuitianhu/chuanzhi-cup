"""国控水质站点路由。

数据来自生态环境部「国家地表水水质自动监测实时数据发布系统」，
覆盖全国 1800+ 断面，每小时更新；不可达时自动降级到内置站点库。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from water_quality_mcp.src.water_quality_mcp.national_station import get_national_station_data

from ..deps import get_exclusive_session
from ..session import Session

router = APIRouter(prefix="/stations", tags=["stations"])

# 与前端下拉框一致。后端还有完整省份码表，这里只是给前端渲染选项用。
PROVINCES = [
    "广东省", "江苏省", "浙江省", "湖北省", "湖南省", "四川省", "山东省",
    "河南省", "河北省", "北京市", "上海市", "重庆市", "福建省", "安徽省",
    "江西省", "广西壮族自治区", "海南省", "云南省", "贵州省", "陕西省",
    "山西省", "甘肃省", "辽宁省", "吉林省", "黑龙江省", "内蒙古自治区",
    "西藏自治区", "宁夏回族自治区", "青海省", "新疆维吾尔自治区", "天津市",
]


@router.get("/provinces")
def provinces() -> dict:
    return {"provinces": PROVINCES}


@router.get("")
def query(
    province: str = Query("", description="省份，如「广东省」"),
    keyword: str = Query("", description="站名或流域关键词"),
    live: bool = Query(True, description="true=实时接口，false=内置离线库"),
    lat: float | None = Query(None),
    lon: float | None = Query(None),
    radius_km: int = Query(50),
    session: Session = Depends(get_exclusive_session),
) -> dict:
    """查询国控站点。

    实时模式必须给省份 —— 全量拉取全国 300+ 站点会让政府服务器超时，
    内核已对此做了保护（无省份时直接降级离线库）。
    """
    raw = get_national_station_data(
        province=province, keyword=keyword,
        lat=lat, lon=lon, radius_km=radius_km, live=live,
    )
    data = json.loads(raw) if isinstance(raw, str) else raw
    from datetime import datetime, timezone
    data['查询条件'] = {'省份': province, '关键词': keyword, '实时请求': live,
                        '纬度': lat, '经度': lon, '半径_km': radius_km}
    data['本次查询时间'] = datetime.now(timezone.utc).isoformat()
    session.national_station_result = data
    session.tool_evidence['get_national_station_data'] = json.dumps(data, ensure_ascii=False)
    # 站点列表可能很长，前端一次展示不了那么多；保留前 50 条并告知总数
    stations = data.get("站点列表", []) or []
    return {
        **{k: v for k, v in data.items() if k != "站点列表"},
        "站点列表": stations[:50],
        "返回条数": min(len(stations), 50),
    }
