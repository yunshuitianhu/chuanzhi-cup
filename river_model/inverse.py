"""受约束单源反演试验：固定排放时间/持续时长，搜索离散断面和有界质量。

仅对给定恒定流水动力及已率定参数有意义，不推断企业责任或贝叶斯后验。
"""
import numpy as np
from scipy.optimize import minimize_scalar

from .observations import sample_water_quality
from .wq_engine import solve_water_quality


def fit_single_source(config, hydro, times_h, chainages_m, concentrations_mgL,
                      sigma_mgL, source_candidates_m, load_bounds_kg=(0., 20.),
                      source_start_h=0., duration_min=5., dt_s=30.):
    if hydro.params.get('tidal_amplitude_m', 0):
        raise ValueError('反演试验仅支持恒定流')
    if any(not np.allclose(field, field[0], rtol=1e-4, atol=1e-7)
           for field in (hydro.discharge, hydro.area)):
        raise ValueError('当前反演要求流量和面积不随时间变化')
    times, locations, observed = [np.asarray(v, dtype=float) for v in
                                (times_h, chainages_m, concentrations_mgL)]
    sigma = np.broadcast_to(np.asarray(sigma_mgL, dtype=float), observed.shape)
    if (times.ndim != 1 or len(times) < 4 or times.shape != locations.shape or times.shape != observed.shape
            or not np.all(np.isfinite([times, locations, observed, sigma]))
            or np.any(observed < 0) or np.any(sigma <= 0) or np.any(times < 0)):
        raise ValueError('至少需要四个有效配对观测，误差标准差须为正有限数')
    if len(np.unique(np.column_stack((times, locations)), axis=0)) != len(times):
        raise ValueError('重复时空观测不能视为独立证据；请先合并并确认观测误差')
    low, high = load_bounds_kg
    if not np.all(np.isfinite([low, high])) or not 0 <= low < high:
        raise ValueError('质量搜索界限须满足 0 <= lower < upper')
    candidates = np.asarray(source_candidates_m, dtype=float)
    if (candidates.ndim != 1 or not 1 <= len(candidates) <= 20 or not np.all(np.isfinite(candidates))
            or np.any(candidates < hydro.chainage[0]) or np.any(candidates > hydro.chainage[-1])):
        raise ValueError('需要 1~20 个河段内有限源候选位置')
    # 不把连续候选位置映射到同一断面的结果当成不同的空间精度。
    candidates = np.unique([hydro.chainage[np.argmin(abs(hydro.chainage - x))] for x in candidates])
    total_h = float(times.max())
    if total_h <= source_start_h:
        return {'status': 'unidentifiable', 'reason': '观测全部早于或等于排放开始时刻', 'estimate': None}

    def predict(source, load):
        result = solve_water_quality(config, hydro, load_kg=float(load), chainage_m=float(source),
            duration_min=duration_min, source_start_h=source_start_h, simulation_hours=total_h,
            dt_s=dt_s, output_times_h=times)
        return sample_water_quality(result, times, locations)

    baseline = predict(candidates[0], 0.)
    baseline_cost = float(np.sum(((baseline-observed)/sigma)**2))
    ranked = []
    for source in candidates:
        def cost(load):
            return float(np.sum(((predict(source, load)-observed)/sigma)**2))
        fit = minimize_scalar(cost, bounds=(low, high), method='bounded',
                              options={'xatol': 1e-4, 'maxiter': 30})
        if not fit.success:
            raise RuntimeError('负荷优化未收敛，不能返回成功反演结果')
        endpoints = [(float(fit.fun), float(fit.x)), (cost(low), low), (cost(high), high)]
        chi2, load = min(endpoints)
        ranked.append({'source_m': float(source), 'load_kg': float(load), 'weighted_sse': chi2})
    ranked.sort(key=lambda row: row['weighted_sse'])
    best = ranked[0]
    plausible = [r for r in ranked if r['weighted_sse'] <= best['weighted_sse'] + 2.]
    signal = baseline_cost - best['weighted_sse']
    weighted_rmse = float(np.sqrt(best['weighted_sse']/len(times)))
    boundary = min(abs(best['load_kg']-low), abs(best['load_kg']-high)) < 1e-3
    # 改善量很大仍可能是错误物理参数下的坏拟合。工程残差门槛不是概率/置信区间。
    status = ('unidentifiable' if signal < 9 else 'model_mismatch' if weighted_rmse > 2. else 'bound_limited' if boundary else
              'ambiguous' if len(plausible) > 1 else 'identified_in_scenario')
    load_probes = []
    if status == 'identified_in_scenario':
        # 检查数据是否能区分最优负荷与相差20%的负荷；不是负荷误差保证或置信区间。
        for factor in (.8, 1.2):
            probe = best['load_kg']*factor
            delta = (float(np.sum(((predict(best['source_m'],probe)-observed)/sigma)**2))
                     - best['weighted_sse']) if low <= probe <= high else None
            load_probes.append({'load_kg': probe, 'sse_increase': delta})
        if any(p['sse_increase'] is None or p['sse_increase'] < 9. for p in load_probes):
            status = 'load_unresolved'
    reasons = {'unidentifiable': '相对无源情景的信号改善不足',
        'model_mismatch': '残差超过观测误差尺度的两倍，需检查参数、边界、单源假设或误差设定',
        'bound_limited': '最优负荷触及搜索边界', 'ambiguous': '存在多个近优源候选',
        'load_unresolved': '数据未能清楚区分相差20%的负荷，或搜索范围不足以检查',
        'identified_in_scenario': '通过给定候选和固定参数情景内的筛查，不代表现场来源已确认'}
    return {'status': status, 'reason': reasons[status],
        'estimate': best if status == 'identified_in_scenario' else None,
        'candidate_fits': ranked, 'near_best_sources_m': [r['source_m'] for r in plausible],
        'baseline_weighted_sse': baseline_cost, 'signal_improvement': signal,
        'diagnostics': {'observation_count': len(times), 'weighted_rmse': weighted_rmse,
            'weighted_rmse_limit': 2., 'signal_improvement_min': 9., 'near_best_sse_delta': 2.,
            'load_relative_probe': .2, 'load_probe_sse_increase_min': 9., 'load_probes': load_probes,
            'interpretation': '独立观测及给定误差尺度下的工程筛查；不输出统计置信度'},
        'source_grid_min_spacing_m': float(np.diff(hydro.chainage).min()),
        'assumptions': {'source_start_h': source_start_h, 'duration_min': duration_min,
            'load_bounds_kg': [low, high], 'fixed_parameters': '水动力、排放时段、反应参数和边界均视为已知'},
        'limitation': '单源恒定流情景试验；近优候选不是置信区间；小残差不能排除流量与负荷等参数混淆，不对应现场责任认定'}
