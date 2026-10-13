"""河道一维水动力-水质混合模型 (1D Hydrodynamic + Water Quality Model).

支持自动参数检测模式, 可从任意河道的走航监测数据自动推断所有参数。
"""

from .config import YayaoConfig, DEFAULT_CONFIG
from .cross_sections import CrossSection, generate_yayao_cross_sections
from .state import RiverModelState
from .parameter_estimator import (
    RiverParameterEstimator,
    estimate_config_from_data,
    estimate_config_from_gps,
)

__all__ = [
    "YayaoConfig",
    "DEFAULT_CONFIG",
    "CrossSection",
    "generate_yayao_cross_sections",
    "RiverModelState",
    "RiverParameterEstimator",
    "estimate_config_from_data",
    "estimate_config_from_gps",
]
