"""可复现的跨河段合成单源反演验收，不使用网络、密钥、HEC-RAS 或真实排污标签。"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from river_model.config import RiverConfig
from river_model.state import HydroResult
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality
from river_model.inverse import fit_single_source


def uniform_hydro(length, width, depth, speed, nodes):
    x = np.linspace(0, length, nodes)
    area = np.full((1, nodes), width*depth)
    return HydroResult(t=np.array([0.]), chainage=x, water_level=np.full_like(area, depth),
        discharge=area*speed, velocity=np.full_like(area, speed), area=area,
        params={'engine':'analytic_uniform_steady_fixture', 'tidal_amplitude_m':0.})


def run(output):
    rng = np.random.default_rng(20261018)
    reports = []
    cases = [(600., 8., 1., .25, .5), (1800., 40., 3., .35, 4.), (4500., 150., 5., .6, 12.)]
    for index, (length, width, depth, speed, load) in enumerate(cases):
        cfg = RiverConfig(river_name=f'合成河段{index+1}', length_m=length, width_avg_m=width,
            depth_avg_m=depth, ammonia_nh3_mgL=.2, bod_cbod_mgL=0., dissolved_oxygen_mgL=8.,
            k_nitrification=.15, k_sediment_oxygen_demand=0., depth_range_m=(depth, depth))
        travel_h = length/speed/3600
        start, duration = .03, 3.
        t = np.repeat(start + travel_h*np.linspace(.1, .8, 12), 4)
        x = np.tile(length*np.array([.55, .65, .75, .85]), 12)
        truth_hydro = uniform_hydro(length, width, depth, speed, 81)
        truth = solve_water_quality(cfg, truth_hydro, load_kg=load, chainage_m=.4*length,
            source_start_h=start, duration_min=duration, simulation_hours=float(max(t)),
            dt_s=15., output_times_h=t)
        exact = sample_water_quality(truth, t, x)
        background = solve_water_quality(cfg, truth_hydro, load_kg=0., chainage_m=.4*length,
            source_start_h=start, duration_min=duration, simulation_hours=float(max(t)),
            dt_s=15., output_times_h=t)
        baseline = sample_water_quality(background, t, x)
        signal = float(max(exact-baseline))
        sigma = max(.0005, signal*.025)
        noisy = np.maximum(0, exact + rng.normal(0, sigma, len(exact)))
        # 不同网格和积分步长产生真值与拟合；留出观测不参加拟合。
        fit_hydro = uniform_hydro(length, width, depth, speed, 41)
        train = np.arange(len(t)) % 3 != 0
        result = fit_single_source(cfg, fit_hydro, t[train], x[train], noisy[train], sigma,
            length*np.array([.2,.3,.4,.5,.6]), (0., 2*load), source_start_h=start,
            duration_min=duration, dt_s=45.)
        estimate = result['estimate']
        if estimate is None:
            raise AssertionError('有效合成信号未恢复出源候选')
        prediction = solve_water_quality(cfg, fit_hydro, load_kg=estimate['load_kg'],
            chainage_m=estimate['source_m'], source_start_h=start, duration_min=duration,
            simulation_hours=float(max(t)), dt_s=45., output_times_h=t)
        holdout = sample_water_quality(prediction, t[~train], x[~train])
        nrmse = float(np.sqrt(np.mean((holdout-exact[~train])**2))/signal)
        relative_load_error = abs(estimate['load_kg']-load)/load
        checks = {'source_recovered':abs(estimate['source_m']-.4*length) <= length/40,
            'load_relative_error_under_20_percent':relative_load_error < .2,
            'holdout_nrmse_under_15_percent':nrmse < .15,
            'finite_fit':bool(np.isfinite(estimate['weighted_sse']))}
        row = {'case':index+1, 'river':{'length_m':length,'width_m':width,'depth_m':depth,'velocity_ms':speed},
            'truth':{'source_m':.4*length,'load_kg':load}, 'sigma_mgL':sigma,
            'truth_nodes':81,'fit_nodes':41,'train_points':int(sum(train)), 'holdout_points':int(sum(~train)),
            'load_relative_error':relative_load_error,'holdout_nrmse':nrmse,'checks':checks,'fit':result}
        reports.append(row)
        print(json.dumps({'case': index+1, 'checks':checks,'load_error':relative_load_error,'nrmse':nrmse}), flush=True)
    negative = fit_single_source(cfg, fit_hydro, [0.,.001,.002,.003], [0.,100.,200.,300.],
        [.2]*4, .01, [100.,200.], source_start_h=.1)
    report = {'seed':20261018,'scope':'3 个理想恒定流合成河段；已知边界/反应/排放时段，未知离散源位置与负荷。跨网格含噪拟合与留出检验，非现场精度保证',
              'cases':reports,'no_information_guard':negative,'passed':all(all(r['checks'].values()) for r in reports) and negative['estimate'] is None}
    output.mkdir(parents=True, exist_ok=True)
    (output/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),encoding='utf-8')
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'work'/'steady-inverse-validation')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output_dir) else 1)
