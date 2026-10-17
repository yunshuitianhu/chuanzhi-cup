"""水质 ADR (Advection-Diffusion-Reaction) 求解器。

算子分裂法:
  1. 控制体积面通量：迎风对流 + 守恒扩散
  2. 单元质量负荷：按真实覆盖时长和水体体积注入
  3. 反应步: Runge-Kutta 4 阶

反应动力学 (简化 QUAL2E 型, 无藻类):
  CBOD: dL/dt = -(K_d + K_s)·L
  NH₃:  dN/dt = -β₁·N + S_sed
  DO:   dO/dt = -K_d·L - SOD/H + K_a·(O_sat - O)

纵向离散系数: Fischer 公式 (1979)
复氧系数: Owens-Gibbs (浅水河道)
"""

from typing import Optional

import logging

import numpy as np

logger = logging.getLogger(__name__)

from .config import YayaoConfig
from .state import HydroResult, WQResult
from .transport import geometry, conservative_step


# ---- 反应动力学 ----

def _owens_gibbs_ka(u_ms: np.ndarray, H_m: np.ndarray) -> np.ndarray:
    """Owens-Gibbs 复氧系数 (d⁻¹)。

    适用范围: 浅水河道 (H < 3m), 低流速 (u < 0.5 m/s)。

    Ka = 5.32 * u^0.67 / H^1.85
    """
    u_safe = np.maximum(u_ms, 0.001)
    H_safe = np.maximum(H_m, 0.1)
    return 5.32 * u_safe ** 0.67 / H_safe ** 1.85


# Fischer 公式的截断上下限 (m²/s)。
#
# 这两条是**安全网**，不是物理规律。此前上下限是 (0.5, 500)，且因为 K 被写死
# 成常数 5.0 而**从未生效过**；改成按河道属性计算后**首次变得可达** ——
# 宽河道必然撞上限：B=200m 时公式给 ~740、B=500m 时给 ~4300，全被压成 500，
# 污染带被系统性低估约 8 倍，而且没有任何提示。
#
# 上限从 500 提到 5000：区域默认值里 lowland 段就有 400~500 m 宽的河道，
# 5000 足以容纳现实中的大河而不放行明显异常的输入。
# 下界保持 0.5。**触及任一边界都会记日志** —— 静默截断比截断本身更糟。
_K_CLIP_LO = 0.5
_K_CLIP_HI = 5000.0


def _fischer_dispersion(u_ms: np.ndarray, B_m: np.ndarray,
                         H_m: np.ndarray, u_star: np.ndarray) -> np.ndarray:
    """Fischer 纵向离散系数 (m²/s)。

    K = 0.011 * u² * B² / (H * u*)

    结果按 `_K_CLIP_LO/_K_CLIP_HI` 截断；**发生截断时记 warning**，
    否则使用者无法察觉扩散被压低了。
    """
    u_safe = np.maximum(u_ms, 0.001)
    B_safe = np.maximum(B_m, 1.0)
    H_safe = np.maximum(H_m, 0.1)
    u_star_safe = np.maximum(u_star, 0.001)
    K = 0.011 * u_safe ** 2 * B_safe ** 2 / (H_safe * u_star_safe)
    n_hi = int(np.sum(K > _K_CLIP_HI))
    n_lo = int(np.sum(K < _K_CLIP_LO))
    if n_hi or n_lo:
        logger.warning(
            "离散系数触及截断边界（上限 %g / 下限 %g）：%d 个断面撞上限、"
            "%d 个撞下限。原始值范围 %.1f~%.1f。截断会压低扩散 —— "
            "若上游河道参数（河宽/流速）无误，应上调 _K_CLIP_HI 而不是接受这个结果。",
            _K_CLIP_HI, _K_CLIP_LO, n_hi, n_lo, float(np.min(K)), float(np.max(K)))
    return np.clip(K, _K_CLIP_LO, _K_CLIP_HI)


# ---- 算子分裂 ----

def _advection_step(c: np.ndarray, u: np.ndarray, dx: float, dt: float) -> np.ndarray:
    """显式迎风格式求解对流方程 ∂c/∂t + u·∂c/∂x = 0。

    对每个断面 j:
      c_j^{n+1} = c_j^n - u_j·dt/dx * (c_j^n - c_{j-1}^n)   if u_j >= 0
      c_j^{n+1} = c_j^n - u_j·dt/dx * (c_{j+1}^n - c_j^n)   if u_j < 0
    """
    n = len(c)
    c_new = c.copy()
    for j in range(1, n - 1):
        if u[j] >= 0:
            c_new[j] = c[j] - u[j] * dt / dx * (c[j] - c[j - 1])
        else:
            c_new[j] = c[j] - u[j] * dt / dx * (c[j + 1] - c[j])
    # 上游边界: u >= 0 → 入流维持边界值; u < 0 → 零梯度
    if u[0] >= 0:
        c_new[0] = c[0]
    else:
        c_new[0] = c[1]
    # 下游边界: u >= 0 → 零梯度; u < 0 → 入流维持
    if u[-1] >= 0:
        c_new[-1] = c[-2]
    else:
        c_new[-1] = c[-1]
    return c_new


def _diffusion_step(c: np.ndarray, K: np.ndarray, dx: float, dt: float) -> np.ndarray:
    """显式中心差分求解扩散方程 ∂c/∂t = K·∂²c/∂x²。

    c_j^{n+1} = c_j^n + K_j·dt/dx² * (c_{j+1}^n - 2c_j^n + c_{j-1}^n)
    """
    n = len(c)
    c_new = c.copy()
    r = dt / (dx * dx)
    for j in range(1, n - 1):
        c_new[j] = c[j] + K[j] * r * (c[j + 1] - 2.0 * c[j] + c[j - 1])
    # 边界: 零通量
    c_new[0] = c_new[1]
    c_new[-1] = c_new[-2]
    return c_new


def _reaction_step(cbod: np.ndarray, nh3: np.ndarray, do_: np.ndarray,
                   H: np.ndarray, u: np.ndarray,
                   K_d: float, K_s: float, beta1: float, SOD: float,
                   O_sat: float, T_C: float, dt_s: float,
                   theta_d: float, theta_n: float, theta_rea: float) -> tuple:
    """RK4 求解反应步 dC/dt = f(C)。

    注意: 反应速率常数单位为 d⁻¹, dt_s 为秒, 需转换为天。
    """
    dt_day = dt_s / 86400.0  # 秒 → 天
    temp_fac_d = theta_d ** (T_C - 20.0)
    temp_fac_n = theta_n ** (T_C - 20.0)
    temp_fac_rea = theta_rea ** (T_C - 20.0)

    Ka = _owens_gibbs_ka(u, H)
    n = len(cbod)

    def rhs(L, N, O):
        dL = -(K_d * temp_fac_d + K_s) * L
        dN = -beta1 * temp_fac_n * N
        dO = (-K_d * temp_fac_d * L
              - SOD / np.maximum(H, 0.1)
              + Ka * temp_fac_rea * (O_sat - O))
        return dL, dN, dO

    # RK4 (使用 dt_day 因为速率常数为 d⁻¹)
    L1, N1, O1 = rhs(cbod, nh3, do_)
    L2, N2, O2 = rhs(cbod + 0.5 * dt_day * L1, nh3 + 0.5 * dt_day * N1, do_ + 0.5 * dt_day * O1)
    L3, N3, O3 = rhs(cbod + 0.5 * dt_day * L2, nh3 + 0.5 * dt_day * N2, do_ + 0.5 * dt_day * O2)
    L4, N4, O4 = rhs(cbod + dt_day * L3, nh3 + dt_day * N3, do_ + dt_day * O3)

    cbod_new = cbod + dt_day / 6.0 * (L1 + 2.0 * L2 + 2.0 * L3 + L4)
    nh3_new = nh3 + dt_day / 6.0 * (N1 + 2.0 * N2 + 2.0 * N3 + N4)
    do_new = do_ + dt_day / 6.0 * (O1 + 2.0 * O2 + 2.0 * O3 + O4)

    return (
        np.maximum(cbod_new, 0.0),
        np.maximum(nh3_new, 0.0),
        np.maximum(do_new, 0.0),
    )


# ---- 主求解器 ----

def solve_water_quality(
    config: YayaoConfig,
    hydro: HydroResult,
    pollutant_type: str = "ammonia",
    load_kg: float = 50.0,
    chainage_m: float = 1600.0,
    duration_min: float = 30.0,
    simulation_hours: float = 24.0,
    dt_s: float = 60.0,
) -> WQResult:
    """运行水质 ADR 模拟 (含污染事件)。

    Args:
        config: 雅瑶水道配置
        hydro: 水动力结果 (由 solve_hydrodynamics 产出)
        pollutant_type: "ammonia" | "cbod" | "cod" | "conservative"
        load_kg: 污染物总质量 (kg)
        chainage_m: 污染源位置 (m, 从上游起算)
        duration_min: 排放持续时间 (min)
        simulation_hours: 模拟总时长 (h)
        dt_s: 水质时间步长 (s), 建议 ≤ 水动力步长

    Returns:
        WQResult 包含各组分浓度时间序列
    """
    if pollutant_type not in ('ammonia', 'cbod'):
        raise ValueError('仅支持独立实现的 ammonia/cbod 组分')
    values = (load_kg, chainage_m, duration_min, simulation_hours, dt_s)
    if not all(np.isfinite(value) for value in values) or load_kg < 0 or min(duration_min, simulation_hours, dt_s) <= 0:
        raise ValueError('负荷须非负，时长和步长须为正有限数')
    if not hydro.chainage[0] <= chainage_m <= hydro.chainage[-1]:
        raise ValueError('污染源桩号超出当前河道范围')
    n_cs = len(hydro.chainage)
    chainage = hydro.chainage
    t_hydro = hydro.t * 3600.0  # 转为秒
    duration_sec = duration_min * 60.0

    # ---- 查找污染源位置 (最近断面) ----
    source_idx = int(np.argmin(np.abs(chainage - chainage_m)))

    # ---- 参数 ----
    K_d = config.k_cbod_decay        # d⁻¹
    K_s = 0.05                        # CBOD 沉降速率 d⁻¹
    beta1 = config.k_nitrification   # 硝化速率 d⁻¹
    SOD = config.k_sediment_oxygen_demand  # g/m²/d
    O_sat = config.do_saturation_mgL
    T_C = config.water_temp_C

    theta_d = config.theta_cbod
    theta_n = config.theta_nitrification
    theta_rea = config.theta_reaeration

    # ---- 计算离散系数 ----
    # 纵向离散系数物理上取决于河宽、水深、流速与摩阻流速。
    #
    # 原实现写死 B=55.0 m、K=5.0 m²/s —— 都是雅瑶水道的标定值。换任何
    # 宽度/流速不同的河流，污染物扩散范围仍按雅瑶算，这是泛化的硬伤。
    # 本模块早已定义 Fischer 公式（_fischer_dispersion），但从未被调用。
    #
    # 代表性水力条件取水动力结果的时间平均：K 在时间循环外算一次，
    # 与既有结构保持一致（避免把 K 的重算塞进每个子步）。
    g = 9.81
    B_array = np.ones(n_cs) * float(config.width_avg_m)

    try:
        vel = np.asarray(hydro.velocity, dtype=float)
        u_rep = np.abs(vel).mean(axis=0) if vel.ndim == 2 else np.abs(vel)
    except (TypeError, ValueError, AttributeError):
        u_rep = np.ones(n_cs) * float(config.velocity_avg_ms)

    try:
        if hydro.area is not None:
            A_rep = np.asarray(hydro.area, dtype=float).mean(axis=0)
        else:
            A_rep = B_array * float(config.depth_avg_m)
    except (TypeError, ValueError, AttributeError):
        A_rep = B_array * float(config.depth_avg_m)

    H_rep = np.maximum(A_rep / np.maximum(B_array, 1.0),
                       float(config.depth_range_m[0]) * 0.5)

    # 摩阻流速 u* = sqrt(g·H·Sf)，Sf 由 Manning 反算
    u_safe = np.maximum(u_rep, 0.001)
    S_f = (float(config.mannings_n) * u_safe
           / np.maximum(H_rep, 0.1) ** (2.0 / 3.0)) ** 2
    u_star = np.sqrt(g * H_rep * S_f)

    K_array = _fischer_dispersion(u_rep, B_array, H_rep, u_star)

    # ---- 初始浓度场 ----
    cbod = np.ones(n_cs) * config.bod_cbod_mgL
    nh3 = np.ones(n_cs) * config.ammonia_nh3_mgL
    do_ = np.ones(n_cs) * config.dissolved_oxygen_mgL

    # ---- 控制体积质量推进；源项 = 质量速率 × 实际覆盖时长 / 单元水体体积 ----
    total_seconds = simulation_hours * 3600.0
    total_steps = int(np.ceil(total_seconds / dt_s))
    output_interval = max(total_steps // max(total_steps // 60, 1), 1)
    components = np.stack([cbod, nh3, do_])
    upstream, downstream = components[:, 0].copy(), components[:, -1].copy()
    initial_area = hydro.area[0] if hydro.area is not None else np.full(n_cs, config.width_avg_m * config.depth_avg_m)
    volume, _, _, _ = geometry(chainage, initial_area, hydro.discharge[0], K_array)
    initial_mass = (components * volume).sum(axis=1)
    injected = np.zeros(3)
    inflow = np.zeros(3)
    outflow = np.zeros(3)
    reaction_change = np.zeros(3)
    mass_rate_gs = load_kg * 1000.0 / duration_sec
    source_component = 1 if pollutant_type == 'ammonia' else 0
    times, snapshots = [0.0], [components.copy()]
    max_stability = 0.0

    for step in range(1, total_steps + 1):
        t0 = (step - 1) * dt_s
        t1 = min(step * dt_s, total_seconds)
        step_dt = t1 - t0
        if len(t_hydro) > 1:
            position = np.interp((t0 + t1) / 2, t_hydro, np.arange(len(t_hydro)))
            lo = int(np.floor(position))
            hi = min(lo + 1, len(t_hydro) - 1)
            fraction = position - lo
            u_now = (1 - fraction) * hydro.velocity[lo] + fraction * hydro.velocity[hi]
            Q_now = (1 - fraction) * hydro.discharge[lo] + fraction * hydro.discharge[hi]
            A_now = ((1 - fraction) * hydro.area[lo] + fraction * hydro.area[hi]
                     if hydro.area is not None else initial_area)
        else:
            u_now, Q_now, A_now = hydro.velocity[0], hydro.discharge[0], initial_area

        new_volume, q_face, conductance, rate = geometry(chainage, A_now, Q_now, K_array)
        components *= volume / new_volume  # 几何随时间变化时保持原有质量。
        volume = new_volume
        # 对流与扩散共同限制显式步长，包含反向流和边界半单元。
        n_sub = max(1, int(np.ceil(step_dt * rate / .9)))
        sub_dt = step_dt / n_sub
        max_stability = max(max_stability, rate * sub_dt)
        H_now = np.maximum(A_now / config.width_avg_m, .1)

        for sub in range(n_sub):
            sub_t0 = t0 + sub * sub_dt
            overlap = max(0., min(sub_t0 + sub_dt, duration_sec) - sub_t0)
            added = mass_rate_gs * overlap
            components[source_component, source_idx] += added / volume[source_idx]
            injected[source_component] += added
            components, step_in, step_out = conservative_step(
                components, volume, q_face, conductance, sub_dt, upstream, downstream)
            inflow += step_in
            outflow += step_out
            before_reaction = (components * volume).sum(axis=1)
            components = np.stack(_reaction_step(
                *components, H_now, np.abs(u_now), K_d, K_s, beta1, SOD,
                O_sat, T_C, sub_dt, theta_d, theta_n, theta_rea))
            reaction_change += (components * volume).sum(axis=1) - before_reaction

        if step % output_interval == 0 or step == total_steps:
            times.append(t1 / 3600)
            snapshots.append(components.copy())

    t_out = np.asarray(times)
    output = np.stack(snapshots)
    cbod_out, nh3_out, do_out = output[:, 0], output[:, 1], output[:, 2]
    final_mass = (components * volume).sum(axis=1)
    residual = final_mass - initial_mass - injected - inflow + outflow - reaction_change
    balance = {name: {key: float(value[i] / 1000) for key, value in {
        'initial_kg': initial_mass, 'injected_kg': injected, 'inflow_kg': inflow,
        'outflow_kg': outflow, 'reaction_change_kg': reaction_change,
        'final_kg': final_mass, 'residual_kg': residual}.items()}
        for i, name in enumerate(('cbod', 'ammonia', 'do'))}

    return WQResult(
        t=t_out,
        chainage=chainage,
        cbod=cbod_out,
        ammonia=nh3_out,
        dissolved_oxygen=do_out,
        params={
            "pollutant_type": pollutant_type,
            "load_kg": load_kg,
            "chainage_m": chainage_m,
            "source_section_id": source_idx,
            "mass_balance": balance,
            "max_transport_stability": max_stability,
            "duration_min": duration_min,
            "simulation_hours": simulation_hours,
        },
    )
