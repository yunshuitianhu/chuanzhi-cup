"""区域河道参数默认值查表。

基于 Chow (1959) Open-Channel Hydraulics + 中国河流经验数据。
为河道参数自动检测提供第二级降级方案（第一级: OSM/Amap 精确数据,
第二级: 此查表, 第三级: 通用默认值）。

数据来源:
  - Manning's n: Chow 1959, Table 5-6
  - 坡降: 河流地貌学经验值 (Schumm, Rosgen)
  - 水深-河宽关系: 水力几何形态 (Leopold & Maddock 1953)
  - 流量: 珠江三角洲水文年鉴 + 集水面积回归
"""

from typing import Dict, Optional, Tuple

import numpy as np

# ======================================================================
# Manning's n 糙率查表
# key: (waterway_type, channel_condition)
# waterway_type: "river" | "canal" | "drain" | "tidal_channel" | "stream"
# channel_condition 从 bank_material 推断
# ======================================================================

MANNINGS_N_TABLE: Dict[Tuple[str, str], float] = {
    # ---- 自然河流 ----
    ("river", "clean_straight"):      0.030,   # 清洁、顺直
    ("river", "clean_winding"):       0.035,   # 清洁、蜿蜒
    ("river", "weedy"):               0.050,   # 有水草
    ("river", "heavy_trees"):         0.075,   # 密林、巨石
    ("river", "mountain_gravel"):     0.040,   # 山区卵石河床
    ("river", "mountain_boulder"):    0.055,   # 山区漂石

    # ---- 人工渠道 ----
    ("canal", "concrete_smooth"):     0.013,   # 光滑混凝土
    ("canal", "concrete_unfinished"): 0.017,   # 未抹面混凝土
    ("canal", "earth_clean"):         0.022,   # 土质、清洁
    ("canal", "earth_weathered"):     0.027,   # 土质、风化
    ("canal", "earth_weedy"):         0.033,   # 土质、有杂草
    ("canal", "riprap"):              0.030,   # 抛石护岸

    # ---- 排水渠 ----
    ("drain", "concrete"):            0.017,   # 混凝土衬砌
    ("drain", "earth"):               0.025,   # 土质
    ("drain", "vegetated"):           0.040,   # 植被茂密

    # ---- 潮汐通道 ----
    ("tidal_channel", "muddy"):       0.025,   # 淤泥质
    ("tidal_channel", "sandy"):       0.022,   # 沙质
    ("tidal_channel", "mixed"):       0.028,   # 混合底质 (珠江三角洲常见)

    # ---- 小溪 ----
    ("stream", "natural"):            0.040,   # 自然状态
    ("stream", "urban"):              0.025,   # 城市渠道化
}

# ======================================================================
# 纵向坡降查表 (m/m)
# key: (region_type, waterway_type)
# ======================================================================

SLOPE_TABLE: Dict[Tuple[str, str], float] = {
    # 珠江三角洲 / 河口平原
    ("delta_plain", "river"):         0.00010,  # 平原河流 (雅瑶水道典型值)
    ("delta_plain", "canal"):         0.00005,  # 人工渠道几乎水平
    ("delta_plain", "tidal_channel"): 0.00002,  # 潮汐主导, 水面坡降极缓
    ("delta_plain", "drain"):         0.00020,  # 排水渠略陡

    # 低海拔平原 (长江中下游、华北平原等)
    ("lowland", "river"):             0.00015,  # 平原河流
    ("lowland", "canal"):             0.00010,

    # 丘陵/台地
    ("foothill", "river"):            0.00050,  # 丘陵河流
    ("foothill", "stream"):           0.00200,  # 丘陵溪流

    # 山区
    ("mountain", "river"):            0.00300,
    ("mountain", "stream"):           0.00800,
}

# ======================================================================
# 河道类型 → 默认河宽 (m)
# 当 GPS/OSM/卫星都无法确定宽度时使用
# ======================================================================

DEFAULT_WIDTH_TABLE: Dict[str, float] = {
    "river":         50.0,   # 自然河流默认宽度
    "canal":         20.0,   # 人工渠道默认宽度
    "drain":          8.0,   # 排水渠
    "tidal_channel": 55.0,   # 潮汐通道 (珠江三角洲典型)
    "stream":         5.0,   # 溪流
}

# ======================================================================
# 地区 → 默认潮汐幅度 (m)
# ======================================================================

TIDAL_AMPLITUDE_BY_REGION: Dict[str, float] = {
    "珠江口":         0.80,
    "伶仃洋":         0.90,
    "磨刀门":         0.70,
    "虎门":           0.85,
    "崖门":           0.65,
    "长江口":         2.50,
    "杭州湾":         3.50,
    "闽江口":         2.80,
    "default":        0.80,
}

# ======================================================================
# 广东省主要河道已知参数库 (手动整理)
# 匹配到已知河道时, 可提供高置信度参数
# ======================================================================

KNOWN_RIVERS_GD: Dict[str, dict] = {
    "雅瑶水道": {
        "length_km": 3.2, "width_avg_m": 55.0, "depth_avg_m": 2.0,
        "mannings_n": 0.028, "slope": 0.0001, "discharge_m3s": 6.9,
        "tidal_amplitude_m": 0.8, "location": "佛山市南海区",
        # GPS bounding box (lat_min, lat_max, lon_min, lon_max)
        "bbox": (23.10, 23.18, 113.11, 113.15),
    },
    "北村水道": {
        "length_km": 5.0, "width_avg_m": 40.0, "depth_avg_m": 2.5,
        "mannings_n": 0.025, "slope": 0.00008, "discharge_m3s": 10.0,
        "tidal_amplitude_m": 0.8, "location": "佛山市南海区",
        "bbox": (23.12, 23.20, 113.08, 113.13),
    },
    "佛山水道": {
        "length_km": 25.0, "width_avg_m": 80.0, "depth_avg_m": 3.5,
        "mannings_n": 0.025, "slope": 0.00005, "discharge_m3s": 30.0,
        "tidal_amplitude_m": 0.9, "location": "佛山市禅城区/南海区",
        "bbox": (23.00, 23.10, 113.00, 113.20),
    },
    "西南涌": {
        "length_km": 38.0, "width_avg_m": 60.0, "depth_avg_m": 3.0,
        "mannings_n": 0.028, "slope": 0.00008, "discharge_m3s": 20.0,
        "tidal_amplitude_m": 0.7, "location": "佛山市三水区/南海区",
        "bbox": (23.05, 23.25, 112.80, 113.10),
    },
    "珠江广州段": {
        "length_km": 80.0, "width_avg_m": 400.0, "depth_avg_m": 8.0,
        "mannings_n": 0.022, "slope": 0.00001, "discharge_m3s": 1500.0,
        "tidal_amplitude_m": 1.0, "location": "广州市",
        "bbox": (23.00, 23.15, 113.20, 113.55),
    },
    "北江": {
        "length_km": 573.0, "width_avg_m": 300.0, "depth_avg_m": 5.0,
        "mannings_n": 0.030, "slope": 0.00030, "discharge_m3s": 1200.0,
        "tidal_amplitude_m": 0.3, "location": "清远市/佛山市",
        "bbox": (23.00, 24.50, 112.80, 113.50),
    },
    "西江": {
        "length_km": 2214.0, "width_avg_m": 500.0, "depth_avg_m": 10.0,
        "mannings_n": 0.025, "slope": 0.00020, "discharge_m3s": 7000.0,
        "tidal_amplitude_m": 0.5, "location": "肇庆市/佛山市/江门市",
        "bbox": (22.00, 24.00, 110.00, 113.50),
    },
    "东江": {
        "length_km": 562.0, "width_avg_m": 200.0, "depth_avg_m": 4.0,
        "mannings_n": 0.030, "slope": 0.00040, "discharge_m3s": 700.0,
        "tidal_amplitude_m": 0.3, "location": "惠州市/东莞市",
        "bbox": (22.50, 24.00, 113.50, 115.00),
    },
    "潭江": {
        "length_km": 248.0, "width_avg_m": 150.0, "depth_avg_m": 3.5,
        "mannings_n": 0.028, "slope": 0.00015, "discharge_m3s": 200.0,
        "tidal_amplitude_m": 0.6, "location": "江门市",
        "bbox": (22.00, 22.60, 112.50, 113.20),
    },
}


def lookup_mannings_n(waterway_type: str, channel_condition: str = "clean_straight") -> float:
    """查表获取 Manning's n。

    Args:
        waterway_type: OSM waterway 值 ("river", "canal", "drain", "tidal_channel", "stream")
        channel_condition: 河道状况 ("clean_straight", "concrete_smooth", "earth_clean", 等)

    Returns:
        Manning's n 值
    """
    key = (waterway_type, channel_condition)
    if key in MANNINGS_N_TABLE:
        return MANNINGS_N_TABLE[key]
    # 尝试模糊匹配
    for (wt, cc), n in MANNINGS_N_TABLE.items():
        if wt == waterway_type:
            return n
    # 最终默认值
    return 0.028


def lookup_slope(region_type: str, waterway_type: str) -> float:
    """查表获取纵向坡降。

    Args:
        region_type: 区域类型 ("delta_plain", "lowland", "foothill", "mountain")
        waterway_type: OSM waterway 值

    Returns:
        纵向坡降 (m/m)
    """
    key = (region_type, waterway_type)
    if key in SLOPE_TABLE:
        return SLOPE_TABLE[key]
    # 降级: 使用该区域的默认坡降
    for (rt, _), s in SLOPE_TABLE.items():
        if rt == region_type:
            return s
    return 0.0001  # 通用默认值


def lookup_default_width(waterway_type: str) -> float:
    """获取河道类型的默认宽度 (m)。"""
    return DEFAULT_WIDTH_TABLE.get(waterway_type, 30.0)


def classify_region(lat: float, lon: float) -> str:
    """根据经纬度分类区域类型。

    Returns:
        "delta_plain" | "lowland" | "foothill" | "mountain" | "coastal"
    """
    # 珠江三角洲范围: 21.5-23.5°N, 112.5-114.5°E
    if 21.5 <= lat <= 23.5 and 112.5 <= lon <= 114.5:
        return "delta_plain"
    # 长江三角洲: 30.0-32.5°N, 119.5-122.0°E
    if 30.0 <= lat <= 32.5 and 119.5 <= lon <= 122.0:
        return "delta_plain"
    # 华北平原: 35.0-40.0°N, 114.0-120.0°E, elevation < 200m
    if 35.0 <= lat <= 40.0 and 114.0 <= lon <= 120.0:
        return "lowland"
    # 近海 (距海岸线粗略判断): 如果靠近已知海岸线
    # 简化为: 东部沿海省份为低地
    if lon > 118.0 and lat < 35.0:
        return "lowland"
    # 默认
    return "lowland"


def estimate_depth_from_width(width_m: float, waterway_type: str = "river") -> float:
    """水力几何形态经验公式: 水深 ≈ 0.12 * 河宽^0.7 (Leopold & Maddock).

    Args:
        width_m: 河宽 (m)
        waterway_type: 河道类型

    Returns:
        估计平均水深 (m)
    """
    if waterway_type in ("canal", "drain"):
        # 人工渠道: 宽深比较大 (矩形/梯形断面)
        return max(1.0, width_m * 0.06)
    elif waterway_type == "tidal_channel":
        # 潮汐通道: 喇叭形, 深槽
        return max(1.5, 0.15 * width_m ** 0.65)
    else:
        # 自然河流
        return max(0.5, 0.12 * width_m ** 0.7)


def estimate_discharge(catchment_area_km2: float) -> float:
    """集水面积-流量经验关系 (珠江三角洲地区)。

    基于广东省水文数据回归: Q_mean ≈ 0.056 * A^0.85
    其中 Q 单位为 m³/s, A 为集水面积 km²。

    对于未知集水面积, 返回默认值 6.9 m³/s (雅瑶水道典型值)。

    Args:
        catchment_area_km2: 集水面积 (km²), 如果 <=0 则返回默认值

    Returns:
        多年平均流量 (m³/s)
    """
    if catchment_area_km2 <= 0:
        return 6.9
    return max(1.0, 0.056 * catchment_area_km2 ** 0.85)


def lookup_known_river(name: str) -> Optional[dict]:
    """在广东省已知河道数据库中查找。

    Args:
        name: 河道名称 (支持部分匹配)

    Returns:
        已知参数字典, 或 None
    """
    if not name:
        return None

    # 精确匹配
    if name in KNOWN_RIVERS_GD:
        return KNOWN_RIVERS_GD[name]

    # 模糊匹配
    name_clean = name.replace(" ", "").replace("　", "")
    for river_name, params in KNOWN_RIVERS_GD.items():
        if river_name in name_clean or name_clean in river_name:
            return params

    return None


def match_known_river_by_coords(
    lats: "list[float]",
    lons: "list[float]",
) -> Optional[Tuple[str, dict]]:
    """根据 GPS 坐标匹配已知河道数据库。

    计算 GPS 轨迹中心点, 检查落入哪个已知河道的 bbox。
    有多条候选时, 选择 bbox 面积最小 (最精确) 的那个。

    Args:
        lats: GPS 轨迹纬度列表
        lons: GPS 轨迹经度列表

    Returns:
        (河道名称, 参数字典) 或 None
    """
    if not lats or not lons or len(lats) < 1:
        return None

    center_lat = float(np.mean(lats))
    center_lon = float(np.mean(lons))

    candidates = []
    for name, params in KNOWN_RIVERS_GD.items():
        bbox = params.get("bbox")
        if not bbox:
            continue
        lat_min, lat_max, lon_min, lon_max = bbox
        if lat_min <= center_lat <= lat_max and lon_min <= center_lon <= lon_max:
            # 计算 bbox 面积 (用于打破平局, 越小越精确)
            area = (lat_max - lat_min) * (lon_max - lon_min)
            candidates.append((area, name, params))

    if not candidates:
        return None

    # 取 bbox 面积最小的 (最精确的匹配)
    candidates.sort(key=lambda x: x[0])
    _, best_name, best_params = candidates[0]
    return best_name, best_params
