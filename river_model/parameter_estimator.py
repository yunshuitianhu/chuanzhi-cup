"""河道参数自发现编排器。

从走航监测数据自动推断所有河道模型参数。
使用多层降级策略:
  Layer 1: GPS 直接提取 (长度、坐标范围)
  Layer 2: Amap 逆地理编码 (河道名称、地址)
  Layer 3: OSM Overpass 查询 (水道类型、名称、宽度标签)
  Layer 4: 卫星影像宽度估计 (实验性)
  Layer 5: 区域默认值查表 (Manning's n, 坡降, 水深)
  Layer 6: 监测数据推断 (水质基线、污染源位置)

每层失败时自动降级到下一层, 确保始终能返回可用的参数集。
"""

import json
import logging
import math
import os
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .config import YayaoConfig
from .geo_mapping import build_chainage_gps_mapping, _haversine
from .regional_defaults import (
    classify_region,
    estimate_depth_from_width,
    estimate_discharge,
    lookup_default_width,
    lookup_known_river,
    lookup_mannings_n,
    lookup_slope,
)

logger = logging.getLogger(__name__)


class RiverParameterEstimator:
    """从监测数据自动推断河道参数。

    用法:
        estimator = RiverParameterEstimator(amap_key="...", cloud_token="...")
        config = estimator.estimate(df)

    df 必须包含经度/纬度列 (或经度/纬度 / longitude/latitude)。
    """

    def __init__(self, amap_key: str = None, cloud_token: str = None):
        self.amap_key = amap_key
        self.cloud_token = cloud_token
        self._sources: Dict[str, str] = {}      # {param_name: data_source}
        self._confidence: Dict[str, float] = {}  # {param_name: 0.0~1.0}

    # ==================================================================
    # 主入口
    # ==================================================================

    def estimate(self, df: pd.DataFrame) -> YayaoConfig:
        """从监测数据 DataFrame 推断完整 YayaoConfig。

        Args:
            df: 走航监测数据 DataFrame (含 GPS 列)

        Returns:
            完整填充的 YayaoConfig
        """
        self._sources.clear()
        self._confidence.clear()

        # ---- Layer 1: GPS 直接提取 ----
        gps_info = self._extract_gps_info(df)
        gps_length = gps_info.get("total_length_m", 3200.0)
        center_lat = gps_info.get("center_lat", 23.11)
        center_lon = gps_info.get("center_lon", 113.14)
        gps_width = gps_info.get("estimated_width_m")
        gps_mapping = gps_info.get("gps_mapping")

        # ---- Layer 2: 河道识别 (OSM + Amap) ----
        river_identity = self._identify_river(df, center_lat, center_lon)
        river_name = river_identity.get("name", "未知河道")
        river_type = river_identity.get("waterway_type", "river")
        river_location = river_identity.get("location", "")

        # ---- Layer 2.5: 查询已知河道库 ----
        known = lookup_known_river(river_name)
        if known:
            self._sources["__all__"] = "known_river_database"
            self._confidence["__all__"] = 0.95

        # ---- Layer 3: OSM 宽度 ----
        osm_width = river_identity.get("osm_width_m")

        # ---- Layer 4: 卫星宽度估计 (在 OSM 无宽度时尝试) ----
        sat_width_info = None
        if osm_width is None and gps_width is None and self.cloud_token and gps_mapping:
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

        # 流量
        discharge_m3s = (known or {}).get("discharge_m3s")
        discharge_source = "known_river_database" if discharge_m3s else "estimated"
        discharge_conf = 0.85 if discharge_m3s else 0.40
        if discharge_m3s is None:
            catchment = (known or {}).get("catchment_area_km2", 122.8)
            discharge_m3s = estimate_discharge(catchment)

        # 潮汐边界
        tidal_amp = (known or {}).get("tidal_amplitude_m")
        is_tidal = river_identity.get("is_tidal", True)
        tidal_source = "known_river_database" if tidal_amp else "regional_defaults"
        tidal_conf = 0.90 if tidal_amp else 0.50
        if tidal_amp is None:
            if is_tidal:
                from .regional_defaults import TIDAL_AMPLITUDE_BY_REGION
                tidal_amp = TIDAL_AMPLITUDE_BY_REGION.get("default", 0.8)
            else:
                tidal_amp = 0.0

        # 水位基准
        high_water = (known or {}).get("high_water_mean_m", 0.83)
        low_water = (known or {}).get("low_water_mean_m", -0.52)
        downstream_stage = (known or {}).get("downstream_stage_m", high_water)

        # ---- Layer 6: 监测数据推断 ----
        monitoring_info = self._infer_from_monitoring(df)

        # ---- 构建 YayaoConfig ----
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
        bed_elevation = (known or {}).get("bed_elevation_avg_m", -2.0)

        config = YayaoConfig(
            # 河道基本信息
            river_name=river_name or "未知河道",
            location=river_location or "未知位置",
            catchment_area_km2=(known or {}).get("catchment_area_km2", 122.8),

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
            return {"total_length_m": 3200.0, "center_lat": 23.11, "center_lon": 113.14}

        center_lat = float(np.mean(lats))
        center_lon = float(np.mean(lons))

        # 尝试通过 geo_mapping 获取长度
        # 直接计算 Haversine 累积距离
        if len(lats) >= 2:
            distances = _haversine(lons[:-1], lats[:-1], lons[1:], lats[1:])
            total_length_m = float(np.sum(distances))
        else:
            total_length_m = 3200.0

        # GPS 横向展宽法估计河宽
        # 平滑中心线 + 计算原始点到中心线的垂直距离
        estimated_width = self._estimate_width_from_gps_spread(lats, lons, total_length_m)

        # 尝试构建 gps_mapping (需要先保存到临时文件)
        gps_mapping = None
        try:
            # 如果有 _uploaded_data.xlsx, 用它; 否则尝试用原始数据路径
            excel_candidates = [
                "_uploaded_data.xlsx",
                "苗圃杯/无人船智能水质监测分析和因果溯源样本数据/河道巡航走航样本数据.xlsx",
            ]
            for excel_path in excel_candidates:
                if os.path.exists(excel_path):
                    gps_mapping = build_chainage_gps_mapping(excel_path, smoothing_sigma=3.0)
                    if gps_mapping:
                        total_length_m = gps_mapping.get("total_length_m", total_length_m)
                    break
        except Exception:
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

    def _identify_river(self, df: pd.DataFrame, center_lat: float, center_lon: float) -> dict:
        """识别河道身份: 名称、类型、位置。

        优先级: 已知河道坐标库 > OSM track match > Amap 逆地理 > 默认值
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
        gps_lats, gps_lons = self._get_coords(df) if df is not None else (np.array([]), np.array([]))

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
                from water_quality_mcp.src.water_quality_mcp.geo_tools import reverse_geocode
                geo_result = reverse_geocode(center_lat, center_lon, self.amap_key)
                if geo_result:
                    address = geo_result.get("address", "") or geo_result.get("formatted_address", "")
                    province = geo_result.get("province", "")
                    city = geo_result.get("city", "")
                    district = geo_result.get("district", "")

                    result["location"] = (province + city + (district or ""))[:40]

                    if result["location"]:
                        result["location_source"] = "amap_reverse_geocode"
                        result["location_confidence"] = 0.90

                    # 智能水资源名称提取: 按优先级匹配
                    # 优先找大型河道名称 (含 水道/河/江/涌/渠)
                    water_keywords_ordered = [
                        "水道",  # 水道 优先 (如 雅瑶水道)
                        "河",    # 其次 (如 汾江河)
                        "江",    # (如 珠江、北江)
                        "涌",    # (如 大坑涌)
                        "渠",    # (如 排灌渠)
                        "湖",    # (如 千灯湖)
                    ]
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
                            # 确保名称不包含分隔符且长度合理
                            if 2 <= len(maybe_name) <= 12 and not any(
                                s in maybe_name for s in ["省", "市", "区", "镇", "路", "街"]
                            ):
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
            except Exception:
                pass

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
        """从 DataFrame 中提取经纬度数组。"""
        col_map = {
            "经度": "lon", "纬度": "lat",
            "longitude": "lon", "latitude": "lat",
            "Longitude": "lon", "Latitude": "lat",
        }
        df_tmp = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        if "lat" not in df_tmp.columns or "lon" not in df_tmp.columns:
            return np.array([]), np.array([])

        lats = pd.to_numeric(df_tmp["lat"], errors="coerce").dropna().values.astype(float)
        lons = pd.to_numeric(df_tmp["lon"], errors="coerce").dropna().values.astype(float)
        return lats, lons

    @staticmethod
    def _is_near_coast(lat: float, lon: float, threshold_km: float = 50.0) -> bool:
        """粗略判断是否靠近海岸线 (用于潮汐影响判定)。

        中国东南沿海大致海岸线参考点。
        """
        # 珠江口附近粗略判断: lon > 112.5 且 lat < 23.6
        coast_refs = [
            (22.5, 113.5),   # 珠江口
            (23.0, 117.0),   # 汕头
            (21.5, 111.0),   # 湛江
            (25.0, 119.0),   # 福州
            (30.0, 122.0),   # 上海/杭州湾
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
        # ---- 第1层: 河流名称校准 ----
        # 如果河流名称明确指向某一地区, 使用该地区的城市
        _RIVER_REGION_HINTS: dict[str, str] = {
            "长江": "湖北省武汉市/重庆市/江苏省南京市",
            "汉江": "湖北省武汉市",
            "湘江": "湖南省长沙市",
            "赣江": "江西省南昌市",
            "黄河": "河南省/山东省",
            "珠江": "广东省广州市",
            "淮河": "安徽省/江苏省",
            "松花江": "黑龙江省哈尔滨市",
            "辽河": "辽宁省",
            "海河": "天津市/河北省",
            "钱塘江": "浙江省杭州市",
            "闽江": "福建省福州市",
            "嘉陵江": "重庆市",
            "岷江": "四川省乐山市/宜宾市",
            "乌江": "贵州省/重庆市",
            "雅砻江": "四川省",
            "怒江": "云南省",
            "澜沧江": "云南省",
            "雅鲁藏布江": "西藏自治区",
        }
        # 完整匹配
        if river_name in _RIVER_REGION_HINTS:
            return _RIVER_REGION_HINTS[river_name]
        # 部分匹配 (如 "长江干流武汉段" → 匹配到 "长江")
        if river_name:
            for rn, loc in _RIVER_REGION_HINTS.items():
                if rn in river_name:
                    return loc

        # ---- 第2层: GPS 坐标推断省份 ----
        # 中国各省份大致经纬度范围 (按面积从小到大排序, 先匹配小省份避免被大省吞掉)
        # (lat_min, lat_max, lon_min, lon_max, 省份, 典型城市)
        _PROVINCE_COORDS = [
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

        for lat_min, lat_max, lon_min, lon_max, province, cities in _PROVINCE_COORDS:
            if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
                return f"{province}{cities}"

        # ---- 第3层: 粗略大区推断 ----
        if lat >= 40.0:
            return "东北/华北地区"
        elif lat >= 30.0:
            return "华中/华东地区"
        elif lat >= 22.0:
            return "华南地区"
        elif lat >= 18.0:
            return "华南/西南地区"
        else:
            return None

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
) -> YayaoConfig:
    """从监测数据自动推断河道配置的便捷函数。

    Args:
        df: 走航监测数据 DataFrame
        amap_key: 高德 API Key (可选, 用于逆地理和 POI)
        cloud_token: 星图地球 Cloud Token (可选, 用于卫星宽度估计)

    Returns:
        完整填充的 YayaoConfig
    """
    estimator = RiverParameterEstimator(amap_key=amap_key, cloud_token=cloud_token)
    return estimator.estimate(df)


def estimate_config_from_gps(
    gps_mapping: dict,
    amap_key: str = None,
    cloud_token: str = None,
) -> YayaoConfig:
    """从 GPS 映射字典自动推断河道配置。

    适用于已有 gps_mapping 但无完整 DataFrame 的场景。

    Args:
        gps_mapping: geo_mapping.build_chainage_gps_mapping() 返回的字典
        amap_key: 高德 API Key
        cloud_token: 星图地球 Cloud Token

    Returns:
        YayaoConfig (使用 GPS 长度, 其余参数用默认值)
    """
    estimator = RiverParameterEstimator(amap_key=amap_key, cloud_token=cloud_token)

    total_length = gps_mapping.get("total_length_m", 3200.0)
    center_lat = float(np.mean(gps_mapping.get("lat_raw", [23.11])))
    center_lon = float(np.mean(gps_mapping.get("lon_raw", [113.14])))

    # 从 gps_mapping 推断河宽
    lats = gps_mapping.get("lat_raw", np.array([]))
    lons = gps_mapping.get("lon_raw", np.array([]))
    if len(lats) > 10:
        gps_width = estimator._estimate_width_from_gps_spread(
            np.array(lats), np.array(lons), total_length
        )
    else:
        gps_width = None

    # 河道识别
    river_identity = estimator._identify_river(None, center_lat, center_lon)
    river_type = river_identity.get("waterway_type", "river")

    # 解算宽度
    width_m, width_src, width_conf = estimator._resolve_width(
        osm_width=river_identity.get("osm_width_m"),
        gps_width=gps_width,
        sat_width_info=None,
        river_type=river_type,
        known=lookup_known_river(river_identity.get("name", "")),
    )

    region = classify_region(center_lat, center_lon)
    depth_m = estimate_depth_from_width(width_m, river_type)
    mannings_n = lookup_mannings_n(river_type)
    slope = lookup_slope(region, river_type)
    discharge = estimate_discharge(122.8)

    is_tidal = river_identity.get("is_tidal", True)
    tidal_amp = 0.8 if is_tidal else 0.0

    return YayaoConfig(
        river_name=river_identity.get("name", "未知河道"),
        location=river_identity.get("location", ""),
        length_km=round(total_length / 1000, 2),
        length_m=round(total_length, 1),
        width_avg_m=round(width_m, 1),
        depth_avg_m=round(depth_m, 1),
        bottom_width_m=round(width_m * 0.73, 1),
        mannings_n=round(mannings_n, 3),
        longitudinal_slope=slope,
        avg_discharge_m3s=round(discharge, 1),
        tidal_range_m=round(tidal_amp * 2, 1),
        center_lat=round(center_lat, 4),
        center_lon=round(center_lon, 4),
        param_sources={
            "length_m": "gps",
            "width_avg_m": width_src,
            "depth_avg_m": "estimated_from_width",
            "mannings_n": "regional_defaults",
            "longitudinal_slope": "regional_defaults",
        },
        param_confidence={
            "length_m": 0.95,
            "width_avg_m": width_conf,
            "depth_avg_m": 0.50,
            "mannings_n": 0.50,
            "longitudinal_slope": 0.50,
        },
    )
