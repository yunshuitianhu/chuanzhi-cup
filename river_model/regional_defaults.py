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

import logging
from typing import Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

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

# ⚠️ 关于上表的可及性说明
# ------------------------
# 调用方（parameter_estimator）目前**只取 "default" 键**，8 个具体河口键
# 在现有代码路径下不可达。后果：
#   - 珠三角河道：0.80 与各分河口真值（0.65~0.90）接近，影响很小
#   - 杭州湾（真值 3.50）/ 长江口（2.50）/ 闽江口（2.80）：会被低估 3~4 倍，
#     而这是水动力模型的主要输入之一
#
# 要走通这些键，需要一个"坐标或名称 → 河口"的匹配器。当前**未实现**，
# 原因是珠三角内部各分河口（珠江口/伶仃洋/虎门/磨刀门/崖门）在空间上
# 高度重叠，仅凭 bbox 难以可靠区分，误判反而会引入更差的参数。
# 这是一个已知局限，不是遗漏 —— 若后续要补，建议用名称匹配（OSM/高德
# 返回的水体名）而非坐标匹配。
TIDAL_AMPLITUDE_DEFAULT_M: float = TIDAL_AMPLITUDE_BY_REGION["default"]

# 未知河道的集水面积占位值 (km²)。
# 取珠江三角洲平原河涌的典型量级（雅瑶水道 122.8）。
# 注意：这是**占位值而非推断值** —— 对西江这类大河（约 35 万 km²）会严重低估。
# 使用方须在输出中如实标注该参数的来源为"经验估算"、置信度低。
DEFAULT_CATCHMENT_KM2: float = 122.8

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


# ======================================================================
# 地形起伏判据（阈值取自 15 条实测样本的自然断点）
# ======================================================================
# 15 条河按起伏排序后，最大的两个间隔出现在：
#     65 m (长江南京) -> 176 m (嘉陵江重庆)   间隔 111 m
#   324 m (西江肇庆) -> 466 m (澜沧江云南)   间隔 142 m
# 阈值即落在这两处，不是先定类别再凑数字。
RELIEF_HILL_MIN = 100.0       # 起伏 >= 100 m 视为丘陵/台地
RELIEF_MOUNTAIN_MIN = 400.0   # 起伏 >= 400 m 视为山地
DELTA_MEAN_MAX = 20.0         # 低平地中高程均值 <= 20 m 归入三角洲平原


def classify_by_relief(mean_m: float, relief_m: float) -> str:
    """按地形起伏与高程均值判定区域类型。

    **判据以起伏为主、高程为辅** —— 塔里木河高程均值 916 m 但起伏仅 5 m
    （高原上的平缓河谷），只看高程会把它误判成山区。

    验证状态（2026-10-15，见 data/external/terrain/）：
      用 HydroRIVERS 河道中心线 + SRTM/ETOPO1 沿程高程反算纵坡做独立验证。
      13 条河的两个高程源全部同量级；其中 5 条 R²≥0.8 可信，落在给定值的
      0.19~1.33 倍内 —— **数量级成立**。
      ⚠️ 但测量的是"采样点往下游 180 km 的平均纵坡"，**不是该点的局部坡降**
      （岷江都江堰、黄河兰州恰好是河流冲出山区的位置，下游大部分在平原），
      因此**不足以据此重新标定 SLOPE_TABLE 的数值**，故本表数值保持不变。
    """
    if relief_m >= RELIEF_MOUNTAIN_MIN:
        return "mountain"
    if relief_m >= RELIEF_HILL_MIN:
        return "foothill"
    return "delta_plain" if mean_m <= DELTA_MEAN_MAX else "lowland"


def _classify_by_coords(lat: float, lon: float) -> str:
    """经纬度方框启发式 —— **现仅作为地形数据不可用时的回落**。

    ⚠️ 这条例路本身覆盖极差：穷举中国全境网格，**99.3% 会返回 lowland**，
    `foothill` / `mountain` / `coastal` 一次都不返回 —— 山区河道据此会拿到
    平原坡降（错 20~33 倍）。保留它只是为了"网络不可用时至少还能出结果"，
    **不要把它当作正确实现**。
    """
    # 珠江三角洲范围: 21.5-23.5°N, 112.5-114.5°E
    if 21.5 <= lat <= 23.5 and 112.5 <= lon <= 114.5:
        return "delta_plain"
    # 长江三角洲: 30.0-32.5°N, 119.5-122.0°E
    if 30.0 <= lat <= 32.5 and 119.5 <= lon <= 122.0:
        return "delta_plain"
    # 华北平原
    if 35.0 <= lat <= 40.0 and 114.0 <= lon <= 120.0:
        return "lowland"
    # 东部沿海省份粗略视为低地
    if lon > 118.0 and lat < 35.0:
        return "lowland"
    return "lowland"


def classify_region(lat: float, lon: float, use_terrain: bool = True) -> str:
    """判定河道所在区域的类型。

    优先使用**真实地形起伏**（SRTM，经 terrain_lookup 查询并缓存）；
    查询不可用时**回落到经纬度启发式**，保证参数推导链不会因网络问题中断。

    降级链：进程内缓存 → 磁盘缓存 → OpenTopoData → 经纬度启发式

    Args:
        use_terrain: 置 False 可强制走旧逻辑（测试/离线复现用）

    Returns:
        "delta_plain" | "lowland" | "foothill" | "mountain"
        （旧逻辑理论上还可能返回 "coastal"，但那条分支从未实现）
    """
    # 坐标先转成 float —— 经纬度方框比较对非数值会直接 TypeError。
    # 这是**原有行为**（改动前同样会崩），但既然是"给出一个可用的区域类型"
    # 的兜底函数，崩掉没有意义。这里记为 warning 而不是静默吞掉：
    # 传入非法坐标本身是调用方的 bug，不应被掩盖。
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        logger.warning("classify_region 收到非法坐标 (%r, %r)，按 lowland 处理",
                       lat, lon)
        return "lowland"

    if use_terrain:
        try:
            from .terrain_lookup import lookup_relief
            mean_m, relief_m, _src = lookup_relief(lat, lon)
            if relief_m is not None:
                return classify_by_relief(mean_m, relief_m)
        except Exception as e:      # 任何异常都降级，绝不向上抛
            logger.info("地形查询不可用，回落到经纬度启发式: %s: %s",
                        type(e).__name__, e)
    return _classify_by_coords(lat, lon)


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

    该回归适用于中小型平原河涌；对大河（西江、北江等）不适用 ——
    那些河道应直接使用 KNOWN_RIVERS_GD 中已标定的 discharge_m3s。

    Args:
        catchment_area_km2: 集水面积 (km²)。**小于等于 0 表示"未知"**，
            此时返回 6.9 m³/s（雅瑶水道典型值，仅作占位）。

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
