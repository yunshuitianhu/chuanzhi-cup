"""多渠道根因溯源融合引擎。

整合多数据源进行综合污染溯源:
  - 高德 POI (工厂/养殖场/排污口)
  - 排污许可证 (国家环保部门公开数据)
  - 国控水质站点 (国家监测网络)
  - 重点排污单位名录 (地方生态环境局)

按 config.source_weights 加权融合各数据源结果,
输出综合置信度排名的污染源列表。

用法:
    from .trace_engine import multi_source_trace

    result = multi_source_trace(lat=23.11, lon=113.14, amap_key="...", config=config)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .analysis_config import AnalysisConfig

logger = logging.getLogger(__name__)

# 从 "四川省成都市" / "广东省广州市/深圳市" / "内蒙古自治区…" 里取出省份
_PROVINCE_RE = re.compile(r"^(.{2,8}?(?:省|自治区|特别行政区|市))")


def _unique_province_by_bbox(lat: float, lon: float) -> str | None:
    """用省界经纬度盒子推断省份 —— **只在唯一命中时采信**。

    ⚠️ 为什么必须要求"唯一"：`parameter_estimator._PROVINCE_COORDS` 那张表
    的盒子**过大且大量重叠，且先匹配者胜**。实测（2026-10-15）：

        石家庄(38.04,114.51) -> 山东省   （被"山东省"那个 34.5~39.0,114~122.5
                                          的大盒子覆盖，河北排在其后）
        郴州  (25.77,113.03) -> 江西省   （江西盒子排在湖南前面且重叠）
        芜湖  (31.35,118.43) -> 江苏省
        九江  (29.71,116.00) -> 安徽省

    直接取首个命中会**大面积判错**。而按省读名录这件事上，
    **用错省份比不用更糟**（会把外省企业当本地污染源）。
    所以这里改成：多个省盒子都覆盖时视为"判不准"，返回 None。

    实测效果（同一组样本）：正确 2 / 判不准而跳过 8 / **判错 0**。
    """
    try:
        from river_model.parameter_estimator import PROVINCE_COORDS
    except Exception as e:
        logger.info("省界盒子表不可用: %s: %s", type(e).__name__, e)
        return None

    hits = {prov for a, b, c, d, prov, _cities in PROVINCE_COORDS
            if a <= lat <= b and c <= lon <= d}
    return hits.pop() if len(hits) == 1 else None


def _infer_province(lat: float, lon: float,
                    amap_key: str | None = None) -> str | None:
    """由坐标推断省份。**推断不出时返回 None**（调用方据此跳过该数据源）。

    两级，优先可靠来源：

      ① **高德逆地理**（`amap_key` 可用时）—— 直接给出「省份」字段，实测可靠。
         注意它的**市级**不一定准（雅瑶水道会被判成"广州市/深圳市"，实际在佛山），
         所以只取省一级。名录本就是按省发布的，只用到省。
      ② **省界盒子**（无 Key 或调用失败时）—— 只在唯一命中时采信，见
         `_unique_province_by_bbox` 的说明。

    两级都判不出就返回 None。**宁可不报，不可用错省份。**
    """
    # ---- ① 高德逆地理 ----
    if amap_key:
        try:
            from .geo_tools import reverse_geocode
            raw = reverse_geocode(lat, lon, amap_key)
            # reverse_geocode 返回的是 JSON 字符串；失败时返回中文提示串，
            # 因此必须先确认是 JSON 再解析（历史上曾有调用方直接 .get() 崩掉）。
            if isinstance(raw, str) and raw.lstrip().startswith("{"):
                prov = (json.loads(raw).get("省份") or "").strip()
                if prov:
                    return prov
        except Exception as e:
            logger.info("高德逆地理推断省份失败，回落到省界盒子: %s: %s",
                        type(e).__name__, e)

    # ---- ② 省界盒子（保守） ----
    return _unique_province_by_bbox(lat, lon)


def multi_source_trace(
    lat: float,
    lon: float,
    amap_key: str | None = None,
    config: "AnalysisConfig | None" = None,
    state: Any = None,
    province: str | None = None,
    city: str | None = None,
) -> str:
    """多渠道污染溯源。

    执行流程:
      1. 高德 POI: 搜索周边工厂/养殖场/排污口
      2. 排污许可证: 搜索周边持证排污企业
      3. 重点排污单位: 按行政区划检索名录
      4. 国控站点: 查询周边水质自动监测站
      5. 融合: 去重 + 加权排序 + 综合评分

    Args:
        lat: 搜索中心纬度
        lon: 搜索中心经度
        amap_key: 高德 API Key (可选, 缺少则跳过 POI 搜索)
        config: AnalysisConfig 或 None
        state: WaterQualityState (可选, 用于自动定位聚类中心)
        province: 省份（可选）。数据源 3 的排污单位名录是**按省发布**的，
            原先在此写死 "广东省" —— 任何非广东的河流都会拿到广东的名录，
            把外省企业当成本地污染源。不传则**跳过该数据源**，
            而不是拿别省的名录冒充本地数据。调用方若已知行政区
            （例如来自 config.location）应显式传入。
        city: 城市（可选），进一步筛选

    Returns:
        JSON 字符串, 含去重融合后的多源污染源列表
    """
    if config is None:
        from .analysis_config import DEFAULT_CONFIG
        config = DEFAULT_CONFIG

    # 未显式给出行政区时尝试推断。传 amap_key —— 高德逆地理给出的省份可靠，
    # 是首选来源；无 Key 时才回落到"省界盒子唯一命中"这条保守路径。
    # 两级都判不出则保持 None（跳过该源），**不用错省份**。
    if not province:
        province = _infer_province(lat, lon, amap_key)

    results_by_source: dict[str, list[dict]] = {}
    all_sources_flat: list[dict] = []
    station_background = {}

    # ---- 数据源 1: 高德 POI ----
    if amap_key and "amap_poi" in config.enabled_sources:
        try:
            from .geo_tools import search_nearby_pollution_sources
            pois = search_nearby_pollution_sources(
                lat, lon, amap_key,
                radius=config.search_radius_m,
                keywords=config.pollution_keywords,
            )
            # 标记数据源
            for p in pois:
                p["数据源"] = "高德POI"
                p["置信度权重"] = config.source_weights.get("amap_poi", 0.25)
            results_by_source["高德POI"] = pois[:15]
            all_sources_flat.extend(pois[:15])
        except Exception as e:
            logger.warning("高德 POI 搜索失败: %s", e)

    # ---- 数据源 2: 排污许可证 ----
    if "pollution_permit" in config.enabled_sources:
        try:
            from .pollution_permit import search_pollution_permits
            permits = search_pollution_permits(lat, lon, radius_m=config.search_radius_m)
            for p in permits:
                p["置信度权重"] = config.source_weights.get("pollution_permit", 0.35)
            results_by_source["排污许可证"] = permits
            all_sources_flat.extend(permits)
        except Exception as e:
            logger.warning("排污许可证搜索失败: %s", e)

    # ---- 数据源 3: 重点排污单位名录 ----
    # 名录按省发布。原先写死 ("广东省", "佛山市")，任何非广东的河流都会读到
    # 广东的名录 —— 把外省企业当成本地污染源，是正确性问题而非展示问题。
    # 未提供行政区时跳过该源：宁可不报，不可拿别省数据冒充本地。
    if "national_station" in config.enabled_sources:
        if not province:
            logger.info(
                "未提供 province，跳过「重点排污单位」数据源 —— 该源按省读名录，"
                "用错省份会把外省企业当成本地污染源。调用方应传入已知行政区。"
            )
        else:
            try:
                from .pollution_permit import search_key_pollution_sources
                key_sources = search_key_pollution_sources(province, city)
                for s in key_sources:
                    s["置信度权重"] = config.source_weights.get("national_station", 0.25)
                results_by_source["重点排污单位"] = key_sources
                all_sources_flat.extend(key_sources)
            except Exception as e:
                logger.warning("重点排污单位搜索失败: %s", e)

    # ---- 数据源 4: 国控站点 (优先走实时数据) ----
    if "national_station" in config.enabled_sources:
        try:
            from .national_station import get_national_station_data as _station_data
            # 与技术页相同的 HTTP API，按已核实省份请求；实际回退状态随结果返回。
            stations_json = _station_data(
                province=province or '',
                lat=lat, lon=lon, radius_km=config.national_station_radius_km,
                live=True,
            )
            stations_data = json.loads(stations_json) if isinstance(stations_json, str) else stations_json
            station_background = stations_data
            station_list = stations_data.get("站点列表", [])
            sources = []
            for st in station_list:
                sources.append({
                    "数据源": "国控站点",
                    "站点名称": st.get("站名", ""),
                    "流域": st.get("流域", ""),
                    "水质目标": st.get("水质目标", ""),
                    "距离_km": st.get("距离_km", ""),
                    "置信度权重": config.source_weights.get("national_station", 0.25),
                })
            results_by_source["国控站点"] = sources
            # 监测站点是背景资料，不参与排污企业候选排序。
        except Exception as e:
            logger.warning("国控站点查询失败: %s", e)

    # ---- 汇总 ----
    source_summary = {
        data_source: len(items) for data_source, items in results_by_source.items()
    }

    if not all_sources_flat:
        return json.dumps({
            "状态": "所有数据源均未返回结果",
            "搜索中心": f"{lat:.6f}, {lon:.6f}",
            "搜索半径_m": config.search_radius_m,
            "已启用的数据源": config.enabled_sources,
            "国控监测背景": station_background,
            "建议": "检查 AMAP_KEY 是否配置或扩大搜索半径",
        }, ensure_ascii=False)

    return json.dumps({
        "搜索中心": f"{lat:.6f}, {lon:.6f}",
        '证据边界': '结果是周边待核实资料；权重仅表示数据源的配置优先级，不是来源概率。尚未核验入河连接、上下游关系、排放时段及输移一致性，不能确认污染源。',
        "搜索半径_m": config.search_radius_m,
        "各数据源贡献": source_summary,
        "国控监测背景": station_background,
        "数据源说明": {
            "排污许可证": "全国排污许可证管理信息平台 (permit.mee.gov.cn) — 环保部门官方数据, 权威性高",
            "国控站点": "国家水质自动监测实时数据发布平台 (szzdjc.cnemc.cn) — 环保部门官方监测网络",
            "高德POI": "高德开放平台 POI 搜索 — 补充商业/地理信息, 覆盖面广",
            "重点排污单位": "地方生态环境局年度公开名录 — 官方监管数据",
        },
        "综合污染源列表": _deduplicate_and_rank(all_sources_flat, config),
    }, ensure_ascii=False)


def _deduplicate_and_rank(
    sources: list[dict],
    config: "AnalysisConfig",
) -> list[dict]:
    """按名称去重并加权排序。

    同名称来源取最高置信度权重, 按加权分数降序排列。
    """
    # 按名称去重
    seen: dict[str, dict] = {}
    for src in sources:
        name = src.get("name") or src.get("企业名称") or src.get("站点名称") or src.get("名称") or ""
        if not name:
            name = src.get("站名", "")
        if not name:
            continue

        weight = src.get("置信度权重", 0.2)
        if name not in seen or weight > seen[name].get("置信度权重", 0):
            seen[name] = src

    ranked = sorted(seen.values(), key=lambda x: x.get("置信度权重", 0), reverse=True)

    # 添加排名
    result = []
    for i, src in enumerate(ranked, 1):
        entry = {
            "排名": i,
            "名称": (src.get("name") or src.get("企业名称") or src.get("站点名称") or src.get("站名") or ""),
        }
        # 复制关键字段
        for field in ["数据源", "置信度权重", "距离", "distance", "地址", "address", "行业类别", "主要污染物", "流域", "水质目标"]:
            if field in src:
                entry[field] = src[field]
        result.append(entry)

    return result[:20]  # 最多返回 20 条
