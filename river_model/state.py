"""河道模型状态管理。"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .config import RiverConfig
from .cross_sections import CrossSection


@dataclass
class HydroResult:
    """一次水动力模拟的结果。"""
    t: np.ndarray                     # 相对模拟起点的时间 (h), shape (nt,)
    chainage: np.ndarray             # 桩号 (m), shape (ncs,)
    water_level: np.ndarray          # 水位 (m), shape (nt, ncs)
    discharge: np.ndarray            # 流量 (m³/s), shape (nt, ncs)
    velocity: np.ndarray             # 流速 (m/s), shape (nt, ncs)
    area: np.ndarray                 # 过水面积 (m²), shape (nt, ncs)
    params: dict = field(default_factory=dict)  # 运行时参数


@dataclass
class WQResult:
    """一次水质模拟的结果。"""
    t: np.ndarray                    # 相对模拟起点的时间 (h), shape (nt,)
    chainage: np.ndarray            # 桩号 (m), shape (ncs,)
    cbod: Optional[np.ndarray] = None    # (nt, ncs)
    ammonia: Optional[np.ndarray] = None # (nt, ncs)
    nitrate: Optional[np.ndarray] = None  # (nt, ncs)
    dissolved_oxygen: Optional[np.ndarray] = None  # (nt, ncs)
    phosphate: Optional[np.ndarray] = None  # (nt, ncs)
    params: dict = field(default_factory=dict)


@dataclass
class RiverModelState:
    """河道模型全局状态。"""
    config: RiverConfig = field(default_factory=RiverConfig)
    cross_sections: List[CrossSection] = field(default_factory=list)
    hydro_result: Optional[HydroResult] = None
    wq_result: Optional[WQResult] = None
    scenarios: Dict[str, dict] = field(default_factory=dict)
    engine_type: str = "pure_python"
    model_initialized: bool = False
    geometry_revision: int = 0

    def invalidate_hydro(self):
        self.hydro_result = None
        self.wq_result = None
        self.scenarios.clear()

    def has_hydro(self) -> bool:
        return self.hydro_result is not None

    def has_wq(self) -> bool:
        if self.wq_result is None:
            return False
        origin = self.wq_result.params.get('hydro_run_id')
        return origin is None or (self.hydro_result is not None
                                  and origin == self.hydro_result.params.get('run_id'))
