"""水质分析统一配置模块。

将所有散落在各个文件中的硬编码常量集中到此 AnalysisConfig dataclass,
支持运行时覆盖和持久化, 实现"目标水质、溯源范围等参数设计为可配置的公共变量"。

用法:
    from water_quality_mcp.src.water_quality_mcp.analysis_config import (
        AnalysisConfig, DEFAULT_CONFIG, get_config, set_config,
    )

    config = get_config()                           # 全局单例
    config.target_water_class = "IV"                # 切换到IV类
    pi = calc_black_odor_single_factor(df, config)  # 传入分析函数
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ======================================================================
# GB3838-2002 地表水环境质量标准 — 各等级限值
# ======================================================================
# 数据来源: 《地表水环境质量标准》(GB 3838-2002) 表1
# 单位: 氨氮=mg/L, DO=mg/L, COD=mg/L, BOD=mg/L, 总磷=mg/L, 总氮=mg/L
# ======================================================================

GB3838_STANDARDS: Dict[str, Dict[str, float]] = {
    "I":   {"nh3": 0.15, "do": 7.5, "cod": 15, "bod": 3,  "tp": 0.02, "tn": 0.2},
    "II":  {"nh3": 0.5,  "do": 6.0, "cod": 15, "bod": 3,  "tp": 0.1,  "tn": 0.5},
    "III": {"nh3": 1.0,  "do": 5.0, "cod": 20, "bod": 4,  "tp": 0.2,  "tn": 1.0},
    "IV":  {"nh3": 1.5,  "do": 3.0, "cod": 30, "bod": 6,  "tp": 0.3,  "tn": 1.5},
    "V":   {"nh3": 2.0,  "do": 2.0, "cod": 40, "bod": 10, "tp": 0.4,  "tn": 2.0},
}

GB3838_CLASS_LABELS: Dict[str, str] = {
    "I":   "Ⅰ类 (源头水/国家自然保护区)",
    "II":  "Ⅱ类 (集中式饮用水源地一级保护区)",
    "III": "Ⅲ类 (集中式饮用水源地二级保护区)",
    "IV":  "Ⅳ类 (一般工业用水/娱乐用水)",
    "V":   "Ⅴ类 (农业用水/一般景观)",
}

# GB3838-2002 各等级浊度参考值 (NTU) — 标准未明确定义, 采用经验参考值
GB3838_TURBIDITY_REFERENCE: Dict[str, float] = {
    "I": 50, "II": 80, "III": 100, "IV": 150, "V": 200,
}

# ======================================================================
# 《城市黑臭水体整治工作指南》(2015) 判定阈值
# ======================================================================

BLACK_ODOR_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "mild":   {"nh3": 8.0,  "do": 2.0},
    "severe": {"nh3": 15.0, "do": 0.2},
}

# ======================================================================
# 内梅罗综合污染指数等级划分 (相对目标水质等级限值)
# ======================================================================

NEMEROW_GRADE_THRESHOLDS: List[Tuple[float, str, str, str]] = [
    # (上限, 等级标识, 中文标签, 颜色)
    (1.0,  "normal",   "达标",     "#2E8B57"),
    (2.0,  "mild",     "轻污染",   "#FFA500"),
    (3.0,  "moderate", "中污染",   "#FF4500"),
    (5.0,  "severe",   "重污染",   "#DC143C"),
    (float("inf"), "critical", "严重污染", "#8B0000"),
]

# ======================================================================
# AnalysisConfig — 所有可配置参数
# ======================================================================

@dataclass
class AnalysisConfig:
    """水质分析统一配置。

    所有字段都有合理的默认值, 可通过属性赋值在运行时覆盖。
    """

    # ---- 目标水质等级 ----
    target_water_class: str = "V"
    """目标水质等级: I, II, III, IV, V。黑点检测以此为基线计算超标倍数。"""

    # ---- DBSCAN 聚类参数 ----
    dbscan_eps: float = 0.0003
    """旧配置的角半径（度）；换算为球面距离约33.36m，不再以经纬度平面计算。"""

    dbscan_min_samples: int = 5
    """DBSCAN 最小成簇点数。"""

    dbscan_eps_m: float | None = None
    """米制邻域半径。None时把旧dbscan_eps按球面角距离转换，兼容旧配置。"""

    dbscan_time_gap_min: float | None = 30.
    """热点相邻观测最大时间差；None表示仅空间聚类。工程门限，需按任务核实。"""

    continuity_max_gap_min: float = 5.
    """时空支持度不跨越的观测空档（分钟），不是仪器校准参数。"""

    # ---- 时空维度黑点检测参数 ----
    temporal_window_min: int = 30
    """观测支持度窗口（分钟）；按超目标时长占完整窗口的比例计权。"""

    spatial_window_m: int = 50
    """空间连续性窗口 (米)。基于 Haversine 距离判定连续超标段。"""

    persistence_min_points: int = 3
    """支持度所需的最少观测数；不足时不加权，但保留点级阈值预警。"""

    time_weight_factor: float = 1.5
    """时间支持度加权系数；时间乘数为1+该系数×支持度。"""

    spatial_weight_factor: float = 1.5
    """空间连续性加权系数。连续 N 个点超标时施加的乘数。"""

    exceedance_severity_multipliers: Dict[float, float] = field(default_factory=lambda: {
        3.0: 1.5,    # Pi > 3 (超3倍) → 加权 1.5
        5.0: 2.0,    # Pi > 5 (超5倍) → 加权 2.0
        10.0: 3.0,   # Pi > 10 (超10倍) → 加权 3.0
    })
    """超标倍数严重度加权表: {阈值: 乘数}。"""

    # ---- 溯源参数 ----
    search_radius_m: int = 500
    """污染源搜索半径 (米)。"""

    expand_radius_m: int = 2000
    """扩展搜索半径 (米), 当基础半径内无结果时使用。"""

    upstream_search_m: int = 3000
    """上游搜索距离 (米), 沿河道向上搜索污染源。"""

    downstream_search_m: int = 1000
    """下游搜索距离 (米), 沿河道向下搜索污染源。"""

    # ---- POI 搜索关键词 ----
    pollution_keywords: List[str] = field(default_factory=lambda: [
        "工厂", "养殖场", "排污口", "化工厂", "污水处理厂", "工业园区",
    ])
    """高德 POI 搜索关键词列表。"""

    # ---- 多源溯源权重 ----
    source_weights: Dict[str, float] = field(default_factory=lambda: {
        "pollution_permit": 0.35,
        "national_station": 0.25,
        "amap_poi": 0.25,
        "land_use": 0.15,
    })
    """各数据源在综合评分中的权重。"""

    # ---- 启用的溯源数据源 ----
    enabled_sources: List[str] = field(default_factory=lambda: [
        "amap_poi", "national_station", "pollution_permit",
    ])
    """按优先级排列的启用数据源列表。"""

    # ---- 国控站点查询 ----
    national_station_radius_km: int = 50
    """国控站点搜索半径 (km)。"""

    # ---- 视频交叉印证参数 ----
    cross_validation_time_window_sec: int = 30
    """视频-传感器匹配时间窗口 (秒)。"""

    # ---- 黑臭等级颜色映射 ----
    @property
    def severity_colors(self) -> Dict[str, str]:
        return {
            "normal": "#2E8B57",
            "mild": "#FFA500",
            "moderate": "#FF4500",
            "severe": "#DC143C",
            "critical": "#8B0000",
        }

    # ---- 便捷方法 ----

    def get_target_limits(self) -> Dict[str, float]:
        """返回当前目标水质等级的限值。"""
        return GB3838_STANDARDS.get(self.target_water_class, GB3838_STANDARDS["V"])

    def get_black_odor_thresholds(self) -> Dict[str, Dict[str, float]]:
        """返回黑臭判定阈值。"""
        return BLACK_ODOR_THRESHOLDS

    def get_nemerow_grades(self) -> List[Tuple[float, str, str, str]]:
        """返回内梅罗等级划分。"""
        return NEMEROW_GRADE_THRESHOLDS

    def to_dict(self) -> dict:
        """导出为 JSON 可序列化字典。"""
        return {
            "target_water_class": self.target_water_class,
            "dbscan_eps": self.dbscan_eps,
            "dbscan_min_samples": self.dbscan_min_samples,
            "dbscan_eps_m": self.dbscan_eps_m,
            "dbscan_time_gap_min": self.dbscan_time_gap_min,
            "continuity_max_gap_min": self.continuity_max_gap_min,
            "temporal_window_min": self.temporal_window_min,
            "spatial_window_m": self.spatial_window_m,
            "persistence_min_points": self.persistence_min_points,
            "time_weight_factor": self.time_weight_factor,
            "spatial_weight_factor": self.spatial_weight_factor,
            "exceedance_severity_multipliers": self.exceedance_severity_multipliers,
            "search_radius_m": self.search_radius_m,
            "expand_radius_m": self.expand_radius_m,
            "upstream_search_m": self.upstream_search_m,
            "downstream_search_m": self.downstream_search_m,
            "pollution_keywords": self.pollution_keywords,
            "source_weights": self.source_weights,
            "enabled_sources": self.enabled_sources,
            "national_station_radius_km": self.national_station_radius_km,
            "cross_validation_time_window_sec": self.cross_validation_time_window_sec,
        }


# ======================================================================
# 全局单例管理
# ======================================================================

_DEFAULT_CONFIG: AnalysisConfig | None = None


def get_config() -> AnalysisConfig:
    """获取全局 AnalysisConfig 单例。"""
    global _DEFAULT_CONFIG
    if _DEFAULT_CONFIG is None:
        _DEFAULT_CONFIG = AnalysisConfig()
    return _DEFAULT_CONFIG


def set_config(config: AnalysisConfig) -> None:
    """设置全局 AnalysisConfig 单例。"""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = config


def reset_config() -> AnalysisConfig:
    """重置为默认配置。"""
    global _DEFAULT_CONFIG
    _DEFAULT_CONFIG = AnalysisConfig()
    return _DEFAULT_CONFIG


# 模块级默认实例 (向后兼容, config=None 时使用)
DEFAULT_CONFIG = AnalysisConfig()
