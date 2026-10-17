import base64
import json
import math
from collections import Counter
from datetime import timedelta
from typing import TYPE_CHECKING

import cv2
import numpy as np
import pandas as pd
from scipy.stats import pearsonr
from sklearn.cluster import DBSCAN
from .data_quality import measurement_error, assessment_mask

if TYPE_CHECKING:
    from .analysis_config import AnalysisConfig

# ============================================================
#  黑臭水体判定 — 依据《城市黑臭水体整治工作指南》(2015)
#  关键指标: 氨氮、溶解氧、透明度、氧化还原电位
#  目前走航数据仅有氨氮和溶解氧两项关键指标
# ============================================================

_MISSING_INDICATORS = ["透明度 (SD)", "氧化还原电位 (ORP)"]
_AVAILABLE_INDICATORS = ["氨氮 (NH₃-N)", "溶解氧 (DO)"]


def _saturated_do(temp_c):
    """饱和溶解氧浓度 (mg/L). Cf = 468 / (31.6 + T)."""
    return 468.0 / (31.6 + np.clip(temp_c, 0, 40))


def _get_target_limits(config=None):
    """获取目标水质等级限值。

    Args:
        config: AnalysisConfig 实例或 None (回退 V 类)。

    Returns:
        dict: {"nh3": 2.0, "do": 2.0, "cod": 40.0, "turb": 200.0, "ph": 1.5}
    """
    if config is not None:
        limits = config.get_target_limits()
        return {
            "nh3": limits.get("nh3", 2.0),
            "do": limits.get("do", 2.0),
            "cod": limits.get("cod", 40.0),
            "turb": 200.0,  # 浊度无GB3838标准限值, 保持经验值
            "ph": 1.5,       # pH偏移容忍度
        }
    # 回退 V 类默认值
    return {"nh3": 2.0, "do": 2.0, "cod": 40.0, "turb": 200.0, "ph": 1.5}


def calc_black_odor_single_factor(df, config=None):
    """单因子污染指数法。

    基准根据 config.target_water_class 动态切换 (默认 V 类):
      氨氮:       Pi = Ci / 限值         (V类 2.0, IV类 1.5, III类 1.0 ...)
      溶解氧:     Pi = (Cf - Ci) / (Cf - 限值)
      COD(辅助):  Pi = Ci / 限值         (V类 40, IV类 30, III类 20 ...)
      浊度(辅助): Pi = Ci / 200.0
      pH(辅助):   Pi = |Ci - 7.5| / 1.5

    Args:
        df: 监测数据 DataFrame
        config: AnalysisConfig 或 None (None 时回退 V 类)
    """
    limits = _get_target_limits(config)
    pi = pd.DataFrame(index=df.index)

    if 'ammonia_nitrogen' in df.columns:
        pi['nh3'] = df['ammonia_nitrogen'].fillna(0).clip(0) / limits["nh3"]

    if 'dissolved_oxygen' in df.columns:
        temp = df['water_temperature'] if 'water_temperature' in df.columns else pd.Series(20.0, index=df.index)
        cf = _saturated_do(temp.fillna(20))
        do = df['dissolved_oxygen'].fillna(cf)
        pi['do'] = np.clip((cf - do) / np.maximum(cf - limits["do"], 0.01), 0, 20)

    if 'cod' in df.columns:
        pi['cod'] = df['cod'].fillna(0).clip(0) / limits["cod"]

    if 'turbidity' in df.columns:
        pi['turb'] = df['turbidity'].fillna(0).clip(0) / limits["turb"]

    if 'ph' in df.columns:
        pi['ph'] = np.abs(df['ph'].fillna(7.5) - 7.5) / limits["ph"]

    return pi


def calc_nemerow_index(pi_df):
    """内梅罗综合污染指数（相对 GB3838-2002 V类限值）。

    P_综合 = sqrt((P_avg² + P_max²) / 2)

    分级 (Pi 基准 = V类限值):
      < 1      清洁 (达V类)
      1 ~ 2    轻污染 (超V类)
      2 ~ 3    污染
      3 ~ 5    重污染
      > 5      严重污染
    """
    if pi_df.empty or pi_df.shape[1] == 0:
        return np.zeros(len(pi_df))
    p_avg = pi_df.mean(axis=1).values
    p_max = pi_df.max(axis=1).values
    return np.sqrt((p_avg ** 2 + p_max ** 2) / 2.0)


def calc_black_score_vectorized(df, config=None):
    """黑点连续评分 — 使用内梅罗综合污染指数。

    超标基准根据 config.target_water_class 动态切换 (默认 V 类限值)。
    供 DBSCAN 聚类和排序使用。

    返回值语义:
      < 1.0  → 达标
      1.0-2.0 → 轻污染
      2.0-3.0 → 中污染
      3.0-5.0 → 重污染
      > 5.0  → 严重污染

    Args:
        df: 监测数据 DataFrame
        config: AnalysisConfig 或 None
    """
    return calc_nemerow_index(calc_black_odor_single_factor(df, config))


def get_severity_info(score, config=None):
    """基于内梅罗指数返回 (等级标识, 中文标签, 十六进制颜色)。

    Args:
        score: 内梅罗综合指数值
        config: AnalysisConfig 或 None
    """
    if config is not None:
        grades = config.get_nemerow_grades()
    else:
        from .analysis_config import NEMEROW_GRADE_THRESHOLDS
        grades = NEMEROW_GRADE_THRESHOLDS

    for threshold, level_id, label, color in grades:
        if score < threshold:
            return level_id, label, color
    return "critical", "严重污染", "#8B0000"


def _determine_black_odor_vectorized(df, config=None):
    """按《城市黑臭水体整治工作指南》(2015) 阈值判定每个采样点的黑臭等级。

    阈值从 config.black_odor_thresholds 读取 (config=None 时使用内置默认值):
      氨氮 ≤ mild.nh3(8.0)  且 DO ≥ mild.do(2.0)  → 无黑臭
      氨氮 mild.nh3~severe.nh3(15) 或 DO severe.do(0.2)~mild.do(2.0) → 轻度黑臭
      氨氮 > severe.nh3(15) 或 DO < severe.do(0.2) → 重度黑臭

    Args:
        df: 监测数据 DataFrame
        config: AnalysisConfig 或 None

    返回: (levels, labels, colors) 三个等长 list
    """
    if config is not None:
        thresholds = config.get_black_odor_thresholds()
    else:
        from .analysis_config import BLACK_ODOR_THRESHOLDS
        thresholds = BLACK_ODOR_THRESHOLDS

    mild_nh3 = thresholds["mild"]["nh3"]
    severe_nh3 = thresholds["severe"]["nh3"]
    mild_do = thresholds["mild"]["do"]
    severe_do = thresholds["severe"]["do"]

    n = len(df)
    is_severe = np.zeros(n, dtype=bool)
    is_mild = np.zeros(n, dtype=bool)

    if 'ammonia_nitrogen' in df.columns:
        nh3 = df['ammonia_nitrogen'].fillna(0).values
        is_severe |= (nh3 > severe_nh3)
        is_mild |= ((nh3 > mild_nh3) & ~is_severe)

    if 'dissolved_oxygen' in df.columns:
        do = df['dissolved_oxygen'].fillna(20).values
        is_severe |= (do < severe_do)
        is_mild |= ((do < mild_do) & (do >= severe_do) & ~is_severe)

    levels = np.where(is_severe, 'critical',
                      np.where(is_mild, 'mild', 'normal'))

    labels = np.where(is_severe, '重度黑臭',
                      np.where(is_mild, '轻度黑臭', '无黑臭'))

    colors = np.where(is_severe, '#8B0000',
                      np.where(is_mild, '#FFA500', '#2E8B57'))

    return list(levels), list(labels), list(colors)


# ---------- 聚类辅助 ----------

def _get_trigger_indicators(row, config=None):
    """返回触发黑臭判定的指标列表。

    Args:
        row: DataFrame 行
        config: AnalysisConfig 或 None
    """
    if config is not None:
        thresholds = config.get_black_odor_thresholds()
    else:
        from .analysis_config import BLACK_ODOR_THRESHOLDS
        thresholds = BLACK_ODOR_THRESHOLDS

    mild_nh3 = thresholds["mild"]["nh3"]
    severe_nh3 = thresholds["severe"]["nh3"]
    mild_do = thresholds["mild"]["do"]
    severe_do = thresholds["severe"]["do"]

    triggers = []
    nh3 = row.get('ammonia_nitrogen')
    do_val = row.get('dissolved_oxygen')
    if pd.notna(nh3) and nh3 > mild_nh3:
        sev = '重度' if nh3 > severe_nh3 else '轻度'
        triggers.append(f"氨氮={nh3:.1f}mg/L({sev})")
    if pd.notna(do_val) and do_val < mild_do:
        sev = '重度' if do_val < severe_do else '轻度'
        triggers.append(f"DO={do_val:.2f}mg/L({sev})")
    return triggers


def get_cluster_summary(cluster_df, cid, config=None):
    """单聚类摘要，控制在 500 字符内供 LLM 消费。

    Args:
        cluster_df: 聚类内的数据子集
        cid: 聚类 ID
        config: AnalysisConfig 或 None
    """
    avg_score = round(float(cluster_df['black_score'].mean()), 2)
    # 黑臭等级: 取最严重成员点的等级 (与官方"任一指标不达标即判定"原则一致)
    _BO_LABEL = {'critical': '重度黑臭', 'severe': '重度黑臭', 'mild': '轻度黑臭', 'normal': '无黑臭'}
    sev_order = {'critical': 4, 'severe': 3, 'moderate': 2, 'mild': 1, 'normal': 0}
    if 'severity' in cluster_df.columns:
        worst = max(cluster_df['severity'], key=lambda x: sev_order.get(x, 0))
        black_odor_level = _BO_LABEL.get(worst, '未知')
    else:
        black_odor_level = '未知'
    # 内梅罗污染等级
    _, nemerow_label, _ = get_severity_info(avg_score, config)
    # 目标水质标签
    target_class = config.target_water_class if config else "V"
    info = {
        "cluster_id": int(cid),
        "count": len(cluster_df),
        "center_lat": round(float(cluster_df['latitude'].mean()), 6),
        "center_lon": round(float(cluster_df['longitude'].mean()), 6),
        "内梅罗指数_均值": avg_score,
        "内梅罗等级": nemerow_label,
        "黑臭等级": black_odor_level,
        "目标水质": f"GB3838-2002 {target_class}类",
        "判定标准": "单因子+内梅罗: GB3838-2002 | 黑臭判定: 《城市黑臭水体整治工作指南》(2015)",
    }
    # 关键指标统计
    pollutants = {}
    if 'ammonia_nitrogen' in cluster_df.columns:
        nh3_mean = float(cluster_df['ammonia_nitrogen'].mean())
        nh3_max = float(cluster_df['ammonia_nitrogen'].max())
        pollutants['氨氮'] = f"均值{nh3_mean:.1f}/峰值{nh3_max:.1f} mg/L"
    if 'dissolved_oxygen' in cluster_df.columns:
        do_mean = float(cluster_df['dissolved_oxygen'].mean())
        do_min = float(cluster_df['dissolved_oxygen'].min())
        pollutants['DO'] = f"均值{do_mean:.2f}/最低{do_min:.2f} mg/L"
    if 'cod' in cluster_df.columns:
        pollutants['COD'] = f"均值{float(cluster_df['cod'].mean()):.0f} mg/L"
    if 'turbidity' in cluster_df.columns:
        pollutants['浊度'] = f"均值{float(cluster_df['turbidity'].mean()):.1f} NTU"
    info["指标统计"] = pollutants

    # 触发黑臭的主要指标 (按指标聚合, 非按具体值)
    all_triggers = []
    for _, row in cluster_df.iterrows():
        all_triggers.extend(_get_trigger_indicators(row, config))
    if all_triggers:
        indicator_counts = Counter()
        for t in all_triggers:
            indicator = '氨氮' if t.startswith('氨氮') else 'DO' if t.startswith('DO') else t
            indicator_counts[indicator] += 1
        info["主要触发指标"] = [f"{ind}({cnt}次)" for ind, cnt in indicator_counts.most_common(3)]

    # ---- 时空维度字段 ----
    if 'datetime' in cluster_df.columns:
        times = cluster_df['datetime']
        info["持续超标时长_min"] = round((times.max() - times.min()).total_seconds() / 60.0, 1)
        # 趋势判定: 基于 black_score 的线性回归斜率
        if len(cluster_df) >= 3:
            x = (times - times.min()).dt.total_seconds().values.astype(float)
            y = cluster_df['black_score'].values.astype(float)
            if np.std(x) > 0:
                slope = np.polyfit(x, y, 1)[0]
                per_hour = slope * 3600.0  # 每小时变化率
                if per_hour > 0.5:
                    info["趋势"] = "恶化"
                elif per_hour < -0.5:
                    info["趋势"] = "改善"
                else:
                    info["趋势"] = "稳定"

    if 'latitude' in cluster_df.columns and 'longitude' in cluster_df.columns:
        lats, lons = cluster_df['latitude'].values, cluster_df['longitude'].values
        spatial_span = _haversine_distance_m(lats[0], lons[0], lats[-1], lons[-1])
        info["空间跨度_m"] = round(float(spatial_span), 1)

    # 超标倍数范围
    if 'black_score' in cluster_df.columns:
        info["超标倍数范围"] = f"{cluster_df['black_score'].min():.1f} ~ {cluster_df['black_score'].max():.1f}"
    if 'nemerow_raw' in cluster_df.columns:
        info["原始内梅罗范围"] = f"{cluster_df['nemerow_raw'].min():.2f} ~ {cluster_df['nemerow_raw'].max():.2f}"

    return info


def get_cluster_extent(cluster_df):
    """聚类空间范围：基于 3σ 的圆半径 (米)，钳制在 50-800m。"""
    lats = cluster_df['latitude'].values
    lons = cluster_df['longitude'].values
    clat, clon = float(lats.mean()), float(lons.mean())
    d_lat_m = float(lats.std()) * 111_000
    d_lon_m = float(lons.std()) * 111_000 * math.cos(math.radians(clat))
    radius = max(50, min(800, 3.0 * max(d_lat_m, d_lon_m)))
    return clat, clon, radius


# ============================================================
#  时空维度黑点检测增强
# ============================================================

def _haversine_distance_m(lat1, lon1, lat2, lon2):
    """Haversine 距离 (米)。支持标量和数组。"""
    R = 6371000.0
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlon / 2) ** 2
    return R * 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))


def _compute_exceedance_severity(pi_composite, config=None):
    """超标倍数严重度加权因子。

    Pi 值越高，污染越严重，权重越大：
      Pi ≤ 3:  factor = 1.0 (无额外加权)
      Pi > 3:  factor = 1.5
      Pi > 5:  factor = 2.0
      Pi > 10: factor = 3.0

    Args:
        pi_composite: 内梅罗综合指数 array (1D)
        config: AnalysisConfig 或 None

    Returns:
        numpy array of same shape, values ≥ 1.0
    """
    if config is not None:
        multipliers = config.exceedance_severity_multipliers
    else:
        from .analysis_config import DEFAULT_CONFIG
        multipliers = DEFAULT_CONFIG.exceedance_severity_multipliers

    pi = np.asarray(pi_composite, dtype=float)
    factor = np.ones_like(pi)

    # 按阈值从高到低排序，确保高阈值优先匹配
    sorted_thresholds = sorted(multipliers.items(), key=lambda x: x[0], reverse=True)
    for threshold, multiplier in sorted_thresholds:
        factor[pi > threshold] = multiplier

    return factor


def _compute_temporal_persistence(df, pi_composite, window_min=30, min_points=3):
    """时间持续性评分。

    滑动窗口内超标点占比 → 0~1 时间权重因子。
    仅在窗口内超标点 >= min_points 时返回正值 (否则返回 0，表示孤立超标)。

    Args:
        df: 含 datetime 列的 DataFrame
        pi_composite: 内梅罗综合指数 array
        window_min: 时间窗口 (分钟)
        min_points: 最少连续超标点数

    Returns:
        numpy array，0~1 的时间持续性因子
    """
    pi = np.asarray(pi_composite, dtype=float)
    n = len(pi)
    factors = np.zeros(n)

    if n < min_points or 'datetime' not in df.columns:
        return factors

    is_exceed = (pi > 1.0).astype(float)  # Pi > 1 = 超标
    times = df['datetime'].values
    window_ns = pd.Timedelta(minutes=window_min).value  # 纳秒

    for i in range(n):
        t_center = np.datetime64(times[i])
        t_lo = t_center - pd.Timedelta(minutes=window_min / 2)
        t_hi = t_center + pd.Timedelta(minutes=window_min / 2)
        mask = (times >= t_lo) & (times <= t_hi)
        n_window = mask.sum()

        if n_window < min_points:
            continue

        exceed_ratio = is_exceed[mask].sum() / n_window

        # 时间持续因子 = 窗口内超标比例 (≥ min_points/n_window 才有效)
        if is_exceed[mask].sum() >= min_points:
            factors[i] = exceed_ratio

    return factors


def _compute_spatial_continuity(df, pi_composite, window_m=50, min_points=3):
    """空间连续性评分。

    基于 Haversine 距离，连续 N 个采样点超标才标记。
    返回每个点的空间聚集因子 0~1。

    Args:
        df: 含 latitude, longitude 列的 DataFrame
        pi_composite: 内梅罗综合指数 array
        window_m: 空间窗口 (米)
        min_points: 最少连续超标点数

    Returns:
        numpy array，0~1 的空间连续性因子
    """
    pi = np.asarray(pi_composite, dtype=float)
    n = len(pi)
    factors = np.zeros(n)

    if n < min_points or 'latitude' not in df.columns or 'longitude' not in df.columns:
        return factors

    is_exceed = (pi > 1.0).astype(bool)
    lats = df['latitude'].values
    lons = df['longitude'].values

    for i in range(n):
        if not is_exceed[i]:
            continue

        # 向前和向后搜索连续超标段
        start = i
        while start > 0 and is_exceed[start - 1]:
            dist = _haversine_distance_m(lats[start], lons[start], lats[start - 1], lons[start - 1])
            if dist > window_m * 2:  # gap too large, break
                break
            start -= 1

        end = i
        while end < n - 1 and is_exceed[end + 1]:
            dist = _haversine_distance_m(lats[end], lons[end], lats[end + 1], lons[end + 1])
            if dist > window_m * 2:
                break
            end += 1

        segment_len = end - start + 1
        if segment_len >= min_points:
            # 计算总空间跨度
            total_dist = _haversine_distance_m(
                lats[start], lons[start], lats[end], lons[end]
            )
            # 空间因子 = min(total_dist / window_m, 1.0)，跨度越大因子越高
            factors[i] = min(float(total_dist) / window_m, 1.0) if window_m > 0 else 0.0

    return factors


def compute_black_score_enhanced(df, config=None):
    """增强黑点评分：内梅罗指数 × 严重度因子 × 时间持续性 × 空间连续性。

    综合四个维度:
      1. 超标倍数 (内梅罗综合指数 Pi)
      2. 超标严重度 (越高倍率加权越大)
      3. 时间持续性 (滑动窗口内连续超标时长)
      4. 空间连续性 (连续超标段的空间跨度)

    Args:
        df: 监测数据 DataFrame
        config: AnalysisConfig 或 None

    Returns:
        numpy array of enhanced black scores
    """
    if config is None:
        from .analysis_config import DEFAULT_CONFIG as config

    # 1. 内梅罗综合指数 (超标倍数)
    pi_composite = calc_nemerow_index(calc_black_odor_single_factor(df, config))

    # 2. 超标严重度因子
    severity_factor = _compute_exceedance_severity(pi_composite, config)

    # 3. 时间持续性因子
    time_factor = _compute_temporal_persistence(
        df, pi_composite, config.temporal_window_min, config.persistence_min_points
    )

    # 4. 空间连续性因子
    spatial_factor = _compute_spatial_continuity(
        df, pi_composite, config.spatial_window_m, config.persistence_min_points
    )

    # 至少有一个维度有效 (Pi > 1) 时, 增强评分才生效
    # 复合加权: Pi × severity × (1 + time_factor) × (1 + spatial_factor)
    enhanced = pi_composite * severity_factor * (1.0 + config.time_weight_factor * time_factor) * (1.0 + config.spatial_weight_factor * spatial_factor)

    return enhanced


# ---------- 状态依赖的分析函数 ----------

def data_summary(df):
    """返回水质数据统计摘要 JSON。"""
    if df is None or len(df) == 0:
        return json.dumps({"error": "数据未加载"}, ensure_ascii=False)
    agg_cols = {k: v for k, v in {
        'ammonia_nitrogen': ['mean', 'max'], 'dissolved_oxygen': ['mean', 'min'],
        'turbidity': ['mean', 'max'], 'ph': ['mean', 'min', 'max'], 'cod': ['mean', 'max']
    }.items() if k in df.columns}
    s = df.agg(agg_cols) if agg_cols else pd.DataFrame()
    result = {
        "记录数": len(df),
        "时间范围": f"{df['datetime'].min()} ~ {df['datetime'].max()}",
    }
    for col, name, unit in [('ammonia_nitrogen','氨氮','mg/L'), ('dissolved_oxygen','溶解氧','mg/L'),
                              ('turbidity','浊度','NTU'), ('ph','pH',''), ('cod','COD','mg/L')]:
        if col in s.columns:
            result[name] = {"均值": round(float(s[col]['mean']), 2) if 'mean' in s[col].index else None,
                            "极值": round(float(s[col]['max' if 'max' in s[col].index else 'min']), 2),
                            "单位": unit}
    return json.dumps(result, ensure_ascii=False)


def find_black_spots(df, config=None):
    """黑臭水体检测 + DBSCAN 聚类 (增强时空维度)。

    双标准体系:
      1. GB3838-2002 (目标等级由 config.target_water_class 控制, 默认 V类)
         — 增强黑点评分综合四个维度: 超标倍数 × 严重度 × 时间持续性 × 空间连续性
      2. 《城市黑臭水体整治工作指南》(2015)
         — 黑臭等级判定 (is_black + severity_label)

    孤立超标点 (无时空连续性) 在增强评分中被抑制。

    Args:
        df: 监测数据 DataFrame
        config: AnalysisConfig 或 None (None 时使用 V 类默认值)
    """
    if config is None:
        from .analysis_config import DEFAULT_CONFIG as _cfg
        config = _cfg

    if df is None or len(df) == 0:
        return json.dumps({"error": "数据未加载"}, ensure_ascii=False), None

    target_class = config.target_water_class

    error = measurement_error(df)
    if error:
        return json.dumps({'error': error}, ensure_ascii=False), None
    valid = assessment_mask(df)
    quality = {'有效判定点数': int(valid.sum()), '未判定点数': int((~valid).sum())}
    temp = df.loc[valid].copy()
    # 增强黑点评分 (超标倍数 × 严重度 × 时间持续性 × 空间连续性)
    temp['black_score'] = compute_black_score_enhanced(temp, config)
    # 传统内梅罗指数 (用于对比参考)
    temp['nemerow_raw'] = calc_black_score_vectorized(temp, config)
    # 官方阈值判定黑臭等级
    levels, labels, colors = _determine_black_odor_vectorized(temp, config)
    temp['severity'] = levels
    temp['severity_label'] = labels
    temp['severity_color'] = colors
    # 黑点 = 任一关键指标超黑臭阈值 (保留传统判定 AND/OR 增强评分阈值)
    temp['is_black'] = (temp['severity'] != 'normal')

    # 标记时空增强识别的额外黑点: 增强评分 > 1.5 但传统判定为非黑臭的点
    temp['spatiotemporal_black'] = (temp['black_score'] > 1.5) & (temp['severity'] == 'normal')

    black = temp[temp['is_black']].copy()
    black['cluster_id'] = -1
    if {'longitude', 'latitude'} <= set(black.columns):
        coords = black[['longitude', 'latitude']].apply(pd.to_numeric, errors='coerce')
        spatial_valid = (coords.longitude.between(-180, 180)
                         & coords.latitude.between(-90, 90))
        if spatial_valid.sum() >= 5:
            black.loc[spatial_valid, 'cluster_id'] = DBSCAN(
                eps=config.dbscan_eps, min_samples=config.dbscan_min_samples
            ).fit_predict(coords.loc[spatial_valid])

    if len(black) == 0:
        return json.dumps({
            "黑点总数": 0,
            "提示": f"在 {quality['有效判定点数']} 个有效判定点中未检出黑臭（仅依据氨氮和溶解氧）；{quality['未判定点数']} 个缺测点未判定，不能视作达标。",
            **quality,
            "判定标准": {"污染指数": f"GB3838-2002 {target_class}类 + 增强时空评分", "黑臭判定": "《城市黑臭水体整治工作指南》(2015)"},
            "可用指标": _AVAILABLE_INDICATORS,
            "缺失指标": _MISSING_INDICATORS,
        }, ensure_ascii=False), black

    nc = len(set(black['cluster_id'])) - (1 if -1 in black['cluster_id'].values else 0)
    clusters_info = []
    for cid in sorted(black['cluster_id'].unique()):
        if cid == -1:
            continue
        clusters_info.append(get_cluster_summary(black[black['cluster_id'] == cid], cid, config))

    sev_dist = black['severity_label'].value_counts().to_dict()
    # 时空增强识别的额外黑点数
    n_extra = int(temp['spatiotemporal_black'].sum()) if 'spatiotemporal_black' in temp.columns else 0

    result = json.dumps({
        "判定标准": {
            "污染指数": f"GB3838-2002 {target_class}类 + 增强时空评分 (超标倍数×严重度×时间持续性×空间连续性)",
            "黑臭判定": "《城市黑臭水体整治工作指南》(2015)"
        },
        "可用指标": _AVAILABLE_INDICATORS,
        **quality,
        "缺失指标": _MISSING_INDICATORS,
        "黑点总数": len(black),
        "时空增强识别的额外黑点": n_extra,
        "聚类热点数": nc,
        "黑臭等级分布": sev_dist,
        "增强评分最高": round(float(black['black_score'].max()), 2),
        "内梅罗指数最高": round(float(black['nemerow_raw'].max()), 2) if 'nemerow_raw' in black.columns else None,
        "氨氮峰值(mg/L)": round(float(black['ammonia_nitrogen'].max()), 2) if 'ammonia_nitrogen' in black.columns else None,
        "溶解氧最低(mg/L)": round(float(black['dissolved_oxygen'].min()), 2) if 'dissolved_oxygen' in black.columns else None,
        "聚类详情": clusters_info
    }, ensure_ascii=False)
    return result, black


def analyze_video_frames(frames):
    """分析视频帧的浑浊度和颜色异常。"""
    if not frames:
        return "无视频帧"
    results = []
    for f in frames[:20]:
        buf = base64.b64decode(f['image_base64'])
        img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s, v = hsv[:, :, 1].mean(), hsv[:, :, 2].mean()
        lap = cv2.Laplacian(gray, cv2.CV_64F).var()
        turb = "清澈" if lap > 200 else ("轻微浑浊" if lap > 100 else ("中度浑浊" if lap > 30 else "重度浑浊"))
        col = "疑似黑臭" if (s < 40 and v < 70) else "正常"
        risk = "需关注" if turb in ["中度浑浊", "重度浑浊"] or col == "疑似黑臭" else "正常"
        results.append(f"时间{f['time_sec']}s: 浑浊度={turb}, 颜色={col}, 风险={risk}")
    return "\n".join(results)


# ============================================================
#  视频与监测数据交叉印证
# ============================================================

def _analyze_video_frames_structured(frames):
    """返回 DataFrame, 含每帧的数值指标和分类标签。

    列: time_sec, laplacian_var, mean_saturation, mean_brightness,
         turbidity_label, color_label, risk_label
    """
    if not frames:
        return pd.DataFrame()
    rows = []
    for f in frames:
        buf = base64.b64decode(f['image_base64'])
        img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        s, v = float(hsv[:, :, 1].mean()), float(hsv[:, :, 2].mean())
        lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        turb = "清澈" if lap > 200 else ("轻微浑浊" if lap > 100 else ("中度浑浊" if lap > 30 else "重度浑浊"))
        col = "疑似黑臭" if (s < 40 and v < 70) else "正常"
        risk = "需关注" if turb in ["中度浑浊", "重度浑浊"] or col == "疑似黑臭" else "正常"
        rows.append({
            'time_sec': f['time_sec'],
            'laplacian_var': lap,
            'mean_saturation': s,
            'mean_brightness': v,
            'turbidity_label': turb,
            'color_label': col,
            'risk_label': risk,
        })
    return pd.DataFrame(rows)


def cross_validate_video_with_monitoring(state, time_offset_sec=0, time_window_sec=30):
    """视频与监测数据交叉印证。

    Args:
        state: WaterQualityState (需含 df 和 video_frames)
        time_offset_sec: 视频 time_sec=0 相对第一条记录时间的偏移秒数 (正=视频滞后)
        time_window_sec: 匹配时间窗口宽度 (秒), 取窗口内监测数据的均值

    Returns:
        JSON 字符串, 含相关系数矩阵和黑臭判定一致率
    """
    df = state.df
    frames = state.video_frames if hasattr(state, 'video_frames') else []
    if df is None or len(df) == 0:
        return json.dumps({"error": "监测数据未加载"}, ensure_ascii=False)
    if not frames:
        return json.dumps({"error": "视频帧未提取，请先调用 extract_video"}, ensure_ascii=False)

    error = measurement_error(df)
    if error:
        return json.dumps({'error': error}, ensure_ascii=False)
    recording_start = df['datetime'].min()
    df = df.loc[assessment_mask(df)]

    # 结构化视频分析
    vid_df = _analyze_video_frames_structured(frames)
    if vid_df.empty:
        return json.dumps({"error": "未能从视频帧中提取有效指标"}, ensure_ascii=False)

    # 时间对齐: 视频第0秒 = 第一条数据时间 + 偏移
    base_time = recording_start + timedelta(seconds=time_offset_sec)
    vid_df['frame_datetime'] = vid_df['time_sec'].apply(lambda t: base_time + timedelta(seconds=t))

    # 为每个视频帧匹配监测数据 (时间窗口内取均值)
    matched = []
    for _, vrow in vid_df.iterrows():
        t_lo = vrow['frame_datetime'] - timedelta(seconds=time_window_sec / 2)
        t_hi = vrow['frame_datetime'] + timedelta(seconds=time_window_sec / 2)
        window = df[(df['datetime'] >= t_lo) & (df['datetime'] <= t_hi)]
        if len(window) == 0:
            continue
        matched.append({
            'time_sec': vrow['time_sec'],
            'laplacian_var': vrow['laplacian_var'],
            'mean_saturation': vrow['mean_saturation'],
            'mean_brightness': vrow['mean_brightness'],
            'video_turbidity_label': vrow['turbidity_label'],
            'video_color_label': vrow['color_label'],
            'video_risk': vrow['risk_label'],
            'turbidity': float(window['turbidity'].mean()) if 'turbidity' in window.columns else None,
            'ammonia': float(window['ammonia_nitrogen'].mean()) if 'ammonia_nitrogen' in window.columns else None,
            'do': float(window['dissolved_oxygen'].mean()) if 'dissolved_oxygen' in window.columns else None,
            'cod': float(window['cod'].mean()) if 'cod' in window.columns else None,
            'matched_records': len(window),
        })
    if len(matched) < 3:
        return json.dumps({"error": f"时间匹配的记录不足 ({len(matched)} < 3)，请调整 time_offset_sec 或 time_window_sec"}, ensure_ascii=False)

    match_df = pd.DataFrame(matched)

    # Pearson 相关系数矩阵 (视频指标 × 传感器指标)
    corr_pairs = []
    for v_col, v_label in [('laplacian_var', 'Laplacian方差'), ('mean_saturation', 'HSV饱和度'), ('mean_brightness', 'HSV亮度')]:
        for s_col, s_label in [('turbidity', '浊度'), ('ammonia', '氨氮'), ('do', '溶解氧'), ('cod', 'COD')]:
            vals = match_df[[v_col, s_col]].dropna()
            if len(vals) < 3:
                continue
            r, p = pearsonr(vals[v_col], vals[s_col])
            corr_pairs.append({
                "视频指标": v_label,
                "传感器指标": s_label,
                "Pearson_r": round(r, 4),
                "p_value": round(p, 4),
                "显著性": "***" if p < 0.001 else ("**" if p < 0.01 else ("*" if p < 0.05 else "不显著")),
                "样本数": len(vals),
            })
    corr_pairs.sort(key=lambda x: abs(x['Pearson_r']), reverse=True)

    # 黑臭判定一致性
    # 视频: color_label == "疑似黑臭" 视为黑臭
    # 传感器: 调用 _determine_black_odor_vectorized 判定
    sensor_levels, sensor_labels, _ = _determine_black_odor_vectorized(match_df.rename(columns={
        'ammonia': 'ammonia_nitrogen', 'do': 'dissolved_oxygen'}))
    match_df['sensor_black_odor'] = sensor_labels  # "轻度黑臭" / "重度黑臭" / "无黑臭"
    match_df['sensor_is_black'] = [l != '无黑臭' for l in sensor_labels]
    match_df['video_is_black'] = match_df['video_color_label'] == '疑似黑臭'

    both_black = int((match_df['video_is_black'] & match_df['sensor_is_black']).sum())
    both_clean = int((~match_df['video_is_black'] & ~match_df['sensor_is_black']).sum())
    video_only = int((match_df['video_is_black'] & ~match_df['sensor_is_black']).sum())
    sensor_only = int((~match_df['video_is_black'] & match_df['sensor_is_black']).sum())
    total = len(match_df)
    agreement = round((both_black + both_clean) / total * 100, 1) if total > 0 else 0

    result = json.dumps({
        "时间对齐": {
            "视频基准时间": str(base_time),
            "时间偏移秒": time_offset_sec,
            "匹配窗口秒": time_window_sec,
            "匹配成功帧数": len(match_df),
            "总视频帧数": len(vid_df),
        },
        "相关系数矩阵": corr_pairs,
        "黑臭判定一致性": {
            "总匹配帧": total,
            "双方一致_黑臭": both_black,
            "双方一致_清洁": both_clean,
            "仅视频判黑": video_only,
            "仅传感器判黑": sensor_only,
            "一致率_%": agreement,
            "解读": f"视频与传感器黑臭判定一致率为 {agreement}%",
            "视频判黑准确率_%": round(both_black / (both_black + video_only) * 100, 1) if (both_black + video_only) > 0 else None,
            "视频判黑召回率_%": round(both_black / (both_black + sensor_only) * 100, 1) if (both_black + sensor_only) > 0 else None,
        },
        "关键发现": _summarize_cross_validation(corr_pairs, agreement),
    }, ensure_ascii=False)
    return result


def _summarize_cross_validation(corr_pairs, agreement):
    """生成交叉验证关键发现的中文摘要。"""
    findings = []
    significant = [c for c in corr_pairs if c['显著性'] != '不显著']
    if significant:
        best = significant[0]
        direction = "正相关" if best['Pearson_r'] > 0 else "负相关"
        findings.append(f"最强显著相关: {best['视频指标']} vs {best['传感器指标']} ({direction}, r={best['Pearson_r']}, p={best['p_value']})")
    else:
        findings.append("未发现视频指标与传感器指标的显著相关性，可能时间未对齐或水质变化在视频中不可见")
    if agreement >= 70:
        findings.append(f"黑臭判定一致率 {agreement}% — 视频视觉判断与传感器检测基本吻合")
    elif agreement >= 50:
        findings.append(f"黑臭判定一致率 {agreement}% — 部分吻合，存在一定偏差")
    else:
        findings.append(f"黑臭判定一致率 {agreement}% — 偏差较大，视频颜色判断可能受光照条件影响")
    return findings


# ============================================================
#  综合分析摘要 — 聚合所有分析结果供 LLM 消费
# ============================================================

def build_comprehensive_summary(state, river_state=None, config=None):
    """聚合所有分析结果为一统一 JSON，供 LLM 获得完整数据图景。

    包含:
      - 数据概览 (data_summary)
      - 黑臭检测 (find_black_spots)
      - 空间分析 (聚类位置、Top-5 严重点位、周边污染源)
      - 交叉印证 (cross_validate 结果)
      - 河道模型 (水动力 + 水质模拟结果)

    Args:
        state: WaterQualityState
        river_state: RiverModelState 或 None
        config: AnalysisConfig 或 None
    """
    if config is None:
        from .analysis_config import DEFAULT_CONFIG as config

    sections = {}

    # ---- 1. 数据概览 ----
    if state.df is not None and len(state.df) > 0:
        sections["数据概览"] = {
            "记录数": len(state.df),
            "时间范围": f"{state.df['datetime'].min()} ~ {state.df['datetime'].max()}",
            "有效判定点数": int(assessment_mask(state.df).sum()),
            "未判定点数": int((~assessment_mask(state.df)).sum()),
        }
        for col, name, unit in [('ammonia_nitrogen', '氨氮', 'mg/L'), ('dissolved_oxygen', '溶解氧', 'mg/L'),
                                  ('turbidity', '浊度', 'NTU'), ('ph', 'pH', ''), ('cod', 'COD', 'mg/L')]:
            if col in state.df.columns:
                s = state.df[col].dropna()
                if len(s) > 0:
                    sections["数据概览"][name] = {
                        "均值": round(float(s.mean()), 2),
                        "最小": round(float(s.min()), 2),
                        "最大": round(float(s.max()), 2),
                        "单位": unit,
                    }

    # ---- 2. 黑臭检测 ----
    if state.black_spots is not None and len(state.black_spots) > 0:
        black = state.black_spots
        target_class = config.target_water_class if config else "V"
        sections["黑臭检测"] = {
            "判定标准": {
                "污染指数": f"GB3838-2002 {target_class}类 + 内梅罗综合指数",
                "黑臭判定": "《城市黑臭水体整治工作指南》(2015)"
            },
            "黑点总数": len(black),
            "黑臭等级分布": black['severity_label'].value_counts().to_dict(),
            "内梅罗指数范围": f"{black['black_score'].min():.2f} ~ {black['black_score'].max():.2f}",
            "氨氮峰值_mgL": round(float(black['ammonia_nitrogen'].max()), 2) if 'ammonia_nitrogen' in black.columns else None,
            "溶解氧最低_mgL": round(float(black['dissolved_oxygen'].min()), 2) if 'dissolved_oxygen' in black.columns else None,
        }
        # 聚类详情
        clusters_out = []
        for cid in sorted(black['cluster_id'].unique()):
            if cid == -1:
                continue
            c = black[black['cluster_id'] == cid]
            clusters_out.append(get_cluster_summary(c, cid, config))
        sections["黑臭检测"]["聚类数"] = len(clusters_out)
        sections["黑臭检测"]["聚类详情"] = clusters_out

        # Top-5 最严重单点
        top5 = black.nlargest(5, 'black_score')
        top5_list = []
        for i, (_, row) in enumerate(top5.iterrows(), 1):
            top5_list.append({
                "排名": i,
                "时间": str(row['datetime']),
                "经纬度": f"{row['latitude']:.6f}, {row['longitude']:.6f}",
                "内梅罗指数": round(float(row['black_score']), 2),
                "黑臭等级": row['severity_label'],
                "氨氮_mgL": round(float(row['ammonia_nitrogen']), 2) if pd.notna(row.get('ammonia_nitrogen')) else None,
                "溶解氧_mgL": round(float(row['dissolved_oxygen']), 2) if pd.notna(row.get('dissolved_oxygen')) else None,
                "COD_mgL": round(float(row['cod']), 0) if pd.notna(row.get('cod')) else None,
                "浊度_NTU": round(float(row['turbidity']), 0) if pd.notna(row.get('turbidity')) else None,
            })
        sections["黑臭检测"]["Top5最严重点位"] = top5_list

        # 空间范围
        sections["空间分析"] = {
            "轨迹起点": f"{state.df['latitude'].iloc[0]:.6f}, {state.df['longitude'].iloc[0]:.6f}",
            "轨迹终点": f"{state.df['latitude'].iloc[-1]:.6f}, {state.df['longitude'].iloc[-1]:.6f}",
            "最密集聚类中心": None,
        }
        if len(clusters_out) > 0:
            densest = max(clusters_out, key=lambda x: x['count'])
            sections["空间分析"]["最密集聚类中心"] = {
                "经纬度": f"{densest['center_lat']:.6f}, {densest['center_lon']:.6f}",
                "点数": densest['count'],
                "内梅罗等级": densest.get('内梅罗等级', ''),
                "黑臭等级": densest.get('黑臭等级', ''),
            }

    # ---- 3. 交叉印证 ----
    if hasattr(state, 'cross_validation_result') and state.cross_validation_result:
        cv = state.cross_validation_result
        if 'error' not in cv:
            sections["交叉印证"] = {
                "时间对齐": cv.get("时间对齐", {}),
                "黑臭判定一致性": cv.get("黑臭判定一致性", {}),
                "显著相关项": [c for c in cv.get("相关系数矩阵", []) if c['显著性'] != '不显著'],
            }

    # ---- 4. 河道模型 ----
    if river_state is not None:
        try:
            river = {}
            hydro_usable = not (river_state.has_hydro() and
                                river_state.hydro_result.params.get('tidal_amplitude_m', 0))
            if not hydro_usable:
                river['结论限制'] = '潮汐模型未通过质量守恒验收，已排除其水动力和水质模拟数值，不得用于结论。'
            if river_state.model_initialized:
                river["模型已初始化"] = True
                river["断面数"] = len(river_state.cross_sections)
                river["河道长度_m"] = river_state.config.length_m
                river["平均宽度_m"] = river_state.config.width_avg_m
            if river_state.has_hydro() and hydro_usable:
                h = river_state.hydro_result
                river["水动力模拟"] = {
                    "模拟时长_h": h.t[-1],
                    "最高水位_m": round(float(h.water_level.max()), 2),
                    "最低水位_m": round(float(h.water_level.min()), 2),
                    "最大流速_ms": round(float(h.velocity.max()), 3),
                    "平均流速_ms": round(float(np.nanmean(np.abs(h.velocity))), 3),
                    "参数": h.params,
                }
            wq_tidal = (getattr(river_state.wq_result, 'params', {}) or {}).get('hydro_tidal_amplitude_m', 0)
            if wq_tidal:
                river['结论限制'] = '水质模拟来源于未验收潮汐工况，已排除其数值。'
            if hydro_usable and not wq_tidal and hasattr(river_state, 'has_wq') and river_state.has_wq():
                w = river_state.wq_result
                ammonia = w.ammonia if w.ammonia is not None else np.zeros((1, 1))
                river["水质模拟"] = {
                    "污染物": w.params.get('pollutant_type', 'unknown'),
                    "排放量_kg": w.params.get('load_kg', '?'),
                    "排放桩号_m": w.params.get('chainage_m', '?'),
                    "模拟时长_h": w.t[-1],
                    "峰值浓度_mgL": round(float(ammonia.max()), 2),
                    "峰值时刻_h": round(float(w.t[np.unravel_index(ammonia.argmax(), ammonia.shape)[0]]), 1),
                }
            if river:
                sections["河道模型"] = river
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            sections["河道模型"] = {"状态": "模型数据读取不完整"}

    # ---- 综合 ----
    return json.dumps({
        "分析时间": str(pd.Timestamp.now()),
        "分析模块": list(sections.keys()),
        **sections,
    }, ensure_ascii=False, default=str)
