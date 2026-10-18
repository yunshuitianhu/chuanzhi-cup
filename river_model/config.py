"""通用一维河段配置。默认值是情景模板；真实应用须提供并核实几何、边界及水质参数。"""

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class RiverConfig:
    """通用河段配置；默认是未率定的理想情景，不对应任何真实河流。"""

    # ---- 河道基本信息 ----
    river_name: str = "未命名河段（情景模板）"
    location: str = "未提供"
    water_system: str = "未提供"
    catchment_area_km2: float | None = None

    # ---- 几何参数 ----
    length_km: float = 1.0
    length_m: float = 1000.0
    width_avg_m: float = 30.0
    width_range_m: Tuple[float, float] = (20.0, 40.0)
    depth_avg_m: float = 2.0
    depth_range_m: Tuple[float, float] = (1.8, 2.5)
    depth_max_m: float | None = None
    cross_section_area_range_m2: Tuple[float, float] | None = None
    bed_elevation_avg_m: float = 0.0
    bed_elevation_range_m: Tuple[float, float] | None = None
    bank_elevation_m: float = 5.0

    # ---- 水力学参数 ----
    mannings_n: float = 0.028
    mannings_n_range: Tuple[float, float] = (0.025, 0.035)
    design_discharge_m3s: float | None = None
    avg_discharge_m3s: float = 6.0
    discharge_range_m3s: Tuple[float, float] | None = None
    velocity_avg_ms: float = 0.1
    velocity_range_ms: Tuple[float, float] | None = None
    longitudinal_slope: float = 0.0001

    # ---- 潮汐边界 ----
    tidal_range_m: float = 0.0
    tidal_range_limits_m: Tuple[float, float] | None = None
    m2_period_h: float = 12.42
    upstream_structure: str = "未提供"
    downstream_connection: str = "未提供"
    # 局部高程中的下游参考水位
    high_water_mean_m: float = 2.0
    low_water_mean_m: float = 2.0

    # ---- 水质基线 (V类) ----
    ammonia_nh3_mgL: float = 3.0
    nitrate_n_mgL: float = 0.0        # NO3-N 初值；无实测时为情景假设，不是观测值
    dissolved_oxygen_mgL: float = 2.0
    cod_mgL: float = 60.0
    bod_cbod_mgL: float = 20.0
    phosphate_mgL: float = 0.4
    water_temp_C: float = 25.0

    # ---- GB3838-2002 V类标准限值 ----
    standard_nh3_v: float = 2.0
    standard_do_v: float = 2.0
    standard_cod_v: float = 40.0
    standard_bod_v: float = 10.0

    # ---- 数值参数 ----
    n_cross_sections: int = 50
    dt_default_s: float = 60.0
    dt_min_s: float = 10.0
    dt_max_s: float = 300.0
    preissmann_phi: float = 0.5
    preissmann_theta: float = 0.6
    max_newton_iter: int = 20
    newton_tolerance: float = 0.001  # 1mm 水位收敛容差 (HEC-RAS 典型值 ~3mm)

    # ---- 反应动力学参数 (20°C) ----
    k_cbod_decay: float = 0.25        # CBOD衰减速率 d⁻¹
    k_nitrification: float = 0.15     # 硝化速率 d⁻¹
    oxygen_per_nitrified_n: float = 4.57  # 完全硝化理论耗氧 mgO2/mgN，不含生物合成
    oxygen_half_sat_nitrification: float = 0.6  # mgO2/L；以下为待率定情景参数
    oxygen_half_sat_cbod: float = 0.5
    oxygen_half_sat_sod: float = 0.5
    k_organic_n_hydrolysis: float = 0.20  # 有机氮水解 d⁻¹
    k_reaeration_base: float = 5.32   # Owens-Gibbs 复氧系数
    k_sediment_oxygen_demand: float = 1.0  # 底泥耗氧 g/m²/d
    theta_cbod: float = 1.047
    theta_nitrification: float = 1.072
    theta_reaeration: float = 1.024
    do_saturation_mgL: float = 8.25   # 25°C 饱和溶解氧

    # ---- 坐标参考 ----
    center_lat: float | None = None
    center_lon: float | None = None

    # ---- 断面生成参数 ----
    bottom_width_m: float = 22.0
    side_slope: float = 2.0
    vertical_datum: str = "用户自定局部高程；断面及水位须使用同一基准"

    # ---- 参数来源元数据 (auto-detection mode) ----
    param_sources: Dict[str, str] = field(default_factory=dict)
    # 每个参数的来源: "gps" | "osm" | "satellite" | "regional_defaults" | "known_river_database" | "user"
    param_confidence: Dict[str, float] = field(default_factory=dict)
    # 每个参数的置信度: 0.0 (完全是猜测) ~ 1.0 (高置信度测量值)

    # ==================================================================
    # 工厂方法: 从监测数据自动推断参数
    # ==================================================================

    @classmethod
    def from_monitoring_data(
        cls,
        df: "pd.DataFrame",  # type: ignore
        amap_key: str = None,
        cloud_token: str = None,
    ) -> "YayaoConfig":
        """从走航监测数据自动推断所有河道参数。

        使用多层降级策略:
          GPS 轨迹 → OSM 水道查询 → 卫星影像 → 区域默认值 → 监测数据推断

        Args:
            df: 走航监测数据 DataFrame (含 GPS 列)
            amap_key: 高德 API Key (可选, 用于逆地理编码和 POI 搜索)
            cloud_token: 星图地球 Cloud Token (可选, 用于卫星宽度估计)

        Returns:
            RiverConfig；GPS 与水质来自输入，其余建议含经验假设，未经现场率定
        """
        from .parameter_estimator import RiverParameterEstimator
        estimator = RiverParameterEstimator(amap_key=amap_key, cloud_token=cloud_token)
        return estimator.estimate(df)


# 兼容旧导入名；不会自动加载雅瑶水道参数。
YayaoConfig = RiverConfig
DEFAULT_CONFIG = RiverConfig()
