"""水质 ADR (Advection-Diffusion-Reaction) 求解器。

算子分裂法:
  1. 控制体积面通量：迎风对流 + 守恒扩散
  2. 单元质量负荷：按真实覆盖时长和水体体积注入
  3. 反应步: Runge-Kutta 4 阶

反应动力学 (简化 QUAL2E 型, 无藻类):
  CBOD: dL/dt = -(K_d·f_L(O) + K_s)·L
  NH₃-N: dN/dt = -β₁·f_N(O)·N，等量生成 NO3-N
  DO:   dO/dt = -K_d·f_L(O)·L - 4.57·β₁·f_N(O)·N
                 - SOD/H·f_S(O) + K_a·(O_sat - O)
  f(O) = O/(K_O+O)。参数为未现场率定的简化情景值，无反硝化/藻类/碱度过程。

纵向离散系数: Fischer 公式 (1979)
复氧系数: Owens-Gibbs (浅水河道)
"""

from typing import Optional

import logging

import numpy as np

logger = logging.getLogger(__name__)

from .config import RiverConfig
from .state import HydroResult, WQResult
from .transport import geometry, conservative_step
from .wq_grid import refined_chainage, check_work_budget


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
            "%d 个撞下限。原始值范围 %.1f~%.1f。上限可能压低、下限可能放大扩散；"
            "请核查几何和流速并率定弥散系数，不能将截断值当作实测参数。",
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
                   theta_d: float, theta_n: float, theta_rea: float,
                   oxygen_per_n: float = 4.57, oxygen_half_sat_n: float = .6,
                   oxygen_half_sat_cbod: float = .5, oxygen_half_sat_sod: float = .5,
                   reaeration_base: float = 5.32) -> tuple:
    """带氧限制的耦合 RK4；按反应损失率细分，不能靠截零消除透支的氧。

    速率单位 d⁻¹，dt_s 单位秒。NH3-N 的反应损失全部视为 NO3-N 生成，
    由调用方同步推进硝态氮。4.57 为完全硝化理论耗氧，不含生物合成。
    """
    scalar = (K_d, K_s, beta1, SOD, O_sat, T_C, dt_s, theta_d, theta_n, theta_rea,
              oxygen_per_n, oxygen_half_sat_n, oxygen_half_sat_cbod, oxygen_half_sat_sod, reaeration_base)
    if (not np.all(np.isfinite(scalar)) or min(K_d, K_s, beta1, SOD, O_sat, dt_s, reaeration_base) < 0
            or min(theta_d, theta_n, theta_rea, oxygen_per_n,
                   oxygen_half_sat_n, oxygen_half_sat_cbod, oxygen_half_sat_sod) <= 0):
        raise ValueError('反应参数须有限，速率/时长/饱和氧须非负，温度系数及氧限制参数须为正')
    values = np.stack(np.broadcast_arrays(cbod, nh3, do_)).astype(float, copy=True)
    if np.any(~np.isfinite(values)) or np.any(values < 0):
        raise ValueError('反应初始浓度须为非负有限数')
    if np.any(~np.isfinite(H)) or np.any(np.asarray(H) <= 0) or np.any(~np.isfinite(u)):
        raise ValueError('反应水深须为正有限数，流速须有限')
    dt_day = dt_s / 86400.0
    temp_fac_d = theta_d ** (T_C - 20.0)
    temp_fac_n = theta_n ** (T_C - 20.0)
    temp_fac_rea = theta_rea ** (T_C - 20.0)

    kd, kn = K_d * temp_fac_d, beta1 * temp_fac_n
    # 默认5.32保持原结果；显式配置为0可关闭复氧，不能继续默用硬编码系数。
    ka = _owens_gibbs_ka(np.abs(u), H) * temp_fac_rea * (reaeration_base / 5.32)
    sod = SOD / np.asarray(H)
    # L、N 在反应期间只减不增；以 K_O 给氧损失系数取上界，限制 RK4 的子步。
    loss_bound = max(kd + K_s, kn, float(np.max(ka
        + kd * values[0] / oxygen_half_sat_cbod
        + oxygen_per_n * kn * values[1] / oxygen_half_sat_n
        + sod / oxygen_half_sat_sod)))
    if not np.isfinite(loss_bound):
        raise ValueError('反应速率溢出，请检查温度或动力学参数')
    n_sub = max(1, int(np.ceil(dt_day * loss_bound / .25)))
    if n_sub > 100000:
        raise ValueError('反应刚性过高，请减小步长或核查参数')
    h = dt_day / n_sub

    def rhs(y):
        L, N, O = y
        oxidation = kd * L * O / (oxygen_half_sat_cbod + O)
        nitrification = kn * N * O / (oxygen_half_sat_n + O)
        sediment_demand = sod * O / (oxygen_half_sat_sod + O)
        return np.stack((-(oxidation + K_s * L), -nitrification,
                         ka * (O_sat - O) - oxidation - oxygen_per_n * nitrification - sediment_demand))

    for _ in range(n_sub):
        k1 = rhs(values)
        k2 = rhs(values + h * .5 * k1)
        k3 = rhs(values + h * .5 * k2)
        k4 = rhs(values + h * k3)
        values += h / 6 * (k1 + 2*k2 + 2*k3 + k4)
        if np.any(~np.isfinite(values)) or np.any(values < -1e-12):
            raise FloatingPointError('耦合反应出现非物理浓度，请检查参数或减小步长')
    return tuple(np.maximum(values, 0.))  # 仅清除浮点舍入量级的负数


# ---- 主求解器 ----

def solve_water_quality(
    config: RiverConfig,
    hydro: HydroResult,
    pollutant_type: str = "ammonia",
    load_kg: float = 50.0,
    chainage_m: Optional[float] = None,
    duration_min: float = 30.0,
    simulation_hours: float = 24.0,
    dt_s: float = 60.0,
    source_start_h: float = 0.0,
    output_interval_s: float = 60.0,
    output_times_h: Optional[np.ndarray] = None,
    point_sources: Optional[list[dict]] = None,
    wq_refinement: int = 1,
) -> WQResult:
    """运行水质 ADR 模拟 (含污染事件)。

    Args:
        config: 通用河段配置
        hydro: 水动力结果 (由 solve_hydrodynamics 产出)
        pollutant_type: "ammonia" | "cbod"
        load_kg: 污染物总质量 (kg)
        chainage_m: 污染源位置 (m, 从上游起算)
        duration_min: 排放持续时间 (min)
        simulation_hours: 模拟总时长 (h)
        dt_s: 水质时间步长 (s), 建议 ≤ 水动力步长
        source_start_h: 相对模拟起点的排放开始时刻 (h)，允许晚于模拟终点
        output_interval_s: 默认输出间隔 (s)，独立于积分步长
        output_times_h: 指定观测输出时刻 (h)，替代默认等间隔输出；另保留首尾及排放边界
        point_sources: 仅供受控实验的多排放事件。每项须给出 pollutant_type、load_kg、
            chainage_m、duration_min、source_start_h；提供时替代单源参数，空列表表示无排放。
            同一物理源的多次排放可重复 source_id。Web 入口尚不开放多源反演。
        wq_refinement: 每个水动力区间细分为1/2/4/8段水质网格。内核默认1兼容旧实验，
            Web和工具默认4。细分线性插值A/Q，不代表增加实测几何信息。

    Returns:
        WQResult 包含各组分浓度时间序列
    """
    if pollutant_type not in ('ammonia', 'cbod'):
        raise ValueError('仅支持独立实现的 ammonia/cbod 组分')
    if chainage_m is None:
        chainage_m = float((hydro.chainage[0] + hydro.chainage[-1]) / 2)
    if (hydro.params.get('engine') == 'pure_python_preissmann'
            and hydro.params.get('tidal_amplitude_m', 0)):
        raise ValueError('当前纯 Python 潮汐水动力未通过水量守恒验收，不能用于水质计算')
    values = (load_kg, chainage_m, duration_min, simulation_hours, dt_s, source_start_h, output_interval_s)
    if (not all(np.isfinite(value) for value in values) or min(load_kg, source_start_h) < 0
            or min(duration_min, simulation_hours, dt_s, output_interval_s) <= 0):
        raise ValueError('负荷和排放开始时刻须非负，时长和步长须为正有限数')
    if not hydro.chainage[0] <= chainage_m <= hydro.chainage[-1]:
        raise ValueError('污染源桩号超出当前河道范围')
    native_chainage = np.asarray(hydro.chainage, dtype=float)
    chainage = refined_chainage(native_chainage, wq_refinement)
    n_cs = len(chainage)
    def on_wq_grid(values):
        return (np.asarray(values) if wq_refinement == 1
                else np.interp(chainage, native_chainage, values))
    t_hydro = hydro.t * 3600.0  # 转为秒
    total_seconds = simulation_hours * 3600.0
    if point_sources is not None and not isinstance(point_sources, list):
        raise ValueError('point_sources 须为排放事件列表')
    events = ([dict(pollutant_type=pollutant_type, load_kg=load_kg, chainage_m=chainage_m,
                    duration_min=duration_min, source_start_h=source_start_h)]
              if point_sources is None else point_sources)
    releases = []
    for event_number, event in enumerate(events, 1):
        if not isinstance(event, dict):
            raise ValueError('每个排放事件须为参数字典')
        try:
            kind = event['pollutant_type']
            mass, position, minutes, start = [float(event[key]) for key in
                ('load_kg', 'chainage_m', 'duration_min', 'source_start_h')]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('排放事件缺少必要数值参数') from exc
        if (kind not in ('ammonia', 'cbod') or not all(np.isfinite([mass, position, minutes, start]))
                or min(mass, start) < 0 or minutes <= 0
                or not hydro.chainage[0] <= position <= hydro.chainage[-1]):
            raise ValueError('排放事件的组分、负荷、桩号或时长无效')
        idx = int(np.argmin(np.abs(chainage - position)))
        releases.append(dict(source_id=str(event.get('source_id', f'source_{event_number}')),
            pollutant_type=kind, load_kg=mass, chainage_m=position, duration_min=minutes,
            source_start_h=start, start_s=start * 3600., end_s=start * 3600. + minutes * 60.,
            rate_gs=mass * 1000. / (minutes * 60.), component=1 if kind == 'ammonia' else 0,
            source_section_id=idx, source_effective_chainage_m=float(chainage[idx]),
            source_snap_distance_m=float(abs(chainage[idx] - position)), injected_kg=0.))
    if (t_hydro.ndim != 1 or not len(t_hydro) or not np.all(np.isfinite(t_hydro))
            or t_hydro[0] != 0 or np.any(np.diff(t_hydro) <= 0)):
        raise ValueError('水动力时间轴须从 0 开始且严格递增，单位为小时')
    # 单帧结果明示为恒定场；多帧场不可静默延长到其边界数据以外。
    if len(t_hydro) > 1 and t_hydro[-1] < total_seconds - 1e-7:
        raise ValueError('水动力时间范围未覆盖水质模拟，请先延长水动力模拟')

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
    B_array = np.ones(len(native_chainage)) * float(config.width_avg_m)

    try:
        vel = np.asarray(hydro.velocity, dtype=float)
        u_rep = np.abs(vel).mean(axis=0) if vel.ndim == 2 else np.abs(vel)
    except (TypeError, ValueError, AttributeError):
        u_rep = np.ones(len(native_chainage)) * float(config.velocity_avg_ms)

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

    # 在原水动力节点计算经验闭合，再插值；网格加密不更换物理闭合参数。
    K_array = on_wq_grid(_fischer_dispersion(u_rep, B_array, H_rep, u_star))
    u_rep = on_wq_grid(u_rep)

    # ---- 初始浓度场 ----
    cbod = np.ones(n_cs) * config.bod_cbod_mgL
    nh3 = np.ones(n_cs) * config.ammonia_nh3_mgL
    do_ = np.ones(n_cs) * config.dissolved_oxygen_mgL
    nitrate = np.ones(n_cs) * config.nitrate_n_mgL
    if np.any(~np.isfinite([cbod, nh3, do_, nitrate])) or min(cbod.min(), nh3.min(), do_.min(), nitrate.min()) < 0:
        raise ValueError('各组分初始浓度须为非负有限数')

    # ---- 控制体积质量推进；源项 = 质量速率 × 实际覆盖时长 / 单元水体体积 ----
    total_steps = int(np.ceil(total_seconds / dt_s))
    if output_times_h is None:
        if np.ceil(total_seconds / output_interval_s) > 10000:
            raise ValueError('输出时间点过多，请增大输出间隔（最多 10000 个区间）')
        output_seconds = np.arange(0., total_seconds, output_interval_s)
    else:
        requested = np.asarray(output_times_h, dtype=float)
        if (requested.ndim != 1 or len(requested) > 10000
                or not np.all(np.isfinite(requested))
                or np.any((requested < 0) | (requested > simulation_hours))):
            raise ValueError('输出时刻须为模拟范围内的有限一维数组，最多 10000 项')
        output_seconds = requested * 3600.0
    release_edges = [s for release in releases for s in (release['start_s'], release['end_s'])
                     if 0 < s < total_seconds]
    output_seconds = np.unique(np.r_[0., output_seconds, release_edges, total_seconds])
    initial_area_native = (hydro.area[0] if hydro.area is not None else
        np.full(len(native_chainage), config.width_avg_m * config.depth_avg_m))
    initial_area = on_wq_grid(initial_area_native)
    # 在构造积分时刻和结果数组前拒绝失控计算，不靠静默退回粗网格通过请求。
    areas = np.asarray(hydro.area) if hydro.area is not None else initial_area_native[None, :]
    discharges = np.asarray(hydro.discharge)
    steps_bound = check_work_budget(chainage, on_wq_grid(areas.min(axis=0)),
        on_wq_grid(areas.max(axis=0)), on_wq_grid(discharges.min(axis=0)),
        on_wq_grid(discharges.max(axis=0)), K_array, total_seconds,
        total_steps+len(output_seconds), len(output_seconds))
    # 在输出及排放边界处分步，避免提前排放、跨边界摊平，或把邻帧当作观测时刻。
    step_edges = np.unique(np.r_[np.arange(total_steps) * dt_s, output_seconds])
    output_set = set(output_seconds)
    components = np.stack([cbod, nh3, do_, nitrate])
    upstream, downstream = components[:, 0].copy(), components[:, -1].copy()
    volume, _, _, _ = geometry(chainage, initial_area, on_wq_grid(hydro.discharge[0]), K_array)
    initial_mass = (components * volume).sum(axis=1)
    injected = np.zeros(4)
    inflow = np.zeros(4)
    outflow = np.zeros(4)
    reaction_change = np.zeros(4)
    times, snapshots = [0.0], [components.copy()]
    max_stability = 0.0
    substep_count = 0
    min_actual_dt, max_actual_dt = float('inf'), 0.

    for t0, t1 in zip(step_edges[:-1], step_edges[1:]):
        step_dt = t1 - t0
        if len(t_hydro) > 1:
            position = np.interp((t0 + t1) / 2, t_hydro, np.arange(len(t_hydro)))
            lo = int(np.floor(position))
            hi = min(lo + 1, len(t_hydro) - 1)
            fraction = position - lo
            u_now = (1 - fraction) * hydro.velocity[lo] + fraction * hydro.velocity[hi]
            Q_now = (1 - fraction) * hydro.discharge[lo] + fraction * hydro.discharge[hi]
            A_now = ((1 - fraction) * hydro.area[lo] + fraction * hydro.area[hi]
                     if hydro.area is not None else initial_area_native)
        else:
            u_now, Q_now, A_now = hydro.velocity[0], hydro.discharge[0], initial_area_native

        if wq_refinement > 1:
            Q_now, A_now = on_wq_grid(Q_now), on_wq_grid(A_now)
            u_now = Q_now / A_now  # 插值流量和面积，再取流速，保持 Q=A*u。

        new_volume, q_face, conductance, rate = geometry(chainage, A_now, Q_now, K_array)
        components *= volume / new_volume  # 几何随时间变化时保持原有质量。
        volume = new_volume
        # 对流与扩散共同限制显式步长，包含反向流和边界半单元。
        n_sub = max(1, int(np.ceil(step_dt * rate / .9)))
        sub_dt = step_dt / n_sub
        substep_count += n_sub
        min_actual_dt, max_actual_dt = min(min_actual_dt, sub_dt), max(max_actual_dt, sub_dt)
        max_stability = max(max_stability, rate * sub_dt)
        H_now = np.maximum(A_now / config.width_avg_m, .1)

        for sub in range(n_sub):
            sub_t0 = t0 + sub * sub_dt
            for release in releases:
                overlap = max(0., min(sub_t0 + sub_dt, release['end_s']) - max(sub_t0, release['start_s']))
                added = release['rate_gs'] * overlap
                components[release['component'], release['source_section_id']] += added / volume[release['source_section_id']]
                injected[release['component']] += added
                release['injected_kg'] += added / 1000.
            components, step_in, step_out = conservative_step(
                components, volume, q_face, conductance, sub_dt, upstream, downstream)
            inflow += step_in
            outflow += step_out
            before_reaction = (components * volume).sum(axis=1)
            before_nh3 = components[1].copy()
            components[:3] = np.stack(_reaction_step(
                *components[:3], H_now, np.abs(u_now), K_d, K_s, beta1, SOD,
                O_sat, T_C, sub_dt, theta_d, theta_n, theta_rea,
                config.oxygen_per_nitrified_n, config.oxygen_half_sat_nitrification,
                config.oxygen_half_sat_cbod, config.oxygen_half_sat_sod, config.k_reaeration_base))
            components[3] += before_nh3 - components[1]
            reaction_change += (components * volume).sum(axis=1) - before_reaction

        if t1 in output_set:
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
        for i, name in enumerate(('cbod', 'ammonia', 'do', 'nitrate'))}

    return WQResult(
        t=t_out,
        chainage=chainage,
        cbod=cbod_out,
        ammonia=nh3_out,
        nitrate=output[:, 3],
        dissolved_oxygen=do_out,
        params={
            "source_mode": "single" if point_sources is None else "explicit_events",
            "point_sources": [{key: value for key, value in release.items()
                if key not in ('start_s', 'end_s', 'rate_gs', 'component')} for release in releases],
            "pollutant_type": pollutant_type if point_sources is None else "multiple",
            "load_kg": load_kg if point_sources is None else sum(r['load_kg'] for r in releases),
            "chainage_m": chainage_m if point_sources is None else None,
            "source_section_id": source_idx if point_sources is None else None,
            "source_effective_chainage_m": float(chainage[source_idx]) if point_sources is None else None,
            "source_snap_distance_m": float(abs(chainage[source_idx] - chainage_m)) if point_sources is None else None,
            "source_start_h": source_start_h if point_sources is None else None,
            "output_interval_s": output_interval_s if output_times_h is None else None,
            "output_sampling": "指定时刻" if output_times_h is not None else "等间隔及排放边界",
            "time_unit": "h",
            "hydro_time_mode": "stationary_single_frame" if len(t_hydro) == 1 else "time_interpolation",
            "mass_balance": balance,
            "wq_refinement": wq_refinement,
            "max_transport_stability": max_stability,
            "numerical_diagnostics": {
                "requested_dt_s": float(dt_s),
                "actual_min_dt_s": min_actual_dt,
                "actual_max_dt_s": max_actual_dt,
                "transport_substeps": substep_count,
                "grid_nodes": n_cs,
                "hydro_grid_nodes": len(native_chainage),
                "wq_refinement": wq_refinement,
                "transport_steps_upper_bound": steps_bound,
                "grid_method": "subdivide_hydro_intervals_linear_area_discharge",
                "geometry_information": "水质网格细分采用原水动力A/Q线性插值；未增加实测断面信息",
                "min_spacing_m": float(np.diff(chainage).min()),
                "max_spacing_m": float(np.diff(chainage).max()),
                "dispersion_min_m2s": float(K_array.min()),
                "dispersion_max_m2s": float(K_array.max()),
                "max_cell_peclet": float(np.max(u_rep * np.r_[np.diff(chainage), np.diff(chainage)[-1]] / K_array)),
                "scheme": "first_order_upwind_advection_explicit_centered_diffusion_split_rk4",
                "limits": "实际子步受稳定性、输出与排放边界影响；迎风格式存在数值弥散，守恒不代表峰值/到达时间精度。",
            },
            "reaction_model": {
                "version": "oxygen_limited_nitrification_v1",
                "k_cbod_decay_per_day": K_d,
                "k_nitrification_per_day": beta1,
                "sediment_oxygen_demand_g_m2_day": SOD,
                "water_temperature_C": T_C,
                "do_saturation_mgL": O_sat,
                "reaeration_base": config.k_reaeration_base,
                "oxygen_per_nitrified_n": config.oxygen_per_nitrified_n,
                "oxygen_half_sat_nitrification_mgL": config.oxygen_half_sat_nitrification,
                "oxygen_half_sat_cbod_mgL": config.oxygen_half_sat_cbod,
                "oxygen_half_sat_sod_mgL": config.oxygen_half_sat_sod,
                "parameter_status": "通用情景参数，未经当前河道现场率定",
                "simplifications": "硝化产物合并为NO3-N；不含生物合成、反硝化、藻类和碱度过程",
            },
            "reaction_diagnostics": {
                "nitrified_n_kg": -float(reaction_change[1] / 1000),
                "nitrate_n_produced_kg": float(reaction_change[3] / 1000),
                "nitrification_oxygen_demand_kg": -float(reaction_change[1] / 1000) * config.oxygen_per_nitrified_n,
                "final_minimum_do_mgL": float(do_out[-1].min()),
                "final_maximum_do_mgL": float(do_out[-1].max()),
            },
            "duration_min": duration_min if point_sources is None else None,
            "simulation_hours": simulation_hours,
        },
    )
