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
from .data_quality import (measurement_error, assessment_mask, coordinate_text,
                           observation_values, coordinate_mask)
from .detection_support import temporal_support, spatial_support, cluster_black_samples, DETECTION_VERSION
from .evidence import EVIDENCE_VERSION, data_identity, conclusion_limits

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
    avg_score = round(float(cluster_df.get('nemerow_raw',cluster_df['black_score']).mean()), 2)
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
        "时空增强评分_均值": round(float(cluster_df['black_score'].mean()), 2),
        "内梅罗等级": nemerow_label,
        "黑臭等级": black_odor_level,
        "目标水质": f"GB3838-2002 {target_class}类",
        "判定标准": "单因子+内梅罗: GB3838-2002 | 黑臭判定: 《城市黑臭水体整治工作指南》(2015)",
    }
    # 关键指标统计
    pollutants = {}
    if len(values := observation_values(cluster_df, 'ammonia_nitrogen')):
        nh3_mean = float(values.mean())
        nh3_max = float(values.max())
        pollutants['氨氮'] = f"均值{nh3_mean:.1f}/峰值{nh3_max:.1f} mg/L"
    if len(values := observation_values(cluster_df, 'dissolved_oxygen')):
        do_mean = float(values.mean())
        do_min = float(values.min())
        pollutants['DO'] = f"均值{do_mean:.2f}/最低{do_min:.2f} mg/L"
    for col, label, unit in [('cod', 'COD', 'mg/L'), ('turbidity', '浊度', 'NTU')]:
        values = observation_values(cluster_df, col)
        pollutants[label] = f"均值{float(values.mean()):.1f} {unit}" if len(values) else '缺测，未参与统计'
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
        info["首末观测跨度_min"] = round((times.max() - times.min()).total_seconds() / 60.0, 1)
        info["持续性说明"] = '首末观测间隔不代表全时段连续超标。'
        info['趋势'] = '时间趋势未判定'
        info['趋势解释'] = '走航同时改变位置和时间；沿航迹读数变化不能证明固定地点水质恶化、改善或稳定。'
        if len(cluster_df) >= 3:
            x = (times - times.min()).dt.total_seconds().values.astype(float)
            y = cluster_df.get('nemerow_raw',cluster_df['black_score']).values.astype(float)
            finite = np.isfinite(x) & np.isfinite(y)
            if finite.sum() >= 3 and np.std(x[finite]) > 0:
                slope = np.polyfit(x[finite], y[finite], 1)[0]
                per_hour = slope * 3600.0  # 每小时变化率
                info['沿航迹指数变化率_每小时'] = round(float(per_hour), 3)
                if per_hour > 0.5:
                    info['沿航迹指数变化'] = '升高'
                elif per_hour < -0.5:
                    info['沿航迹指数变化'] = '降低'
                else:
                    info['沿航迹指数变化'] = '无明显变化'

    if 'latitude' in cluster_df.columns and 'longitude' in cluster_df.columns:
        lats, lons = cluster_df['latitude'].values, cluster_df['longitude'].values
        farthest = int(np.argmax(_haversine_distance_m(lats[0],lons[0],lats,lons)))
        spatial_span = np.max(_haversine_distance_m(lats[farthest],lons[farthest],lats,lons))
        info["空间跨度_m"] = round(float(spatial_span), 1)
        info["空间跨度口径"] = '两次最远点距离估计，不是河道长度或污染影响范围。'

    # 综合指数不是任一单项污染物的超标倍数。
    if 'black_score' in cluster_df.columns:
        raw = cluster_df.get('nemerow_raw',cluster_df['black_score'])
        info['原始内梅罗范围'] = f"{raw.min():.2f} ~ {raw.max():.2f}"
        info["增强评分范围"] = f"{cluster_df['black_score'].min():.1f} ~ {cluster_df['black_score'].max():.1f}"
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

    # 从低到高覆盖；否则高阈值的3.0会被后续1.5覆盖。
    sorted_thresholds = sorted(multipliers.items(), key=lambda x: x[0])
    for threshold, multiplier in sorted_thresholds:
        factor[pi > threshold] = multiplier

    return factor


def _compute_temporal_persistence(df, pi_composite, window_min=30, min_points=3, max_gap_min=5):
    """观测支持的超目标时长/完整窗口；不把重复采样当持续证据。"""
    return temporal_support(df, pi_composite, window_min, min_points, max_gap_min)


def _compute_spatial_continuity(df, pi_composite, window_m=50, min_points=3, max_gap_min=5):
    """有效相邻观测段的空间支持度，缺测、定位跳跃及长空档处断开。"""
    return spatial_support(df, pi_composite, window_m, min_points, max_gap_min)


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
        df, pi_composite, config.temporal_window_min, config.persistence_min_points, config.continuity_max_gap_min
    )

    # 4. 空间连续性因子
    spatial_factor = _compute_spatial_continuity(
        df, pi_composite, config.spatial_window_m, config.persistence_min_points, config.continuity_max_gap_min
    )

    # 至少有一个维度有效 (Pi > 1) 时, 增强评分才生效
    # 复合加权: Pi × severity × (1 + time_factor) × (1 + spatial_factor)
    enhanced = np.where(pi_composite > 1, pi_composite * severity_factor
        * (1.0 + config.time_weight_factor * time_factor) * (1.0 + config.spatial_weight_factor * spatial_factor), pi_composite)

    return enhanced


# ---------- 状态依赖的分析函数 ----------

def data_summary(df, filename=''):
    """返回水质数据统计摘要 JSON。"""
    if df is None or len(df) == 0:
        return json.dumps({"error": "数据未加载"}, ensure_ascii=False)
    result = {
        '证据版本': EVIDENCE_VERSION,
        '数据身份': data_identity(df, filename),
        "记录数": len(df),
        "时间范围": f"{df['datetime'].min()} ~ {df['datetime'].max()}",
        '有效GPS记录数': int(coordinate_mask(df).sum()),
        '统计口径': '仅统计有效原始观测；有原始观测标记时排除插值。旧数据无标记时无法追溯插值。',
    }
    for col, name, unit in [('ammonia_nitrogen','氨氮','mg/L'), ('dissolved_oxygen','溶解氧','mg/L'),
                              ('turbidity','浊度','NTU'), ('ph','pH',''), ('cod','COD','mg/L')]:
        if col in df.columns:
            values = observation_values(df, col)
            extreme = values.min() if col == 'dissolved_oxygen' else values.max()
            result[name] = {"均值": round(float(values.mean()), 2) if len(values) else None,
                            "极值": round(float(extreme), 2) if len(values) else None,
                            '有效观测数': len(values), "单位": unit}
    result['缺测指标'] = [name for col, name in [('ammonia_nitrogen','氨氮'),
        ('dissolved_oxygen','溶解氧'), ('cod','COD'), ('turbidity','浊度'), ('ph','pH')]
        if observation_values(df, col).empty]
    return json.dumps(result, ensure_ascii=False)


def find_black_spots(df, config=None):
    """黑臭水体检测 + DBSCAN 聚类 (增强时空维度)。

    双标准体系:
      1. GB3838-2002 (目标等级由 config.target_water_class 控制, 默认 V类)
         — 增强黑点评分综合四个维度: 超标倍数 × 严重度 × 时间持续性 × 空间连续性
      2. 《城市黑臭水体整治工作指南》(2015)
         — 黑臭等级判定 (is_black + severity_label)

    时空支持充分时提高排序权重；孤立超标点仍保留点级预警。

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
    ordered = df.copy()
    if 'datetime' in ordered:
        ordered['__time_sort'] = pd.to_datetime(ordered['datetime'],errors='coerce',utc=True)
        ordered = ordered.sort_values('__time_sort',kind='stable').drop(columns='__time_sort')
    ordered['__sample_sequence'] = np.arange(len(ordered))
    valid = assessment_mask(ordered)
    quality = {'有效判定点数': int(valid.sum()), '未判定点数': int((~valid).sum()),
        '算法版本':DETECTION_VERSION, '聚类提示':None,
        '证据版本': EVIDENCE_VERSION,
        '综合指数计算状态': '已对有效判定点计算；热点摘要只展示预警点，不展示全部正常点指数。',
        '筛查口径':'仅用氨氮和DO的点级预警；缺少重复采样、透明度及ORP，不能作为完整水体定级或污染责任认定。'}
    temp = ordered.loc[valid].copy()
    # 增强黑点评分 (超标倍数 × 严重度 × 时间持续性 × 空间连续性)
    temp['black_score'] = compute_black_score_enhanced(temp, config)
    # 传统内梅罗指数 (用于对比参考)
    temp['nemerow_raw'] = calc_black_score_vectorized(temp, config)
    # 官方阈值判定黑臭等级
    levels, labels, colors = _determine_black_odor_vectorized(temp, config)
    temp['severity'] = levels
    temp['severity_label'] = labels
    temp['severity_color'] = colors
    # 点级预警只取关键指标阈值；排序分数不能替代黑臭筛查条件。
    temp['is_black'] = (temp['severity'] != 'normal')

    # 非黑臭但指数异常只是线索，不能因时空加权而晋升为黑臭结论。
    temp['index_only_alert'] = (temp['nemerow_raw'] > 1) & (temp['severity'] == 'normal')
    quality['非黑臭指数异常点数'] = int(temp['index_only_alert'].sum())

    black = temp[temp['is_black']].copy()
    try:
        black['cluster_id'], quality['聚类参数'] = cluster_black_samples(black, config)
    except ValueError as exc:
        black['cluster_id'] = -1
        quality['聚类提示'] = str(exc)

    if len(black) == 0:
        return json.dumps({
            "黑点总数": 0,
            "聚类热点数": 0,
            "提示": f"在 {quality['有效判定点数']} 个有效判定点中未检出黑臭（仅依据氨氮和溶解氧）；{quality['未判定点数']} 个缺测点未判定，不能视作达标。",
            **quality,
            "判定标准": {"污染指数": f"参考GB3838-2002 {target_class}类限值的工程综合指数；增强评分仅用于排序", "黑臭判定": "参考《城市黑臭水体整治工作指南》(2015)的氨氮/DO阈值进行点级筛查"},
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
    result = json.dumps({
        "判定标准": {
            "污染指数": f"参考GB3838-2002 {target_class}类限值的工程综合指数；增强评分仅用于排序",
            "黑臭判定": "参考《城市黑臭水体整治工作指南》(2015)的氨氮/DO阈值进行点级筛查"
        },
        "可用指标": _AVAILABLE_INDICATORS,
        **quality,
        "缺失指标": _MISSING_INDICATORS,
        "黑点总数": len(black),
        "时空增强识别的额外黑点": 0,  # 兼容旧客户端；指数线索不计入黑臭点。
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
        try:
            timestamp = f['time_sec']
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int,float)) or not np.isfinite(timestamp) or timestamp < 0:
                continue
            buf = base64.b64decode(f['image_base64'], validate=True)
            img = cv2.imdecode(np.frombuffer(buf, np.uint8), cv2.IMREAD_COLOR)
        except (KeyError, TypeError, ValueError, cv2.error):
            continue
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

    try:
        for value in (time_offset_sec, time_window_sec):
            if isinstance(value, (bool,str)) or not isinstance(value, (int,float,np.integer,np.floating)) or not np.isfinite(float(value)):
                raise ValueError('时间参数必须为有限数值')
        if time_window_sec <= 0:
            raise ValueError('匹配窗口必须大于0秒')
        offset_delta = timedelta(seconds=float(time_offset_sec))
        half_window = timedelta(seconds=float(time_window_sec)/2)
    except (ValueError, TypeError, OverflowError) as exc:
        return json.dumps({'error':f'时间参数无效：{exc}'}, ensure_ascii=False)

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
    try:
        base_time = recording_start + offset_delta
        vid_df['frame_datetime'] = vid_df['time_sec'].apply(lambda t: base_time + timedelta(seconds=t))
        # 预先检查范围，失败时不写入部分结果。
        _ = vid_df['frame_datetime'].min() - half_window
        _ = vid_df['frame_datetime'].max() + half_window
    except (OverflowError, ValueError) as exc:
        return json.dumps({'error':'时间偏移或匹配窗口超出可表示范围，请检查秒单位。'}, ensure_ascii=False)

    # 为每个视频帧匹配监测数据 (时间窗口内取均值)
    matched = []
    for _, vrow in vid_df.iterrows():
        t_lo = vrow['frame_datetime'] - half_window
        t_hi = vrow['frame_datetime'] + half_window
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
    correlation_limits = []
    for v_col, v_label in [('laplacian_var', 'Laplacian方差'), ('mean_saturation', 'HSV饱和度'), ('mean_brightness', 'HSV亮度')]:
        for s_col, s_label in [('turbidity', '浊度'), ('ammonia', '氨氮'), ('do', '溶解氧'), ('cod', 'COD')]:
            vals = match_df[[v_col, s_col]].apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan).dropna()
            if len(vals) < 3:
                correlation_limits.append(f'{v_label} × {s_label}：有效匹配样本不足3个。')
                continue
            if vals[v_col].nunique() < 2 or vals[s_col].nunique() < 2:
                correlation_limits.append(f'{v_label} × {s_label}：常数序列无法计算相关系数。')
                continue
            r, p = pearsonr(vals[v_col], vals[s_col])
            if not np.isfinite(r) or not np.isfinite(p):
                correlation_limits.append(f'{v_label} × {s_label}：计算结果非有限数，未输出相关系数。')
                continue
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
            "总视频帧数": len(frames),
            "有效视频帧数": len(vid_df),
            "无效视频帧数": len(frames)-len(vid_df),
        },
        "相关系数矩阵": corr_pairs,
        "相关性限制": correlation_limits,
        "相关性说明": '探索性相关分析；窗口可能重叠、序列可能自相关，p值未作多重比较校正，不用于证明因果关系。',
        "黑臭判定一致性": {
            "总匹配帧": total,
            "双方一致_黑臭": both_black,
            "双方一致_清洁": both_clean,
            "仅视频判黑": video_only,
            "仅传感器判黑": sensor_only,
            "一致率_%": agreement,
            "解读": f"视频与传感器阈值筛查一致率为 {agreement}%；以传感器筛查为参照，没有人工真值，不代表现场准确率。",
            "指标口径": '以下比例仅比较视频启发式判断与同一窗口的传感器筛查，不是人工复核准确率。',
            "视频预警符合率_%": round(both_black / (both_black + video_only) * 100, 1) if (both_black + video_only) > 0 else None,
            "传感器预警视频覆盖率_%": round(both_black / (both_black + sensor_only) * 100, 1) if (both_black + sensor_only) > 0 else None,
            "视频判黑准确率_%": round(both_black / (both_black + video_only) * 100, 1) if (both_black + video_only) > 0 else None,
            "视频判黑召回率_%": round(both_black / (both_black + sensor_only) * 100, 1) if (both_black + sensor_only) > 0 else None,
        },
        "关键发现": _summarize_cross_validation(corr_pairs, agreement),
    }, ensure_ascii=False, allow_nan=False)
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

    identity = data_identity(state.df, getattr(state, 'data_filename', ''))
    sections = {'数据身份': identity, '证据边界': conclusion_limits(identity),
        '筛查方法': {'使用指标': ['氨氮', '溶解氧'],
            '缺失的黑臭分级指标': _MISSING_INDICATORS,
            'COD用途': '不参与当前氨氮/DO点级黑臭判定；COD缺测影响有机污染评价，不能解释无黑臭预警。',
            '综合指数计算状态': ('已对有效判定点计算，热点摘要未展示正常点指数'
                          if state.black_spots is not None else '尚未完成本轮点级筛查'),
            '目标水质等级': config.target_water_class}}
    station_result = getattr(state, 'national_station_result', None)
    if station_result and identity['kind'] != 'synthetic':
        sections['国控站点查询'] = {
            k: station_result.get(k) for k in ('数据来源', '数据时效', '含实时监测值',
                '查询条件', '本次查询时间', '站点数量')}
        sections['国控站点查询']['使用说明'] = '监测背景参考；须核对查询省份、站点监测时间和当前走航日期，不是排污源或当前河段的直接实测值'
        sections['国控站点查询']['监测时间样例'] = [s.get('时间') for s in station_result.get('站点列表', [])[:3]]
        sections['国控站点查询']['可用于本次走航直接对照'] = False
        sections['国控站点查询']['未核实内容'] = ['同期观测', '同一水文连通河段', '可比采样条件']
    elif station_result:
        sections['国控站点查询'] = {'使用状态': '仿真分析排除真实监测背景，不用于比较或归因'}

    # ---- 1. 数据概览 ----
    if state.df is not None and len(state.df) > 0:
        sections["数据概览"] = {
            "记录数": len(state.df),
            "时间范围": f"{state.df['datetime'].min()} ~ {state.df['datetime'].max()}",
            "有效判定点数": int(assessment_mask(state.df).sum()),
            "未判定点数": int((~assessment_mask(state.df)).sum()),
            '有效GPS记录数': int(coordinate_mask(state.df).sum()),
            '缺测指标': json.loads(data_summary(state.df))['缺测指标'],
            '统计口径': '有原始观测标记时排除插值；旧数据无标记时无法追溯插值。',
        }
        for col, name, unit in [('ammonia_nitrogen', '氨氮', 'mg/L'), ('dissolved_oxygen', '溶解氧', 'mg/L'),
                                  ('turbidity', '浊度', 'NTU'), ('ph', 'pH', ''), ('cod', 'COD', 'mg/L')]:
            if col in state.df.columns:
                s = observation_values(state.df, col)
                if len(s) > 0:
                    sections["数据概览"][name] = {
                        "均值": round(float(s.mean()), 2),
                        "最小": round(float(s.min()), 2),
                        "最大": round(float(s.max()), 2),
                        "单位": unit,
                        '有效观测数': len(s),
                    }

        coords = state.df.loc[coordinate_mask(state.df)]
        sections['空间分析'] = {'有效GPS记录数': len(coords),
            '轨迹起点': coordinate_text(coords.iloc[0]) if len(coords) else None,
            '轨迹终点': coordinate_text(coords.iloc[-1]) if len(coords) else None,
            '最密集聚类中心': None,
            '说明': '没有污染聚类中心不等于没有GPS坐标；热点聚类数不等于污染源数；往返起终点接近不代表覆盖范围小。'}
        if len(coords):
            coord_values = coords[['latitude', 'longitude']].apply(pd.to_numeric, errors='coerce')
            sections['空间分析']['有效采样经纬度包络'] = {
                '纬度范围': [float(coord_values.latitude.min()), float(coord_values.latitude.max())],
                '经度范围': [float(coord_values.longitude.min()), float(coord_values.longitude.max())],
                '口径': '所有有效GPS记录的坐标极值，不是河道长度或污染范围。'}

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
            "内梅罗指数范围": f"{black.get('nemerow_raw',black['black_score']).min():.2f} ~ {black.get('nemerow_raw',black['black_score']).max():.2f}",
            "时空增强评分范围": f"{black['black_score'].min():.2f} ~ {black['black_score'].max():.2f}",
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
            point_stats = {}
            for col, field, precision in [('ammonia_nitrogen', '氨氮_mgL', 2),
                    ('dissolved_oxygen', '溶解氧_mgL', 2), ('cod', 'COD_mgL', 0), ('turbidity', '浊度_NTU', 0)]:
                values = observation_values(row.to_frame().T, col)
                point_stats[field] = round(float(values.iloc[0]), precision) if len(values) else None
            top5_list.append({
                "排名": i,
                "时间": str(row['datetime']),
                "经纬度": coordinate_text(row),
                "内梅罗指数": round(float(row.get('nemerow_raw',row['black_score'])), 2),
                "时空增强评分": round(float(row['black_score']), 2),
                "黑臭等级": row['severity_label'],
                **point_stats,
            })
        sections["黑臭检测"]["Top5最严重点位"] = top5_list

        # 空间范围
        if len(clusters_out) > 0:
            densest = max(clusters_out, key=lambda x: x['count'])
            sections["空间分析"]["最密集聚类中心"] = {
                "经纬度": f"{densest['center_lat']:.6f}, {densest['center_lon']:.6f}",
                "点数": densest['count'],
                "内梅罗等级": densest.get('内梅罗等级', ''),
                "黑臭等级": densest.get('黑臭等级', ''),
            }

    if state.black_spots is not None and len(state.black_spots) == 0:
        sections['黑臭检测'] = {'黑点总数': 0, '聚类数': 0,
            '无热点原因': '没有氨氮/DO黑臭预警点可参与黑臭热点聚类，不是GPS缺失或聚类失败。',
            '提示': '本次有效判定点未检出黑臭，缺测点未判定'}

    # ---- 3. 交叉印证 ----
    if hasattr(state, 'cross_validation_result') and state.cross_validation_result:
        cv = state.cross_validation_result
        if 'error' not in cv:
            agreement_info = dict(cv.get('黑臭判定一致性', {}))
            agreement_info.setdefault('视频预警符合率_%', agreement_info.pop('视频判黑准确率_%', None))
            agreement_info.setdefault('传感器预警视频覆盖率_%', agreement_info.pop('视频判黑召回率_%', None))
            agreement_info['指标口径'] = '仅以传感器阈值筛查为参照，没有人工真值，不代表现场准确率。'
            sections["交叉印证"] = {
                "时间对齐": cv.get("时间对齐", {}),
                "黑臭判定一致性": agreement_info,
                "相关性限制": cv.get('相关性限制', []),
                "相关性说明": cv.get('相关性说明', '探索性相关分析，不用于证明因果关系。'),
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
                pollutant = w.params.get('pollutant_type', 'unknown')
                concentration = w.cbod if pollutant == 'cbod' else w.ammonia
                river["水质模拟"] = {
                    "污染物": pollutant,
                    "排放量_kg": w.params.get('load_kg', '?'),
                    "排放桩号_m": w.params.get('chainage_m', '?'),
                    "排放开始_h": w.params.get('source_start_h'),
                    "排放持续_min": w.params.get('duration_min'),
                    "实际注入量_kg": w.params.get('mass_balance', {}).get(pollutant, {}).get('injected_kg'),
                    "实际计算桩号_m": w.params.get('source_effective_chainage_m'),
                    "源位置离散距离_m": w.params.get('source_snap_distance_m'),
                    "输出时间点数": len(w.t),
                    "模拟时长_h": w.t[-1],
                    "反应模型": w.params.get('reaction_model', {}),
                    "反应核算": w.params.get('reaction_diagnostics', {}),
                    "结论限制": "仅为情景模拟，参数未经当前河道现场率定，不能据此认定污染责任",
                }
                if pollutant in ('ammonia', 'cbod') and concentration is not None:
                    river['水质模拟'].update({
                        "峰值浓度_mgL": round(float(concentration.max()), 2),
                        "峰值时刻_h": round(float(w.t[np.unravel_index(concentration.argmax(), concentration.shape)[0]]), 4),
                    })
                else:
                    river['水质模拟']['数据状态'] = '缺少有效污染物组分，不生成峰值结论'
            if river:
                sections["河道模型"] = river
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            sections["河道模型"] = {"状态": "模型数据读取不完整"}

    # ---- 综合 ----
    return json.dumps({
        '证据版本': EVIDENCE_VERSION,
        "分析时间": str(pd.Timestamp.now()),
        "分析模块": list(sections.keys()),
        **sections,
    }, ensure_ascii=False, default=str)
