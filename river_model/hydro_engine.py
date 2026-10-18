"""可移植水动力入口：统一使用项目自带求解器，不探测本机商业软件。"""
from .config import RiverConfig
from .state import HydroResult


def detect_engine() -> str:
    return 'pure_python'


def get_engine_info() -> str:
    return '项目内置 Python 一维求解器；无需 HEC-RAS、COM 或 Windows'


def is_nonconstant(tidal_amplitude_m: float) -> bool:
    try:
        return abs(float(tidal_amplitude_m)) > 0
    except (TypeError, ValueError):
        return True


def run_hydrodynamic(config: RiverConfig, cross_sections,
                    upstream_flow_m3s=None, downstream_stage_m=None,
                    tidal_amplitude_m=0., tidal_period_h=None,
                    duration_h=24., dt_s=60., output_interval_min=30.) -> HydroResult:
    from .pure_py_hydro import solve_hydrodynamics
    if (config.avg_discharge_m3s if upstream_flow_m3s is None else upstream_flow_m3s) < 0:
        raise ValueError('当前入口要求沿已定义上下游方向的非负入流；反向河段请核实并调整断面方向')
    return solve_hydrodynamics(config, cross_sections,
        config.avg_discharge_m3s if upstream_flow_m3s is None else upstream_flow_m3s,
        config.high_water_mean_m if downstream_stage_m is None else downstream_stage_m,
        tidal_amplitude_m, config.m2_period_h if tidal_period_h is None else tidal_period_h,
        duration_h, dt_s, output_interval_min)
