"""OSM Overpass API 河道查询。

使用免费、无需 API Key 的 Overpass API 查询 GPS 坐标附近的
水道信息 (名称、类型、宽度标签、几何)。

API 文档: https://wiki.openstreetmap.org/wiki/Overpass_API
限速: 单次查询, 结果缓存, 3秒超时
"""

import json
import math
import time
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Tuple


# 全局缓存: 避免重复查询相同坐标附近的水道
_cache: Dict[str, dict] = {}
_cache_ttl: Dict[str, float] = {}
_CACHE_TTL_SECONDS = 3600  # 缓存1小时

# 水道类型优先级: 分数越高越可能是我们要找的主河道
_WATERWAY_TYPE_PRIORITY = {
    "river": 10,
    "canal": 6,
    "tidal_channel": 5,
    "drain": 3,
    "ditch": 2,
    "stream": 1,
}


def query_all_waterways(
    lat: float,
    lon: float,
    radius_m: int = 5000,
    timeout_s: float = 3.0,
) -> List[dict]:
    """查询指定坐标附近的所有 OSM 水道 (返回列表而非单个)。

    与 query_waterway_nearby 不同, 此函数返回所有水道,
    供 track-based matching 使用。

    Args:
        lat: 纬度 (WGS-84)
        lon: 经度 (WGS-84)
        radius_m: 搜索半径 (m)
        timeout_s: 超时时间 (s)

    Returns:
        [{name, waterway_type, width_m, tags, geometry_coords, way_id,
          approx_length_m, min_distance_m, type_priority}, ...]
        按 type_priority 降序排列 (river > canal > drain > stream)
    """
    cache_key = f"all_{lat:.3f},{lon:.3f},{radius_m}"
    now = time.time()
    if cache_key in _cache and _cache_ttl.get(cache_key, 0) > now:
        return _cache[cache_key]

    overpass_ql = f"""
[out:json][timeout:{int(timeout_s)}];
(
  way(around:{radius_m},{lat},{lon})[waterway];
);
out body geom;
"""
    url = "https://overpass-api.de/api/interpreter"

    try:
        req = urllib.request.Request(
            url,
            data=overpass_ql.encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "WaterQualityMonitor/1.0 (academic research)",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        _cache[cache_key] = []
        _cache_ttl[cache_key] = now + 300
        return []

    elements = data.get("elements", [])
    results = []

    cos_lat = math.cos(math.radians(lat))

    for element in elements:
        if element.get("type") != "way":
            continue

        tags = element.get("tags", {})
        geometry = element.get("geometry", [])
        if not geometry or len(geometry) < 2:
            continue

        waterway_type = tags.get("waterway", "river")

        # 计算水道大致长度
        total_m = 0.0
        for i in range(len(geometry) - 1):
            dlat = geometry[i + 1]["lat"] - geometry[i]["lat"]
            dlon = geometry[i + 1]["lon"] - geometry[i]["lon"]
            total_m += math.sqrt(
                (dlat * 111000) ** 2 + (dlon * 111000 * cos_lat) ** 2
            )

        # 到查询点的最短距离
        min_d2 = float("inf")
        for p in geometry:
            dlat = p["lat"] - lat
            dlon = p["lon"] - lon
            d2 = (dlat * 111000) ** 2 + (dlon * 111000 * cos_lat) ** 2
            if d2 < min_d2:
                min_d2 = d2

        # width 标签
        width_m = None
        if "width" in tags:
            try:
                w = tags["width"].replace("m", "").strip()
                width_m = float(w)
            except (ValueError, AttributeError):
                pass

        name = tags.get("name") or tags.get("name:zh")

        results.append({
            "name": name,
            "waterway_type": waterway_type,
            "width_m": width_m,
            "tags": tags,
            "geometry_coords": [[p["lat"], p["lon"]] for p in geometry],
            "way_id": element.get("id"),
            "approx_length_m": round(total_m, 0),
            "min_distance_m": round(math.sqrt(min_d2), 0),
            "type_priority": _WATERWAY_TYPE_PRIORITY.get(waterway_type, 0),
        })

    # 排序: 先按类型优先级降序, 再按长度降序 (大河道优先)
    results.sort(key=lambda r: (r["type_priority"], r["approx_length_m"]), reverse=True)

    _cache[cache_key] = results
    _cache_ttl[cache_key] = now + _CACHE_TTL_SECONDS
    return results


def query_waterway_nearby(
    lat: float,
    lon: float,
    radius_m: int = 5000,
    timeout_s: float = 3.0,
) -> Optional[dict]:
    """查询指定坐标最佳的 OSM 水道 (兼容旧接口)。

    内部调用 query_all_waterways + match_river_from_track 逻辑:
    如果有 GPS 轨迹, 使用 overlap 匹配; 否则优先选类型最高 + 最长的。

    Args:
        lat: 纬度 (WGS-84)
        lon: 经度 (WGS-84)
        radius_m: 搜索半径 (m)
        timeout_s: 超时时间 (s)

    Returns:
        {name, waterway_type, width_m, tags, geometry_coords, way_id, ...}
        或 None
    """
    waterways = query_all_waterways(lat, lon, radius_m, timeout_s)
    if not waterways:
        return None

    # 没有 GPS 轨迹时: 取优先级最高的水道
    # 但如果有多个同名 river 的片段, 挑选最长的那个 named river
    named_rivers = [w for w in waterways if w["name"] and w["waterway_type"] == "river"]
    if named_rivers:
        # 按名称分组, 聚合长度
        name_groups: Dict[str, dict] = {}
        for w in named_rivers:
            n = w["name"]
            if n not in name_groups:
                name_groups[n] = dict(w)
                name_groups[n]["name_lengths"] = [(w["approx_length_m"], w["min_distance_m"])]
            else:
                name_groups[n]["approx_length_m"] += w["approx_length_m"]
                name_groups[n]["min_distance_m"] = min(
                    name_groups[n]["min_distance_m"], w["min_distance_m"]
                )
                name_groups[n]["name_lengths"].append((w["approx_length_m"], w["min_distance_m"]))
        # 选总长度最长的 named river
        best_named = max(name_groups.values(), key=lambda r: r["approx_length_m"])
        return best_named

    # 没有 named river, 取 unnamed river 中最长的
    unnamed_rivers = [w for w in waterways if not w["name"] and w["waterway_type"] == "river"]
    if unnamed_rivers:
        return unnamed_rivers[0]

    # 都没有就取优先级最高的
    return waterways[0]


def match_river_from_track(
    gps_lats: List[float],
    gps_lons: List[float],
    radius_m: int = 5000,
    timeout_s: float = 3.0,
) -> Optional[dict]:
    """用 GPS 轨迹匹配 OSM 水道 — 核心匹配算法。

    对每个 OSM 水道计算匹配分数:
      score = type_priority * (track_overlap_scalar) * name_bonus

    track_overlap: 水道几何落在 GPS 轨迹包围盒内的比例。
    轨迹包围盒 = GPS 点的 min/max lat/lon 外扩 200m。
    只有与水道的 overlap 足够高 (>30%) 才考虑该水道。
    这确保 GPS 轨迹在雅瑶水道上时不会匹配到大坑涌。

    name_bonus: 有名称的水道得 2.0x

    Args:
        gps_lats: GPS 轨迹纬度数组
        gps_lons: GPS 轨迹经度数组
        radius_m: OSM 查询半径
        timeout_s: OSM 超时

    Returns:
        最佳匹配水道 dict, 或 None
    """
    if not gps_lats or not gps_lons or len(gps_lats) < 3:
        return None

    # GPS 轨迹包围盒 (外扩 300m)
    lat_min, lat_max = min(gps_lats) - 0.003, max(gps_lats) + 0.003  # ~300m
    lon_min, lon_max = min(gps_lons) - 0.003, max(gps_lons) + 0.003

    center_lat = (lat_min + lat_max) / 2
    center_lon = (lon_min + lon_max) / 2

    waterways = query_all_waterways(center_lat, center_lon, radius_m, timeout_s)
    if not waterways:
        return None

    best_score = 0.0
    best_waterway = None

    for w in waterways:
        geom = w.get("geometry_coords", [])
        if len(geom) < 2:
            continue

        # 计算水道在 GPS 轨迹包围盒内的点数比例
        points_inside = 0
        for p in geom:
            if lat_min <= p[0] <= lat_max and lon_min <= p[1] <= lon_max:
                points_inside += 1
        overlap_ratio = points_inside / len(geom) if len(geom) > 0 else 0

        # 包围盒内点数绝对值也很重要: 包围盒至少有 3 个点才算有效 overlap
        if overlap_ratio < 0.20 and points_inside < 3:
            continue

        # 匹配分数
        type_score = w["type_priority"]  # river=10, canal=6, drain=3, stream=1
        # 重叠分: overlap_ratio 0→0.0, 1.0→1.0, 但有 boost 给高重叠
        overlap_score = overlap_ratio * (1.0 + overlap_ratio)  # range: 0~2
        name_score = 2.0 if w["name"] else 1.0
        # 长度奖励: 水道总长在 1km-20km 之间最好 (太短是小溪, 太长是大江主干)
        len_m = w["approx_length_m"]
        if 500 <= len_m <= 50000:
            len_score = 1.0 + min(len_m / 10000, 1.0)
        else:
            len_score = 0.5

        score = type_score * overlap_score * name_score * len_score

        # 对距离也很近 (<500m) 且有名称的河道额外加分
        if w["min_distance_m"] < 500 and w["name"]:
            score *= 1.3

        if score > best_score:
            best_score = score
            best_waterway = w

    if best_waterway is None:
        # 降级: 取类型最高 + 长度最长的 named river
        named = [w for w in waterways if w["name"] and w["waterway_type"] == "river"]
        if named:
            best_waterway = max(named, key=lambda w: w["approx_length_m"])
        else:
            best_waterway = waterways[0] if waterways else None

    return best_waterway


def extract_river_name_from_osm(result: Optional[dict]) -> Optional[str]:
    """从 OSM 查询结果中提取河道名称 (优先中文名)。"""
    if not result:
        return None
    return result.get("name")


def extract_waterway_type_from_osm(result: Optional[dict]) -> str:
    """从 OSM 查询结果中提取水道类型。"""
    if not result:
        return "river"
    return result.get("waterway_type", "river")
