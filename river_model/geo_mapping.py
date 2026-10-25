"""桩号 ↔ GPS 坐标双向映射。

从走航船监测数据提取 GPS 轨迹, 计算 Haversine 累积距离作为桩号,
建立 chainage ↔ (latitude, longitude) 的双向插值映射。
"""

import logging

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d
from scipy.interpolate import interp1d
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _parse_time_column(series: pd.Series) -> pd.Series:
    """两遍解析时间列，兼容带秒与不带秒两种写法。

    ⚠️ 不能用裸的 `pd.to_datetime(series, errors='coerce')`：
    pandas 会**从首个非空值推断格式**并套用到整列。本项目的样本数据首个
    时间值是 "2025-08-01 10:33"（无秒），于是所有形如
    "2025-08-01 10:33:05"（带秒）的值全部解析失败变 NaT，
    再被 dropna 静默丢弃 —— 实测 1805 行只剩 151 行。

    必须显式指定格式、分两遍处理（loaders.py 用的是同样的写法）。
    """
    s = series.astype(str).str.strip()
    dt = pd.to_datetime(s, format='%Y-%m-%d %H:%M:%S', errors='coerce')
    mask = dt.isna()
    if mask.any():
        # 截到分钟再按 "%Y-%m-%d %H:%M" 解析
        dt[mask] = pd.to_datetime(s[mask].str.slice(0, 16),
                                  format='%Y-%m-%d %H:%M', errors='coerce')
    return dt


def _haversine(lon1: np.ndarray, lat1: np.ndarray,
               lon2: np.ndarray, lat2: np.ndarray) -> np.ndarray:
    """向量化 Haversine 距离计算 (m)。"""
    R = 6371000.0
    dlon = np.radians(lon2 - lon1)
    dlat = np.radians(lat2 - lat1)
    a = (np.sin(dlat / 2.0) ** 2
         + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2))
         * np.sin(dlon / 2.0) ** 2)
    return R * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def build_chainage_gps_mapping(excel_path,
                                smoothing_sigma: float = 3.0,
                                interp_resolution_m: float = 50.0,
                                ) -> dict:
    """从走航船 Excel 数据建立 chainage ↔ GPS 双向映射。

    Args:
        excel_path: 走航数据 Excel 文件路径或当前会话 DataFrame（不修改原表）
        smoothing_sigma: 高斯平滑 sigma (点数), 值越大越平滑。默认 3.0
        interp_resolution_m: 插值网格分辨率 (m)。默认 50m 一个点

    Returns:
        dict:
            "chainage_grid": np.ndarray (m,) — 均匀间距的桩号网格
            "lat_grid": np.ndarray (m,) — 对应纬度
            "lon_grid": np.ndarray (m,) — 对应经度
            "chainage_raw": np.ndarray (n,) — 原始 GPS 点的累积距离
            "lat_raw": np.ndarray (n,) — 平滑后的原始点纬度
            "lon_raw": np.ndarray (n,) — 平滑后的原始点经度
            "total_length_m": float — 河道总长度
            "point_count": int — 原始 GPS 点数
            "_chainage_to_lat": interp1d — 正向插值器
            "_chainage_to_lon": interp1d — 正向插值器
    """
    # 1. 加载数据
    df = excel_path.copy() if isinstance(excel_path, pd.DataFrame) else pd.read_excel(excel_path)

    # 列名映射 (与 loaders.py 一致)
    col_map = {
        '经度': 'lon', '纬度': 'lat',
        'longitude': 'lon', 'latitude': 'lat',
        'Longitude': 'lon', 'Latitude': 'lat',
    }
    df.rename(columns={k: v for k, v in col_map.items() if k in df.columns},
              inplace=True)

    n_before = len(df)

    # 如果有时序列, 按时序排列; 否则按数据顺序
    if '时间' in df.columns:
        df['time'] = _parse_time_column(df['时间'])
        df.dropna(subset=['time'], inplace=True)
        df.sort_values('time', inplace=True)
    if 'datetime' in df.columns:
        df.dropna(subset=['datetime'], inplace=True)
        df.sort_values('datetime', inplace=True)

    df.reset_index(drop=True, inplace=True)

    # 丢点必须出声。此前这里是静默的 —— 实测样本数据 1805 行只剩 151 行，
    # 桩号映射与河道长度全部基于这 8% 算出来，而没有任何提示。
    if len(df) < n_before:
        logger.warning(
            "GPS 映射：时间解析后从 %d 行减少到 %d 行（丢弃 %.1f%%）。"
            "若非预期，检查时间列的格式。",
            n_before, len(df), (1 - len(df) / max(n_before, 1)) * 100,
        )
    if len(df) < 2:
        raise ValueError(
            f"有效 GPS 点不足（{len(df)} 个）。时间列格式可能无法识别。"
        )

    # 2. 提取坐标
    lats = df['lat'].values.astype(float)
    lons = df['lon'].values.astype(float)

    # 去除 NaN
    valid = ~(np.isnan(lats) | np.isnan(lons))
    lats = lats[valid]
    lons = lons[valid]

    if len(lats) < 10:
        raise ValueError(f"GPS 数据点不足 ({len(lats)} 个)。需要至少 10 个有效坐标点。")

    # 3. 高斯平滑 (消除船晃动噪声)
    lats_smooth = gaussian_filter1d(lats.astype(float), sigma=smoothing_sigma)
    lons_smooth = gaussian_filter1d(lons.astype(float), sigma=smoothing_sigma)

    # 4. 计算相邻点 Haversine 距离, 累积得到 chainage
    distances = _haversine(lons_smooth[:-1], lats_smooth[:-1],
                            lons_smooth[1:], lats_smooth[1:])
    chainage_raw = np.zeros(len(lats_smooth))
    chainage_raw[1:] = np.cumsum(distances)

    total_length_m = float(chainage_raw[-1])

    # 5. 构建均匀间距插值网格 (向前采样以供动画使用)
    n_grid = max(int(total_length_m / interp_resolution_m) + 1, len(chainage_raw))
    chainage_grid = np.linspace(0, total_length_m, n_grid)

    # 线性插值器
    lat_interp = interp1d(chainage_raw, lats_smooth, kind='linear',
                           bounds_error=False, fill_value='extrapolate')
    lon_interp = interp1d(chainage_raw, lons_smooth, kind='linear',
                           bounds_error=False, fill_value='extrapolate')

    lat_grid = lat_interp(chainage_grid)
    lon_grid = lon_interp(chainage_grid)

    # 确保插值范围不超出原始数据边界
    lat_grid = np.clip(lat_grid, lats_smooth.min() - 0.001, lats_smooth.max() + 0.001)
    lon_grid = np.clip(lon_grid, lons_smooth.min() - 0.001, lons_smooth.max() + 0.001)

    return {
        "chainage_grid": chainage_grid,
        "lat_grid": lat_grid,
        "lon_grid": lon_grid,
        "chainage_raw": chainage_raw,
        "lat_raw": lats_smooth,
        "lon_raw": lons_smooth,
        "total_length_m": total_length_m,
        "point_count": len(lats_smooth),
        "_chainage_to_lat": lat_interp,
        "_chainage_to_lon": lon_interp,
    }


def checked_model_mapping(chainage, monitoring_data=None, alignment_config=None):
    """网页及导出动画共用的核对入口；不把返航累计距离当作河道桩号。"""
    from .cruise_alignment import audit_track, reference_geometry
    axis = np.asarray(chainage, dtype=float)
    if axis.ndim != 1 or len(axis) < 2 or not np.isfinite(axis).all() or np.any(np.diff(axis) <= 0) or axis[0] < 0:
        raise ValueError('模拟桩号轴无效')
    if alignment_config is not None:
        if (not isinstance(alignment_config, dict) or alignment_config.get('confirmed') is not True
                or not str(alignment_config.get('reference_note', '')).strip()):
            raise ValueError('须确认参考线方向并记录来源')
        reference, reference_axis = reference_geometry(alignment_config.get('reference_path'))
        if axis[-1] > reference_axis[-1] + 1e-6:
            raise ValueError('已配置参考线未覆盖全部模拟桩号，地图不缩放或外推；仍可查看沿程动画。')
        latitude = np.interp(axis, reference_axis, reference[:, 0])
        longitude = (np.interp(axis, reference_axis,
            np.degrees(np.unwrap(np.radians(reference[:, 1]))))+180) % 360-180
        length = float(reference_axis[-1])
        confirmation = 'user_supplied_not_independently_verified'
        notice = '按用户确认的参考线映射，不累计船的返航航程；参考线和流向仍需独立核实。'
    else:
        if monitoring_data is None or not {'latitude', 'longitude'} <= set(monitoring_data.columns):
            raise ValueError('当前项目没有有效走航轨迹，可查看沿程浓度动画。')
        if audit_track(monitoring_data)['warnings']:
            raise ValueError('走航轨迹存在返航、缺失坐标或时间等待核对项，请先配置参考线；累计航程不能直接作为河道桩号。')
        gps = build_chainage_gps_mapping(monitoring_data, smoothing_sigma=3.0)
        if gps['total_length_m'] <= 0 or axis[-1] > gps['total_length_m'] + 1e-6:
            raise ValueError('当前走航轨迹未覆盖模拟桩号范围；请核对河段长度、起点与方向。')
        latitude, longitude = chainage_array_to_gps(gps, axis)
        length = gps['total_length_m']
        confirmation = 'illustrative_boat_route_not_registered'
        notice = '桩号从当前走航起点累计映射；地图为示意，起点、方向及GPS需现场核实。'
    if not np.isfinite([latitude, longitude]).all():
        raise ValueError('轨迹映射包含无效坐标，请核实GPS。')
    # 兼容既有动画的映射结构；插值只在已经核对的模型范围内使用。
    unwrapped = np.degrees(np.unwrap(np.radians(longitude)))
    return {'chainage_grid': axis, 'lat_grid': latitude, 'lon_grid': longitude,
        'lat_raw': latitude, 'lon_raw': longitude, 'total_length_m': length,
        '_chainage_to_lat': lambda x: np.interp(x, axis, latitude),
        '_chainage_to_lon': lambda x: (np.interp(x, axis, unwrapped)+180) % 360-180,
        'confirmation': confirmation, 'notice': notice}


def chainage_to_gps(gps_mapping: dict, chainage_m: float) -> Tuple[float, float]:
    """将桩号转换为 GPS 坐标 (lat, lon)。"""
    lat = float(np.clip(gps_mapping["_chainage_to_lat"](chainage_m),
                         gps_mapping["lat_raw"].min() - 0.001,
                         gps_mapping["lat_raw"].max() + 0.001))
    lon = float(np.clip(gps_mapping["_chainage_to_lon"](chainage_m),
                         gps_mapping["lon_raw"].min() - 0.001,
                         gps_mapping["lon_raw"].max() + 0.001))
    return lat, lon


def chainage_array_to_gps(gps_mapping: dict,
                           chainage_array: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """将桩号数组批量转换为 GPS 坐标数组 (lats, lons)。"""
    lats = gps_mapping["_chainage_to_lat"](chainage_array)
    lons = gps_mapping["_chainage_to_lon"](chainage_array)
    lats = np.clip(lats,
                    gps_mapping["lat_raw"].min() - 0.001,
                    gps_mapping["lat_raw"].max() + 0.001)
    lons = np.clip(lons,
                    gps_mapping["lon_raw"].min() - 0.001,
                    gps_mapping["lon_raw"].max() + 0.001)
    return lats, lons


def get_gps_track_coords(gps_mapping: dict) -> list:
    """返回 Folium PolyLine 可用的坐标列表 [[lat, lon], ...]。
    使用插值网格, 保证轨迹平滑。
    """
    return list(zip(gps_mapping["lat_grid"].tolist(),
                    gps_mapping["lon_grid"].tolist()))


def get_map_center(gps_mapping: dict) -> Tuple[float, float]:
    """返回地图中心点 (lat, lon)。"""
    return (float(np.mean(gps_mapping["lat_raw"])),
            float(np.mean(gps_mapping["lon_raw"])))
