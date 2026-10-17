"""水动力引擎调度器 — 检测 HEC-RAS 可用性, 选择引擎路径。"""

import os
from typing import List

from .config import YayaoConfig
from .cross_sections import CrossSection
from .state import HydroResult


def detect_engine() -> str:
    """检测可用的引擎类型。

    Returns:
        "hecras" — HEC-RAS COM 可用 (需管理员运行 Register batch)
        "pure_python" — 使用纯 Python Preissmann 求解器 (默认)
    """
    try:
        import win32com.client
        controller = win32com.client.Dispatch("RAS70.HECRASController")
        # 尝试获取版本号验证 COM 连通性
        _ = controller.HECRASVersion
        return "hecras"
    except Exception:
        pass

    return "pure_python"


def is_nonconstant(tidal_amplitude_m: float) -> bool:
    """判断工况是否属非恒定流。

    下游潮位边界使流动随时间变化, 属非恒定流。HEC-RAS COM 分支只支持
    恒定流 (见 hecras_bridge 模块说明), 若由其处理非恒定工况, 得到的将
    是一张恒定流水面线, 潮汐边界与污染物输移过程会完全丢失。
    因此非恒定流一律交由纯 Python Preissmann 求解器处理。

    Args:
        tidal_amplitude_m: 潮汐振幅 (m)

    Returns:
        True 表示非恒定流 (应使用纯 Python 求解器)
    """
    try:
        return abs(float(tidal_amplitude_m)) > 0.0
    except (TypeError, ValueError):
        # 参数异常时保守选择纯 Python — 它总能给出随时间演化的解
        return True


def get_engine_info() -> str:
    engine = detect_engine()
    ras_installed = False
    from .hecras_bridge import is_hecras_available
    ras_installed = is_hecras_available()

    if engine == "hecras":
        return ("HEC-RAS 7.0 (COM) 可用 — 仅用于恒定流工况; "
                "非恒定流(潮汐)工况自动使用纯 Python Preissmann 求解器")
    elif ras_installed:
        return "纯 Python Preissmann 隐式格式求解器 (已检测到 HEC-RAS，当前进程无法使用 COM；需检查注册和运行权限)"
    else:
        return "纯 Python Preissmann 隐式格式求解器"


def run_hydrodynamic(
    config: YayaoConfig,
    cross_sections: List[CrossSection],
    upstream_flow_m3s: float = 6.9,
    downstream_stage_m: float = 0.83,
    tidal_amplitude_m: float = 0.8,
    tidal_period_h: float = 12.42,
    duration_h: float = 24.0,
    dt_s: float = 60.0,
    output_interval_min: float = 30.0,
) -> HydroResult:
    """运行水动力模拟 (自动选择引擎, HEC-RAS 失败时降级到纯 Python)。

    引擎选择规则:
      1. 非恒定流 (潮汐振幅 != 0) → **强制**纯 Python Preissmann 求解器。
         HEC-RAS COM 分支仅支持恒定流, 若由其处理潮汐工况, 返回的将是
         一张恒定流水面线 (实测: 输出帧数=1, 水位变幅=0.0), 潮汐边界
         与污染物输移过程完全丢失。
      2. 恒定流且 HEC-RAS COM 可用 → HEC-RAS。
      3. 其余 → 纯 Python 求解器。
    """
    engine = "pure_python" if is_nonconstant(tidal_amplitude_m) else detect_engine()

    if engine == "hecras":
        try:
            from .hecras_bridge import run_hecras_simulation
            return run_hecras_simulation(
                config, cross_sections,
                upstream_flow_m3s, downstream_stage_m,
                tidal_amplitude_m, tidal_period_h,
                duration_h, dt_s, output_interval_min,
            )
        except Exception as e:
            # HEC-RAS 失败 → 打印警告, 降级到纯 Python
            import warnings
            warnings.warn(f"HEC-RAS 运行失败, 降级到纯 Python: {e}")

    from .pure_py_hydro import solve_hydrodynamics
    return solve_hydrodynamics(
        config, cross_sections,
        upstream_flow_m3s, downstream_stage_m,
        tidal_amplitude_m, tidal_period_h,
        duration_h, dt_s, output_interval_min,
    )
