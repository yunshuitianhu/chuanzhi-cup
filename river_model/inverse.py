"""受约束单源反演试验：固定排放时间/持续时长，搜索离散水质节点和有界质量。

仅对给定恒定流水动力及已率定参数有意义，不推断企业责任或贝叶斯后验。
"""
import numpy as np
from scipy.optimize import minimize_scalar

from .observations import sample_water_quality
from .wq_engine import solve_water_quality
from .wq_grid import refined_chainage


def _observations(times_h, chainages_m, concentrations_mgL, sigma_mgL, hydro):
    t, x, c = [np.asarray(v, dtype=float) for v in
               (times_h, chainages_m, concentrations_mgL)]
    try:
        sigma = np.broadcast_to(np.asarray(sigma_mgL, dtype=float), c.shape)
    except ValueError as exc:
        raise ValueError('误差标准差须为标量或与观测一一对应') from exc
    if (t.ndim != 1 or not 4 <= len(t) <= 2000 or t.shape != x.shape or t.shape != c.shape
            or not np.all(np.isfinite([t, x, c, sigma])) or np.any(c < 0)
            or np.any(sigma <= 0) or np.any(t < 0)
            or np.any(x < hydro.chainage[0]) or np.any(x > hydro.chainage[-1])):
        raise ValueError('需要4~2000个河段内有效配对观测，误差标准差须为正有限数')
    if len(np.unique(np.column_stack((t, x)), axis=0)) != len(t):
        raise ValueError('重复时空观测不能视为独立证据；请先合并并确认观测误差')
    return t, x, c, sigma


def fit_single_source(config, hydro, times_h, chainages_m, concentrations_mgL,
                      sigma_mgL, source_candidates_m, load_bounds_kg=(0., 20.),
                      source_start_h=0., duration_min=5., dt_s=30., *,
                      wq_refinement=4, holdout=None, response_gain_bounds=None,
                      calibration_reference=None, max_forward_evaluations=240):
    """拟合训练观测；只有全部工程门槛通过才返回 estimate。

    holdout 使用同名四个观测字段，浓度不参与优化。增益作用于相对已知背景的
    污染增量；增益界限与校准记录均由调用方提供，本函数不能核验其真实性。
    默认缺少这两项依据，禁止输出已接受的负荷估计。工程门槛不是置信区间。
    """
    if hydro.params.get('tidal_amplitude_m', 0):
        raise ValueError('反演试验仅支持恒定流')
    fields = [np.asarray(v, dtype=float) for v in (hydro.discharge, hydro.area, hydro.velocity)]
    if (any(v.ndim != 2 or v.shape != fields[0].shape or not np.all(np.isfinite(v))
            for v in fields) or fields[0].shape[1] != len(hydro.chainage)
            or fields[0].shape[0] < 1 or np.any(fields[1] <= 0)
            or not np.allclose(fields[2], fields[0]/fields[1], rtol=1e-4, atol=1e-7)):
        raise ValueError('水动力的有限流量、正面积及流速必须形状一致且满足u=Q/A')
    if any(not np.allclose(field, field[0], rtol=1e-4, atol=1e-7) for field in fields):
        raise ValueError('当前反演要求流量和面积不随时间变化')
    if type(wq_refinement) is not int or wq_refinement not in (1, 2, 4):
        raise ValueError('反演拟合细分须为1/2/4，以便用2/4/8倍网格复核')
    grid = refined_chainage(hydro.chainage, wq_refinement)
    times, locations, observed, sigma = _observations(
        times_h, chainages_m, concentrations_mgL, sigma_mgL, hydro)
    n = len(times)
    validation = None
    if holdout is not None:
        keys = ('times_h', 'chainages_m', 'concentrations_mgL', 'sigma_mgL')
        if not isinstance(holdout, dict) or set(holdout) != set(keys):
            raise ValueError('留出集须包含times_h/chainages_m/concentrations_mgL/sigma_mgL')
        validation = _observations(*(holdout[key] for key in keys), hydro)
        pairs = np.column_stack((np.r_[times, validation[0]], np.r_[locations, validation[1]]))
        if len(np.unique(pairs, axis=0)) != len(pairs):
            raise ValueError('训练集与留出集不能包含重复时空观测')
    low, high = np.asarray(load_bounds_kg, dtype=float)
    if not np.all(np.isfinite([low, high])) or not 0 <= low < high:
        raise ValueError('质量搜索界限须满足 0 <= lower < upper')
    candidates = np.asarray(source_candidates_m, dtype=float)
    if (candidates.ndim != 1 or not 1 <= len(candidates) <= 20 or not np.all(np.isfinite(candidates))
            or np.any(candidates < hydro.chainage[0]) or np.any(candidates > hydro.chainage[-1])):
        raise ValueError('需要 1~20 个河段内有限源候选位置')
    candidates = np.unique([grid[np.argmin(abs(grid - x))] for x in candidates])
    if (not np.all(np.isfinite([source_start_h, duration_min, dt_s])) or source_start_h < 0
            or duration_min <= 0 or dt_s <= 0):
        raise ValueError('排放时段与时间步长必须为有效正数，开始时刻不得为负')
    if type(max_forward_evaluations) is not int or not 2 <= max_forward_evaluations <= 1000:
        raise ValueError('正演次数预算须为2~1000的整数')
    gain_bounds = np.array([1., 1.]) if response_gain_bounds is None else np.asarray(response_gain_bounds, dtype=float)
    if (gain_bounds.shape != (2,) or not np.all(np.isfinite(gain_bounds))
            or not 0 < gain_bounds[0] <= 1 <= gain_bounds[1]):
        raise ValueError('增益界限须为包含1的正有限闭区间')
    if calibration_reference is not None and (not isinstance(calibration_reference, str)
                                               or not calibration_reference.strip()):
        raise ValueError('校准依据须为非空文字记录')
    calibration_verified = response_gain_bounds is not None and calibration_reference is not None
    all_times = times if validation is None else np.r_[times, validation[0]]
    all_x = locations if validation is None else np.r_[locations, validation[1]]
    all_sigma = sigma if validation is None else np.r_[sigma, validation[3]]
    total_h = float(all_times.max())
    cache, grid_cost = {}, {}
    calls, updates = 0, 0

    def forward(source, load, refinement):
        nonlocal calls, updates
        key = (float(source), float(load), refinement)
        if key in cache:
            return cache[key]
        if calls >= max_forward_evaluations:
            raise ValueError('单源反演正演次数超预算；请减少候选或缩短情景，不能返回成功估计')
        if updates + grid_cost.get(refinement, 0) > 50_000_000:
            raise ValueError('单源反演累计节点更新超预算；不能返回成功估计')
        result = solve_water_quality(config, hydro, load_kg=float(load), chainage_m=float(source),
            duration_min=duration_min, source_start_h=source_start_h, simulation_hours=total_h,
            dt_s=dt_s, output_times_h=all_times, wq_refinement=refinement)
        calls += 1
        numerical = result.params['numerical_diagnostics']
        grid_cost[refinement] = numerical['transport_steps_upper_bound'] * len(result.chainage)
        updates += grid_cost[refinement]
        if updates > 50_000_000:
            raise ValueError('单源反演累计节点更新超预算；不能返回成功估计')
        prediction = sample_water_quality(result, all_times, all_x)
        if not np.all(np.isfinite(prediction)):
            raise ValueError('正演观测含非有限值，停止反演')
        cache[key] = prediction
        return prediction

    # k_n=0时氨氮输移对注入质量严格线性；有反应时仍调用完整耦合求解器。
    linear = config.k_nitrification == 0

    def predict(source, load, refinement=wq_refinement):
        baseline = forward(candidates[0], 0., refinement)
        if linear:
            return baseline + load * (forward(source, 1., refinement) - baseline)
        return forward(source, load, refinement)

    def score(prediction):
        response = (prediction[:n] - baseline[:n]) / sigma
        target = (observed - baseline[:n]) / sigma
        denominator = float(response @ response)
        gain = float(np.clip((response @ target)/denominator, *gain_bounds)) if denominator else 1.
        value = float(np.sum((gain*response - target)**2))
        if not np.isfinite(value):
            raise ValueError('加权残差溢出；请核查误差尺度与浓度')
        return value, gain

    if total_h <= source_start_h:
        return {'status': 'unidentifiable', 'reason': '观测全部早于或等于排放开始时刻', 'estimate': None}
    baseline = forward(candidates[0], 0., wq_refinement)

    baseline = predict(candidates[0], 0.)
    baseline_cost, _ = score(baseline)
    ranked = []
    for source in candidates:
        def cost(load):
            return score(predict(source, load))[0]
        if linear:
            response = (predict(source, 1.)[:n] - baseline[:n])/sigma
            denominator = float(response @ response)
            amplitude = float(response @ ((observed-baseline[:n])/sigma))/denominator if denominator else 0.
            amplitude = float(np.clip(amplitude, low*gain_bounds[0], high*gain_bounds[1]))
            nominal_load = float(np.clip(amplitude, low, high))
        else:
            fit = minimize_scalar(cost, bounds=(low, high), method='bounded',
                                  options={'xatol': max(1e-6, (high-low)*1e-5), 'maxiter': 35})
            if not fit.success:
                raise RuntimeError('负荷优化未收敛，不能返回成功反演结果')
            nominal_load = float(fit.x)
        endpoints = [(cost(nominal_load), nominal_load), (cost(low), low), (cost(high), high)]
        chi2, load = min(endpoints)
        ranked.append({'source_m': float(source), 'load_kg': float(load), 'weighted_sse': chi2,
                       'response_gain': score(predict(source, load))[1]})
    ranked.sort(key=lambda row: row['weighted_sse'])
    best = ranked[0]
    plausible = [r for r in ranked if r['weighted_sse'] <= best['weighted_sse'] + 2.]
    signal = baseline_cost - best['weighted_sse']
    weighted_rmse = float(np.sqrt(best['weighted_sse']/len(times)))
    boundary = min(abs(best['load_kg']-low), abs(best['load_kg']-high)) < max(1e-8, (high-low)*1e-5)
    # 改善量很大仍可能是错误物理参数下的坏拟合。工程残差门槛不是概率/置信区间。
    status = ('unidentifiable' if signal < 9 else 'model_mismatch' if weighted_rmse > 2. else 'bound_limited' if boundary else
              'ambiguous' if len(plausible) > 1 else 'identified_in_scenario')
    load_probes = []
    if status == 'identified_in_scenario':
        # 检查数据是否能区分最优负荷与相差20%的负荷；不是负荷误差保证或置信区间。
        for factor in (.8, 1.2):
            probe = best['load_kg']*factor
            delta = (score(predict(best['source_m'], probe))[0]
                     - best['weighted_sse']) if low <= probe <= high else None
            load_probes.append({'load_kg': probe, 'sse_increase': delta})
        if any(p['sse_increase'] is None or p['sse_increase'] < 9. for p in load_probes):
            status = 'load_unresolved'
    conditional = dict(best) if status == 'identified_in_scenario' else None
    grid_check, holdout_check = None, None
    if conditional is not None:
        coarse = baseline + best['response_gain']*(predict(best['source_m'], best['load_kg'])-baseline)
        fine_baseline = forward(candidates[0], 0., wq_refinement*2)
        fine = fine_baseline + best['response_gain']*(
            predict(best['source_m'], best['load_kg'], wq_refinement*2)-fine_baseline)
        grid_error = float(np.sqrt(np.mean(((fine-coarse)/all_sigma)**2)))
        grid_check = {'fit_refinement': wq_refinement, 'check_refinement': wq_refinement*2,
                      'normalized_rms_difference': grid_error, 'limit': 1.}
        if validation is not None:
            held_error = float(np.sqrt(np.mean(((coarse[n:]-validation[2])/validation[3])**2)))
            holdout_check = {'count': len(validation[0]), 'weighted_rmse': held_error,
                             'limit': 2., 'used_for_optimization': False}
        status = ('numerical_unresolved' if grid_error > 1. else
                  'holdout_missing' if validation is None else
                  'holdout_mismatch' if held_error > 2. else
                  'calibration_unverified' if not calibration_verified else 'identified_in_scenario')
    reasons = {'unidentifiable': '相对无源情景的信号改善不足',
        'model_mismatch': '残差超过观测误差尺度的两倍，需检查参数、边界、单源假设或误差设定',
        'bound_limited': '最优负荷触及搜索边界', 'ambiguous': '存在多个近优源候选',
        'load_unresolved': '数据未能清楚区分相差20%的负荷，或搜索范围不足以检查',
        'numerical_unresolved': '加倍水质网格的预测变化超过给定误差尺度，分辨率不足',
        'holdout_missing': '缺少不参与拟合的独立留出观测',
        'holdout_mismatch': '留出残差超过给定误差尺度的两倍',
        'calibration_unverified': '缺少增量响应增益界限或校准依据，无法分离仪器响应与排放负荷',
        'identified_in_scenario': '通过给定候选和固定参数情景内的筛查，不代表现场来源已确认'}
    return {'version': 'single_source_checked_grid_v2', 'status': status, 'reason': reasons[status],
        'estimate': best if status == 'identified_in_scenario' else None,
        'conditional_fit': conditional,
        'candidate_fits': ranked, 'near_best_sources_m': [r['source_m'] for r in plausible],
        'baseline_weighted_sse': baseline_cost, 'signal_improvement': signal,
        'diagnostics': {'observation_count': len(times), 'weighted_rmse': weighted_rmse,
            'weighted_rmse_limit': 2., 'signal_improvement_min': 9., 'near_best_sse_delta': 2.,
            'load_relative_probe': .2, 'load_probe_sse_increase_min': 9., 'load_probes': load_probes,
            'grid_check': grid_check, 'holdout': holdout_check,
            'forward_evaluations': calls, 'node_updates_upper_bound': updates,
            'linear_ammonia_response': linear,
            'interpretation': '独立观测及给定误差尺度下的工程筛查；不输出统计置信度'},
        'source_grid_min_spacing_m': float(np.diff(grid).min()),
        'assumptions': {'source_start_h': source_start_h, 'duration_min': duration_min,
            'load_bounds_kg': [float(low), float(high)], 'wq_refinement': wq_refinement,
            'response_gain_bounds': gain_bounds.tolist() if response_gain_bounds is not None else None,
            'calibration_reference': calibration_reference,
            'calibration_status': 'provided_assumption' if calibration_verified else 'unverified',
            'response_model': '已知背景 + 增益 * 污染浓度增量',
            'fixed_parameters': '水动力、排放时段、反应参数和边界均视为已知'},
        'limitation': '单源恒定流条件试验；近优候选不是置信区间；校准记录由调用者提供且未核实。错误的增益假设可产生低残差和错误负荷，留出集不能消除此混淆，不对应现场责任认定'}
