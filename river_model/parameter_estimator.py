"""通用河段参数建议：使用当前输入，区分数据、经验假设和待核实参数，不代表自动率定。"""

import json
import logging
import math
import os
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .config import RiverConfig
from .geo_mapping import build_chainage_gps_mapping, _haversine
from .regional_defaults import (
    DEFAULT_CATCHMENT_KM2,
    TIDAL_AMPLITUDE_DEFAULT_M,
    classify_region,
    estimate_depth_from_width,
    estimate_discharge,
    lookup_default_width,
    lookup_known_river,
    lookup_mannings_n,
    lookup_slope,
)

logger = logging.getLogger(__name__)

# 中国各省份大致经纬度范围 (lat_min, lat_max, lon_min, lon_max, 省份, 典型城市)
#
# ⚠️ 这张表**只适合粗筛，不能直接取首个命中**：盒子过大且大量重叠，
#    先匹配者胜会大面积判错（实测石家庄->山东省、郴州->江西省、芜湖->江苏省）。
#    使用者若要据此决定"读哪个省的名录"，必须要求**唯一命中**，
#    见 trace_engine._unique_province_by_bbox。
PROVINCE_COORDS = [
    # 直辖市 (最小, 优先匹配)
    (30.5, 31.5, 121.0, 122.0, "上海市", "上海市"),
    (39.0, 41.0, 115.5, 117.5, "北京市", "北京市"),
    (38.5, 40.5, 116.5, 118.5, "天津市", "天津市"),
    (28.5, 30.5, 105.5, 107.5, "重庆市主城区", "重庆市"),
    # 小省份
    (35.5, 39.5, 118.5, 123.0, "山东省", "济南市/青岛市"),
    (31.0, 33.5, 114.0, 122.0, "江苏省", "南京市/苏州市"),
    (18.0, 20.5, 108.5, 111.5, "海南省", "海口市/三亚市"),
    (34.5, 39.0, 114.0, 122.5, "山东省", "济南市/青岛市"),
    # 中等省份
    (29.5, 31.5, 113.5, 115.5, "湖北省", "武汉市"),
    (27.5, 33.5, 114.0, 119.0, "安徽省", "合肥市"),
    (24.0, 30.5, 113.0, 118.5, "江西省", "南昌市"),
    (26.5, 29.5, 118.0, 121.0, "福建省", "福州市/厦门市"),
    (27.0, 31.5, 119.0, 123.0, "浙江省", "杭州市/宁波市"),
    (24.0, 30.5, 111.0, 114.5, "湖南省", "长沙市"),
    (31.0, 37.0, 110.5, 116.5, "河南省", "郑州市"),
    (29.5, 33.5, 108.5, 111.5, "湖北省", "武汉市"),
    (22.0, 26.0, 112.0, 117.5, "广东省", "广州市/深圳市"),
    (21.5, 26.5, 104.5, 112.0, "广西壮族自治区", "南宁市/桂林市"),
    (27.5, 30.5, 105.5, 110.5, "重庆市", "重庆市"),
    (26.0, 34.5, 97.0, 108.5, "四川省", "成都市"),
    (24.5, 29.5, 103.5, 109.5, "贵州省", "贵阳市"),
    (21.0, 29.5, 97.0, 106.5, "云南省", "昆明市"),
    # 大省份
    (32.0, 40.5, 104.5, 112.0, "陕西省", "西安市"),
    (36.0, 40.5, 110.0, 115.0, "山西省", "太原市"),
    (35.0, 42.5, 113.0, 120.0, "河北省", "石家庄市"),
    (33.0, 43.0, 95.0, 109.0, "甘肃省", "兰州市"),
    (39.0, 43.5, 118.5, 126.0, "辽宁省", "沈阳市/大连市"),
    (40.5, 45.5, 121.0, 131.5, "吉林省", "长春市"),
    (43.0, 53.5, 120.0, 136.0, "黑龙江省", "哈尔滨市"),
    (35.0, 47.0, 97.0, 127.0, "内蒙古自治区", "呼和浩特市"),
    (31.0, 40.0, 89.0, 100.0, "青海省", "西宁市"),
    (35.0, 40.0, 104.0, 108.0, "宁夏回族自治区", "银川市"),
    (26.5, 40.0, 73.0, 98.0, "新疆维吾尔自治区", "乌鲁木齐市"),
    (26.5, 37.0, 78.0, 100.0, "西藏自治区", "拉萨市"),
]


class RiverParameterEstimator:
    """从监测数据自动推断河道参数。

    用法:
        estimator = RiverParameterEstimator(amap_key="...", cloud_token="...")
        config = estimator.estimate(df)

    df 必须包含经度/纬度列 (或经度/纬度 / longitude/latitude)。
    """

    def __init__(self, amap_key: str = None, cloud_token: str = None, use_reference_database: bool = False,
                 enable_satellite: bool = False):
        self.amap_key = amap_key
        self.cloud_token = cloud_token
        self.use_reference_database = use_reference_database
        self.enable_satellite = enable_satellite
        self._sources: Dict[str, str] = {}      # {param_name: data_source}
        self._confidence: Dict[str, float] = {}  # {param_name: 0.0~1.0}

    # ==================================================================
    # 主入口
    # ==================================================================

    def estimate(self, df: pd.DataFrame) -> RiverConfig:
        """从监测数据 DataFrame 推断完整 RiverConfig。

        Args:
            df: 走航监测数据 DataFrame (含 GPS 列)

        Returns:
            完整填充的 RiverConfig
        """
        self._sources.clear()
        self._confidence.clear()

        # ---- Layer 1: GPS 直接提取 ----
        gps_info = self._extract_gps_info(df)
        if not gps_info:
            raise ValueError('至少需要三个有效 GPS 点，无法凭空确定河段位置和长度')
        gps_length = gps_info['total_length_m']
        center_lat = gps_info['center_lat']
        center_lon = gps_info['center_lon']
        gps_width = gps_info.get("estimated_width_m")
        gps_mapping = gps_info.get("gps_mapping")

        # ---- Layer 2: 河道识别 (OSM + Amap) ----
        river_identity = self._identify_river(df, center_lat, center_lon)
        river_name = river_identity.get("name", "未知河道")
        river_type = river_identity.get("waterway_type", "river")
        river_location = river_identity.get("location", "")

        # ---- Layer 2.5: 查询已知河道库 ----
        known = lookup_known_river(river_name) if self.use_reference_database else None
        if known:
            self._sources["__all__"] = "known_river_database"
            self._confidence["__all__"] = 0.95

        # ---- Layer 3: OSM 宽度 ----
        osm_width = river_identity.get("osm_width_m")

        # ---- Layer 4: 卫星宽度估计 (在 OSM 无宽度时尝试) ----
        sat_width_info = None
        if self.enable_satellite and osm_width is None and gps_width is None and self.cloud_token and gps_mapping:
            try:
                from .satellite_width import estimate_width_from_satellite
                sat_width_info = estimate_width_from_satellite(
                    gps_mapping, self.cloud_token, sample_spacing_m=100.0, max_samples=15
                )
            except Exception as e:
                logger.debug(f"卫星宽度估计失败: {e}")

        # ---- 综合确定河宽 ----
        width_m, width_source, width_conf = self._resolve_width(
            osm_width=osm_width,
            gps_width=gps_width,
            sat_width_info=sat_width_info,
            river_type=river_type,
            known=known,
        )

        # ---- Layer 5: 区域默认值 ----
        region = classify_region(center_lat, center_lon)

        # Manning's n
        mannings_n = (known or {}).get("mannings_n")
        mannings_source = "known_river_database" if mannings_n else "regional_defaults"
        mannings_conf = 0.90 if mannings_n else 0.50
        if mannings_n is None:
            channel_condition = "mixed" if river_type == "tidal_channel" else "clean_straight"
            mannings_n = lookup_mannings_n(river_type, channel_condition)

        # 纵向坡降
        slope = (known or {}).get("slope")
        slope_source = "known_river_database" if slope else "regional_defaults"
        slope_conf = 0.85 if slope else 0.50
        if slope is None:
            slope = lookup_slope(region, river_type)

        # 水深
        depth_m = (known or {}).get("depth_avg_m")
        depth_source = "known_river_database" if depth_m else "estimated_from_width"
        depth_conf = 0.90 if depth_m else 0.50
        if depth_m is None:
            depth_m = estimate_depth_from_width(width_m, river_type)

        # 派生流量须使用实际返回的情景几何与糙率，避免分别舍入造成自相矛盾。
        width_m, depth_m, mannings_n = round(width_m, 1), round(depth_m, 1), round(mannings_n, 3)
        # 流量
        discharge_m3s = (known or {}).get("discharge_m3s")
        discharge_source = "known_river_database" if discharge_m3s else "estimated"
        discharge_conf = 0.85 if discharge_m3s else 0.40
        if discharge_m3s is None:
            # 已知河道库中没有 discharge_m3s 时才会走到这里。
            # 集水面积同样取不到时用占位值 —— 这是**经验估算而非推断**,
            # 置信度已在下方标为 0.40, 调用方不应把它当成实测值。
            area = width_m * depth_m
            radius = area / (width_m + 2*depth_m)
            discharge_m3s = area * radius**(2/3) * np.sqrt(max(0., slope)) / mannings_n
            discharge_source = 'manning_geometry_estimate'

        # 潮汐边界
        tidal_amp = (known or {}).get("tidal_amplitude_m")
        is_tidal = river_identity.get("is_tidal", True)
        tidal_source = "known_river_database" if tidal_amp else "regional_defaults"
        tidal_conf = 0.90 if tidal_amp else 0.50
        if tidal_amp is None:
            # 注意: TIDAL_AMPLITUDE_BY_REGION 的 8 个具体河口键当前不可达,
            # 这里取的是统一的默认值。见 regional_defaults 中该表的说明。
            tidal_amp = TIDAL_AMPLITUDE_DEFAULT_M if is_tidal else 0.0

        # 水位基准
        high_water = (known or {}).get("high_water_mean_m", depth_m)
        low_water = (known or {}).get("low_water_mean_m", high_water)
        downstream_stage = (known or {}).get("downstream_stage_m", high_water)

        # ---- Layer 6: 监测数据推断 ----
        monitoring_info = self._infer_from_monitoring(df)

        # ---- 构建 RiverConfig ----
        # 记录来源和置信度
        self._sources.update({
            "river_name": river_identity.get("name_source", "osm"),
            "location": river_identity.get("location_source", "reverse_geocode"),
            "length_m": "gps",
            "width_avg_m": width_source,
            "depth_avg_m": depth_source,
            "mannings_n": mannings_source,
            "longitudinal_slope": slope_source,
            "avg_discharge_m3s": discharge_source,
            "tidal_range_m": tidal_source,
        })
        self._confidence.update({
            "river_name": river_identity.get("name_confidence", 0.7),
            "location": river_identity.get("location_confidence", 0.9),
            "length_m": 0.95,
            "width_avg_m": width_conf,
            "depth_avg_m": depth_conf,
            "mannings_n": mannings_conf,
            "longitudinal_slope": slope_conf,
            "avg_discharge_m3s": discharge_conf,
            "tidal_range_m": tidal_conf,
        })

        # 河底宽: 取河宽的 ~73% (梯形断面经验比例)
        bottom_width_m = width_m * 0.73

        # 床面高程: 区域典型值
        bed_elevation = (known or {}).get("bed_elevation_avg_m", 0.0)

        config = RiverConfig(
            # 河道基本信息
            river_name=river_name or "未知河道",
            location=river_location or "未知位置",
            catchment_area_km2=(known or {}).get("catchment_area_km2"),

            # 几何参数
            length_km=round(gps_length / 1000.0, 2),
            length_m=round(gps_length, 1),
            width_avg_m=round(width_m, 1),
            depth_avg_m=round(depth_m, 1),
            bed_elevation_avg_m=bed_elevation,
            bottom_width_m=round(bottom_width_m, 1),
            longitudinal_slope=slope,

            # 水力学参数
            mannings_n=round(mannings_n, 3),
            avg_discharge_m3s=round(discharge_m3s, 1),

            # 潮汐
            tidal_range_m=round(tidal_amp * 2, 1),
            high_water_mean_m=high_water,
            low_water_mean_m=low_water,

            # 水质基线 (来自监测数据)
            ammonia_nh3_mgL=monitoring_info.get("ammonia_baseline", 3.0),
            dissolved_oxygen_mgL=monitoring_info.get("do_baseline", 2.0),
            cod_mgL=monitoring_info.get("cod_baseline", 60.0),

            # 坐标参考
            center_lat=round(center_lat, 4),
            center_lon=round(center_lon, 4),

            # 元数据
            param_sources=dict(self._sources),
            param_confidence=dict(self._confidence),
        )

        return config

    # ==================================================================
    # Layer 1: GPS 信息提取
    # ==================================================================

    def _extract_gps_info(self, df: pd.DataFrame) -> dict:
        """从监测数据 DataFrame 提取 GPS 信息。

        使用 geo_mapping 模块建立 chainage-GPS 映射,
        并从原始 GPS 轨迹计算河道宽度下界。
        """
        # 提取坐标数组
        lats, lons = self._get_coords(df)
        if len(lats) < 3:
            return {}

        center_lat = float(np.mean(lats))
        center_lon = float(np.mean(lons))

        # 尝试通过 geo_mapping 获取长度
        # 直接计算 Haversine 累积距离
        if len(lats) >= 2:
            distances = _haversine(lons[:-1], lats[:-1], lons[1:], lats[1:])
            total_length_m = float(np.sum(distances))
        else:
            raise ValueError('有效 GPS 点不足')

        # GPS 横向展宽法估计河宽
        # 平滑中心线 + 计算原始点到中心线的垂直距离
        estimated_width = self._estimate_width_from_gps_spread(lats, lons, total_length_m)

        # 仅使用当前会话的 DataFrame，不读取固定样本或其它会话的上传文件。
        gps_mapping = None
        try:
            gps_mapping = build_chainage_gps_mapping(df, smoothing_sigma=3.0)
        except (ValueError, KeyError, TypeError):
            pass

        return {
            "total_length_m": total_length_m,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "lat_min": float(np.min(lats)),
            "lat_max": float(np.max(lats)),
            "lon_min": float(np.min(lons)),
            "lon_max": float(np.max(lons)),
            "estimated_width_m": estimated_width,
            "gps_mapping": gps_mapping,
            "n_points": len(lats),
        }

    def _estimate_width_from_gps_spread(
        self, lats: np.ndarray, lons: np.ndarray, total_length: float
    ) -> Optional[float]:
        """从 GPS 横向展宽估计河宽。

        计算每个 GPS 点到平滑中心线的垂直距离,
        取 95% 分位数 × 2 作为河宽下界估计。

        只有在展宽显著 (>15m) 时才认为存在真实的横向移动,
        否则返回 None (GPS 噪声, 非真实宽度)。
        """
        if len(lats) < 10:
            return None

        # 对原始坐标做轻度平滑得到近似中心线
        from scipy.ndimage import gaussian_filter1d
        try:
            lats_smooth = gaussian_filter1d(lats.astype(float), sigma=2.0)
            lons_smooth = gaussian_filter1d(lons.astype(float), sigma=2.0)
        except Exception:
            return None

        # 计算原始点到平滑中心线的垂直距离
        # 使用球面近似: dy (m) = dlat * 111000, dx (m) = dlon * 111000 * cos(lat)
        cos_lat = math.cos(math.radians(float(np.mean(lats))))
        lat2m = 111000.0
        lon2m = 111000.0 * cos_lat

        # 对每个点, 找最近的平滑中心线点
        n = len(lats)
        half_window = min(5, n // 3)

        cross_track_distances = []
        for i in range(n):
            # 局部中心线方向
            i_prev = max(0, i - half_window)
            i_next = min(n - 1, i + half_window)

            dy = lats_smooth[i_next] - lats_smooth[i_prev]
            dx = lons_smooth[i_next] - lons_smooth[i_prev]
            mag = math.sqrt((dy * lat2m) ** 2 + (dx * lon2m) ** 2)
            if mag < 1e-6:
                continue

            # 垂直单位向量
            perp_y = dx * lon2m / mag
            perp_x = -dy * lat2m / mag

            # 原始点到中心线的偏移
            offset_y = (lats[i] - lats_smooth[i]) * lat2m
            offset_x = (lons[i] - lons_smooth[i]) * lon2m

            # 垂直距离 = |offset · perp|
            cross_dist = abs(offset_y * perp_y + offset_x * perp_x)
            cross_track_distances.append(cross_dist)

        if not cross_track_distances:
            return None

        cross_track_distances = np.array(cross_track_distances)
        spread_95 = float(np.percentile(cross_track_distances, 95))

        # 只有在展宽 > 15m 时才认为有真实横向移动
        # (GPS 精度 ~3-5m, 振动噪声 ~5-10m)
        if spread_95 > 15.0:
            return spread_95 * 2.0  # 双边宽度
        return None

    # ==================================================================
    # Layer 2: 河道识别
    # ==================================================================

    def _identify_river(self, df: pd.DataFrame, center_lat: float, center_lon: float,
                        gps_coords: Optional[Tuple[np.ndarray, np.ndarray]] = None) -> dict:
        """识别河道身份: 名称、类型、位置。

        优先级: 已知河道坐标库 > OSM track match > Amap 逆地理 > 默认值

        Args:
            df: 监测数据 DataFrame；为 None 时须改用 gps_coords 提供轨迹。
            center_lat / center_lon: 搜索中心。
            gps_coords: (lats, lons) 数组，直接提供 GPS 轨迹。
                **走 gps_mapping 的调用方必须传这个** —— 否则下面取不到
                GPS 点，"已知河道坐标库"（置信度 0.95 的最高优先级来源）
                与"OSM 轨迹匹配"两层都会被整层跳过，河道识别只能退到
                单点 OSM 查询与高德逆地理。
        """
        result = {
            "name": None,
            "waterway_type": "river",
            "location": "",
            "osm_width_m": None,
            "is_tidal": True,
            "name_source": "unknown",
            "name_confidence": 0.0,
            "location_source": "unknown",
            "location_confidence": 0.0,
        }

        # ---- 提取 GPS 轨迹 ----
        if gps_coords is not None:
            gps_lats, gps_lons = gps_coords
        elif df is not None:
            gps_lats, gps_lons = self._get_coords(df)
        else:
            gps_lats, gps_lons = np.array([]), np.array([])

        # ---- 第0优先级: 已知河道坐标库 ----
        # 如果 GPS 坐标落入已知河道的 bbox, 直接采用该河道身份 (最高置信度)
        if len(gps_lats) >= 3:
            try:
                from .regional_defaults import match_known_river_by_coords
                known_match = match_known_river_by_coords(
                    list(gps_lats), list(gps_lons)
                )
                if known_match:
                    river_name, params = known_match
                    result["name"] = river_name
                    result["name_source"] = "known_river_database"
                    result["name_confidence"] = 0.95
                    result["location"] = params.get("location", "")
                    result["location_source"] = "known_river_database"
                    result["location_confidence"] = 0.95
                    result["waterway_type"] = "river"
                    result["is_tidal"] = params.get("tidal_amplitude_m", 0.5) > 0.1
                    return result
            except Exception:
                pass

        # ---- 第1优先级: OSM track-based matching ----
        if len(gps_lats) >= 10:
            try:
                from .osm_river_lookup import match_river_from_track
                osm_result = match_river_from_track(
                    list(gps_lats), list(gps_lons), radius_m=5000
                )
            except Exception:
                osm_result = None
        else:
            # GPS 点太少, 降级到单点查询
            try:
                from .osm_river_lookup import query_waterway_nearby
                osm_result = query_waterway_nearby(center_lat, center_lon, radius_m=5000)
            except Exception:
                osm_result = None

        if osm_result:
            osm_name = osm_result.get("name")
            if osm_name:
                # 验证 OSM 名称: 如果 OSM 返回小涌名称 (如 大坑涌)
                # 但已知河道库中有该区域的大河道, 优先用已知河道库
                known_by_name = lookup_known_river(osm_name)
                if not known_by_name and len(gps_lats) >= 3:
                    try:
                        from .regional_defaults import match_known_river_by_coords
                        known_by_coords = match_known_river_by_coords(
                            list(gps_lats), list(gps_lons)
                        )
                        if known_by_coords:
                            # OSM 名称为小涌, 但坐标在已知大河道范围内
                            # 优先用已知河道名称 (如 雅瑶水道)
                            result["name"] = known_by_coords[0]
                            result["name_source"] = "known_river_database"
                            result["name_confidence"] = 0.90
                            result["location"] = known_by_coords[1].get("location", "")
                            result["location_source"] = "known_river_database"
                            result["location_confidence"] = 0.90
                            result["waterway_type"] = osm_result.get("waterway_type", "river")
                            result["osm_width_m"] = osm_result.get("width_m")
                            tags = osm_result.get("tags", {})
                            result["is_tidal"] = (
                                tags.get("tidal") == "yes"
                                or self._is_near_coast(center_lat, center_lon)
                            )
                            return result
                    except Exception:
                        pass

                result["name"] = osm_name
                result["name_source"] = "osm"
                result["name_confidence"] = 0.80
            result["waterway_type"] = osm_result.get("waterway_type", "river")
            result["osm_width_m"] = osm_result.get("width_m")
            tags = osm_result.get("tags", {})
            result["is_tidal"] = (
                tags.get("tidal") == "yes"
                or self._is_near_coast(center_lat, center_lon)
            )

        # ---- 降级: Amap 逆地理编码 ----
        if not result["name"] and self.amap_key:
            try:
                # 跨包导入：项目根目录在 sys.path 上时用带 src 的绝对路径，
                # 标准 src-layout（src 在 sys.path 上）时用短路径。两种配置都要能跑。
                try:
                    from water_quality_mcp.src.water_quality_mcp.geo_tools import reverse_geocode
                except ImportError:
                    from water_quality_mcp.geo_tools import reverse_geocode

                geo_raw = reverse_geocode(center_lat, center_lon, self.amap_key)

                # reverse_geocode 返回的是 JSON 字符串（键为中文），不是 dict。
                # 此处曾经直接对它调用 .get("address") —— 抛 AttributeError 后被
                # 下面的裸 except 吞掉，导致"高德逆地理 → 识别河道名称"这条
                # 降级链从未生效过。解析失败（含"逆地理编码失败/异常"这类纯文本
                # 返回值）时静默跳过本分支即可。
                geo_result = None
                if isinstance(geo_raw, str):
                    try:
                        parsed = json.loads(geo_raw)
                        if isinstance(parsed, dict):
                            geo_result = parsed
                    except (json.JSONDecodeError, ValueError):
                        pass

                if geo_result:
                    address = geo_result.get("地址", "") or ""
                    province = geo_result.get("省份", "") or ""
                    city = geo_result.get("城市", "") or ""
                    district = geo_result.get("区县", "") or ""

                    result["location"] = (province + city + district)[:40]

                    if result["location"]:
                        result["location_source"] = "amap_reverse_geocode"
                        result["location_confidence"] = 0.90

                    # 智能水资源名称提取: 按优先级匹配
                    # 优先找大型河道名称 (含 水道/河/江/涌/渠)
                    #
                    # ⚠️ 该策略在高德真实返回上**误报率很高**，使用前须知：
                    # 高德 regeo 的 formatted_address 是**街道地址与场所名**
                    # （实测："广东省佛山市南海区狮山镇唐苑居江南园艺花场"），
                    # 其中"江/河"等字常是"江南""河畔"这类词的一部分。
                    # 原实现会从中抠出「唐苑居江」这样一个并不存在的水体名。
                    # 因此下面加了场所类字符的排除表（见 _NON_WATER_CHARS）。
                    #
                    # 更可靠的水体名来源是：已知河道坐标库 > OSM 水道匹配 >
                    # 高德 POI 搜索（按水体关键词）。本层只是最后的兜底猜测，
                    # 置信度也只给到 0.50/0.70。
                    water_keywords_ordered = [
                        "水道",  # 水道 优先 (如 雅瑶水道)
                        "河",    # 其次 (如 汾江河)
                        "江",    # (如 珠江、北江)
                        "涌",    # (如 大坑涌)
                        "渠",    # (如 排灌渠)
                        "湖",    # (如 千灯湖)
                    ]
                    # 出现以下任一字符的候选一律丢弃 —— 它们表明这一段是
                    # 场所/建筑/道路名而非水体名（苑居园场楼厦路号镇街公司厂店馆院校站）。
                    _NON_WATER_CHARS = "苑居园场楼厦路号镇街公司厂店馆院校站"
                    best_match = None
                    best_priority = 99  # 越小越好 (0=水道)

                    for keyword in water_keywords_ordered:
                        idx = address.find(keyword)
                        if idx >= 0:
                            # 向前搜索到上一个分隔符
                            start = max(0, idx - 10)
                            for sep in ["省", "市", "区", "县", "镇", "乡", "村", "路", "街", "大道", "号", "组"]:
                                sep_idx = address.rfind(sep, start, idx + len(keyword))
                                if sep_idx >= 0:
                                    start = sep_idx + 1
                            # 跳过地址前缀词
                            for prefix_word in ["广东省", "佛山市", "南海区", "广州市"]:
                                if address[start:].startswith(prefix_word):
                                    start += len(prefix_word)

                            maybe_name = address[start:idx + len(keyword)].strip()
                            # 三层过滤：长度合理 / 不含行政区划与道路词 /
                            # 不含场所建筑类字符（后者专治"唐苑居江"这类误报）
                            if (2 <= len(maybe_name) <= 12
                                    and not any(s in maybe_name for s in ["省", "市", "区", "镇", "路", "街"])
                                    and not any(ch in maybe_name for ch in _NON_WATER_CHARS)):
                                priority = water_keywords_ordered.index(keyword)
                                if priority < best_priority:
                                    best_priority = priority
                                    best_match = maybe_name

                    if best_match:
                        result["name"] = best_match
                        result["name_source"] = "amap_address"
                        # 高优先级词 (水道/河/江) 置信度更高
                        result["name_confidence"] = (
                            0.70 if best_priority <= 2 else 0.50
                        )
            except Exception as e:
                # 不阻断流程（本层只是降级选项之一），但要留下痕迹 ——
                # 原来的裸 pass 正是让上面那个类型错误潜伏至今的原因。
                logger.warning("高德逆地理识别河道失败（将降级到下一层）: %s: %s",
                               type(e).__name__, e)

        # 如果还是没有位置信息, 从 GPS 坐标推断省份
        if not result["location"]:
            inferred_loc = self._infer_location_from_coords(center_lat, center_lon, result.get("name") or "")
            if inferred_loc:
                result["location"] = inferred_loc
                result["location_source"] = "gps_coordinate_inference"
                result["location_confidence"] = 0.55
            else:
                result["location"] = "未知位置"
                result["location_source"] = "default"
                result["location_confidence"] = 0.10

        # 如果还没有名称, 设为 "未知河道"
        if not result["name"]:
            result["name"] = "未知河道"
            result["name_source"] = "default"
            result["name_confidence"] = 0.10

        return result

    # ==================================================================
    # Layer 6: 监测数据推断
    # ==================================================================

    def _infer_from_monitoring(self, df: pd.DataFrame) -> dict:
        """从传感器数据推断水质基线。"""
        result = {
            "ammonia_baseline": 3.0,
            "do_baseline": 2.0,
            "cod_baseline": 60.0,
        }

        col_map = {
            "氨氮(mg/L)": "ammonia", "氨氮": "ammonia",
            "ammonia_nitrogen": "ammonia",
            "溶解氧(mg/L)": "do", "溶解氧": "do",
            "dissolved_oxygen": "do",
            "化学需氧量(mg/L)": "cod", "cod": "cod",
        }
        df_mapped = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "ammonia" in df_mapped.columns:
            vals = pd.to_numeric(df_mapped["ammonia"], errors="coerce").dropna()
            if len(vals) > 0:
                result["ammonia_baseline"] = round(float(vals.median()), 1)

        if "do" in df_mapped.columns:
            vals = pd.to_numeric(df_mapped["do"], errors="coerce").dropna()
            if len(vals) > 0:
                result["do_baseline"] = round(float(vals.median()), 1)

        if "cod" in df_mapped.columns:
            vals = pd.to_numeric(df_mapped["cod"], errors="coerce").dropna()
            if len(vals) > 0:
                result["cod_baseline"] = round(float(vals.median()), 1)

        return result

    # ==================================================================
    # 工具方法
    # ==================================================================

    @staticmethod
    def _get_coords(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """从 DataFrame 中提取经纬度数组（含缺失值的行整行剔除）。

        **必须用同一个掩码同时过滤经纬度**，不能各自 dropna()：
        若纬度列第 3 行缺失、经度列第 7 行缺失，两次独立 dropna 会得到
        长度不同、且行与行不再对应的两个数组。后果分两种：

          - 长度不同：后续 Haversine 广播失败抛 ValueError，被
            tools.tool_river_init 的 except 捕获，用户看到"自动检测失败"，
            全部参数静默退回默认值
          - 长度恰好相同（更危险）：不报错，但河长、中心点、OSM 轨迹匹配
            全部基于错位的点对，结果失真且无从察觉

        geo_mapping.build_chainage_gps_mapping 用的是联合掩码的正确写法，
        此处此前是不一致的另一套。
        """
        col_map = {
            "经度": "lon", "纬度": "lat",
            "longitude": "lon", "latitude": "lat",
            "Longitude": "lon", "Latitude": "lat",
        }
        df_tmp = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "lat" not in df_tmp.columns or "lon" not in df_tmp.columns:
            return np.array([]), np.array([])

        lat_v = pd.to_numeric(df_tmp["lat"], errors="coerce")
        lon_v = pd.to_numeric(df_tmp["lon"], errors="coerce")
        valid = lat_v.notna() & lon_v.notna()
        return (lat_v[valid].to_numpy(dtype=float),
                lon_v[valid].to_numpy(dtype=float))

    @staticmethod
    def _is_near_coast(lat: float, lon: float, threshold_km: float = 50.0) -> bool:
        """粗略判断是否靠近海岸线 (用于潮汐影响判定)。

        ⚠️ 这是**近似判据**，不是真实海岸线距离。严谨做法是引入海岸线数据集
        （或全球潮汐模型）算距离；此处用沿海参考点近似，属已知局限。

        原先只有 5 个参考点，实测导致闽江口、辽河口、海河口、黄河口、
        长江口、杭州湾等**真实感潮河口被判为非潮汐**，进而 tidal_amplitude
        归零、潮汐边界整段丢失（见 tests/泛化能力检查.py 第 4 节）。
        现按中国海岸线自北向南补齐主要河口与海湾。

        阈值 50 km：参考点取在口门附近，足以覆盖其感潮河段。
        内陆站点距最近参考点均在 1000 km 以上（兰州约 1400 km、
        成都约 1500 km），不会误判。
        """
        coast_refs = [
            # ---- 渤海 / 黄海 ----
            (40.90, 121.80),  # 辽河口
            (39.00, 117.70),  # 海河口 / 渤海湾
            (38.00, 119.00),  # 黄河口 / 莱州湾
            (37.50, 122.50),  # 山东半岛北岸
            (36.00, 120.40),  # 青岛 / 胶州湾
            (34.50, 119.50),  # 连云港 / 海州湾
            # ---- 长江口 / 浙闽 ----
            (31.40, 121.50),  # 长江口
            (30.30, 121.00),  # 杭州湾
            (28.30, 121.60),  # 台州 / 温州
            (26.05, 119.60),  # 闽江口
            (24.50, 118.10),  # 厦门 / 泉州
            # ---- 华南 ----
            (23.00, 117.00),  # 汕头
            (22.50, 113.50),  # 珠江口
            (21.50, 111.00),  # 湛江
            (21.50, 109.10),  # 北部湾 / 北海
            (20.00, 110.30),  # 海南北部 / 琼州海峡
        ]
        for clat, clon in coast_refs:
            dlat = lat - clat
            dlon = lon - clon
            dist = math.sqrt(dlat ** 2 + (dlon * math.cos(math.radians(lat))) ** 2) * 111.0
            if dist < threshold_km:
                return True
        return False

    @staticmethod
    def _infer_location_from_coords(lat: float, lon: float, river_name: str = "") -> Optional[str]:
        """根据 GPS 坐标推断河道所在省份/城市。

        当逆地理编码不可用 (缺少 Amap Key 或调用失败) 时,
        用中国各省份的大致经纬度范围推断位置。

        对于已知河流名称, 也会结合河流名称的典型位置来校准。

        Args:
            lat: 纬度
            lon: 经度
            river_name: 河道名称 (可选, 如 "长江")

        Returns:
            "省名+城市名" 格式的位置字符串, 或 None
        """
        matches = {province for lat_min, lat_max, lon_min, lon_max, province, _ in PROVINCE_COORDS
                   if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max}
        return next(iter(matches)) + '（坐标范围估计，待核实）' if len(matches) == 1 else None

    @staticmethod
    def _resolve_width(
        osm_width: Optional[float],
        gps_width: Optional[float],
        sat_width_info: Optional[dict],
        river_type: str,
        known: Optional[dict],
    ) -> Tuple[float, str, float]:
        """综合多源数据确定最终河宽。

        优先级: 已知河道库 > OSM width 标签 > GPS 横向展宽 > 卫星估计 > 类型默认值
        """
        # 已知河道库
        if known and "width_avg_m" in known:
            return known["width_avg_m"], "known_river_database", 0.95

        # OSM width 标签
        if osm_width and osm_width > 0:
            return osm_width, "osm_width_tag", 0.85

        # GPS 横向展宽
        if gps_width and gps_width > 0:
            return gps_width, "gps_cross_track", 0.60

        # 卫星估计
        if sat_width_info and sat_width_info.get("width_avg_m", 0) > 0:
            return (
                sat_width_info["width_avg_m"],
                "satellite_imagery",
                sat_width_info.get("confidence", 0.40),
            )

        # 类型默认值
        default_w = lookup_default_width(river_type)
        return default_w, "type_default", 0.30


# ==================================================================
# 便捷函数 (供 app.py / MCP server 直接调用)
# ==================================================================

def estimate_config_from_data(
    df: pd.DataFrame,
    amap_key: str = None,
    cloud_token: str = None,
) -> RiverConfig:
    """从监测数据自动推断河道配置的便捷函数。

    Args:
        df: 走航监测数据 DataFrame
        amap_key: 高德 API Key (可选, 用于逆地理和 POI)
        cloud_token: 星图地球 Cloud Token (可选, 用于卫星宽度估计)

    Returns:
        完整填充的 RiverConfig
    """
    estimator = RiverParameterEstimator(amap_key=amap_key, cloud_token=cloud_token)
    return estimator.estimate(df)


def estimate_config_from_gps(gps_mapping: dict, amap_key=None, cloud_token=None) -> RiverConfig:
    """GPS 与 DataFrame 入口共享同一估计管道；缺失坐标时拒绝伪造位置。"""
    lats, lons = gps_mapping.get('lat_raw', []), gps_mapping.get('lon_raw', [])
    if len(lats) != len(lons) or len(lats) < 3:
        raise ValueError('GPS 映射需要至少三个成对的原始坐标')
    return estimate_config_from_data(pd.DataFrame({'latitude': lats, 'longitude': lons}),
                                     amap_key=amap_key, cloud_token=cloud_token)
