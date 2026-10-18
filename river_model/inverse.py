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
    boundary = min(abs(best['load_kg']-low), abs(best['load_kg']-high)) < 1e-3
    status = ('unidentifiable' if signal < 9 else 'bound_limited' if boundary else
              'ambiguous' if len(plausible) > 1 else 'identified_in_scenario')
    return {'status': status, 'estimate': best if signal >= 9 else None,
        'candidate_fits': ranked, 'near_best_sources_m': [r['source_m'] for r in plausible],
        'baseline_weighted_sse': baseline_cost, 'signal_improvement': signal,
        'source_grid_min_spacing_m': float(np.diff(hydro.chainage).min()),
        'assumptions': {'source_start_h': source_start_h, 'duration_min': duration_min,
            'load_bounds_kg': [low, high], 'fixed_parameters': '水动力、排放时段、反应参数和边界均视为已知'},
        'limitation': '单源恒定流情景试验；近优候选不是置信区间；参数失配和多源情况需另验，不对应现场责任认定'}
