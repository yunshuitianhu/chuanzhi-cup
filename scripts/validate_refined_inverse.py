"""新水质网格的受控单源反演验收；解析卷积生成真值，不使用拟合求解器造真值。"""
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
from river_model.inverse import fit_single_source
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality


def uniform_hydro(length, width, depth, speed, nodes):
    x = np.linspace(0, length, nodes)
    area = np.full((1, nodes), width*depth)
    return HydroResult(t=np.array([0.]), chainage=x, water_level=np.full_like(area, depth),
        discharge=area*speed, velocity=np.full_like(area, speed), area=area,
        params={'engine': 'analytic_uniform_steady_fixture', 'tidal_amplitude_m': 0.})


def analytic_observations(t, x, *, width, depth, speed, source, mass, start, duration, order=64):
    """常系数无限域点浓度Green函数的排放时间积分，单位mg/L。

    与被测输移器/源项/采样器独立实现；本参照仅适用于关闭氨氮反应的受控算例。
    """
    shear = (9.81*depth*(.028*speed/depth**(2/3))**2)**.5
    diffusion = float(np.clip(.011*speed**2*width**2/(depth*shear), .5, 5000.))
    nodes, weights = np.polynomial.legendre.leggauss(order)
    answer = np.zeros(len(t))
    for i, (time, position) in enumerate(zip(t, x)):
        stop = min(start*3600+duration*60, time*3600)
        interval = stop-start*3600
        if interval <= 0:
            continue
        release = start*3600 + interval*(nodes+1)/2
        age = time*3600-release
        kernel = np.exp(-(position-source-speed*age)**2/(4*diffusion*age))/np.sqrt(4*np.pi*diffusion*age)
        answer[i] = mass*1000/(width*depth*duration*60)*interval/2 * (kernel @ weights)
    return .2+answer


def fixture(length=600., width=8., depth=1., speed=.25, load=.5):
    cfg = RiverConfig(length_m=length, width_avg_m=width, depth_avg_m=depth,
        depth_range_m=(depth, depth), ammonia_nh3_mgL=.2, bod_cbod_mgL=0.,
        k_nitrification=0., k_sediment_oxygen_demand=0., k_reaeration_base=0.,
        dissolved_oxygen_mgL=8., water_temp_C=20.)
    start, duration = .03, 3.
    t = np.repeat(start + length/speed/3600*np.linspace(.12, .45, 12), 4)
    x = np.tile(length*np.array([.40, .48, .56, .64]), 12)
    args = dict(width=width, depth=depth, speed=speed, source=length*.25, mass=load,
                start=start, duration=duration)
    exact = analytic_observations(t, x, **args)
    independent = analytic_observations(t, x, order=128, **args)
    peak = float(max(exact-.2))
    quadrature_error = float(max(abs(exact-independent))/peak)
    if quadrature_error > 1e-6:
        raise AssertionError('独立解析积分未收敛')
    return cfg, uniform_hydro(length, width, depth, speed, 100), t, x, exact, peak, quadrature_error


def experiment(name, physical, *, noise=.025, seed=20261020, gain=1., known_gain=True,
               gain_bounds=(.98, 1.02), speed_factor=1., background_bias=0.,
               candidates=None, upper=None, early=False, holdout_bias=0., refinement=4,
               expectation='recover'):
    length, width, depth, speed, mass = physical
    cfg, hydro, t, x, exact, peak, qerror = fixture(*physical)
    sigma = peak*max(noise, .025)
    observed = np.maximum(0., .2+gain*(exact-.2) + np.random.default_rng(seed).normal(0., peak*noise, len(t)))
    if early:
        t = np.repeat(np.linspace(0., .02, 12), 4)
        observed = np.full(len(t), .2)
    # 整个最下游测站留出，其浓度从不进入目标函数或候选选择。
    train = np.arange(len(t)) % 4 != 3
    observed[~train] += holdout_bias*peak
    fit_cfg = replace(cfg, ammonia_nh3_mgL=.2+background_bias*peak)
    fit_hydro = uniform_hydro(length, width, depth, speed*speed_factor, 100)
    result = fit_single_source(fit_cfg, fit_hydro, t[train], x[train], observed[train], sigma,
        length*np.array(candidates if candidates is not None else [.15, .20, .25, .30, .35]),
        (0., upper if upper is not None else 2*mass), source_start_h=.03, duration_min=3.,
        dt_s=15., wq_refinement=refinement,
        holdout=dict(times_h=t[~train], chainages_m=x[~train], concentrations_mgL=observed[~train], sigma_mgL=sigma),
        response_gain_bounds=gain_bounds if known_gain else None,
        calibration_reference='受控仿真增量响应假设；不是现场仪器校准记录' if known_gain else None)
    estimate = result['estimate']
    error = held_error = None
    recovered = False
    if estimate is not None:
        error = abs(estimate['load_kg']-mass)/mass
        prediction = solve_water_quality(fit_cfg, fit_hydro, load_kg=estimate['load_kg'],
            chainage_m=estimate['source_m'], source_start_h=.03, duration_min=3.,
            simulation_hours=float(t.max()), dt_s=15., output_times_h=t, wq_refinement=refinement)
        held = sample_water_quality(prediction, t[~train], x[~train])
        held = fit_cfg.ammonia_nh3_mgL + estimate['response_gain']*(held-fit_cfg.ammonia_nh3_mgL)
        held_error = float(np.sqrt(np.mean((held-exact[~train])**2))/peak)
        recovered = bool(abs(estimate['source_m']-.25*length) <= length/396/2
                         and error < .2 and held_error < .15)
    passed = (recovered if expectation == 'recover' else
              estimate is None or recovered if expectation == 'recover_or_decline' else
              estimate is None if expectation == 'decline' else
              estimate is not None and error > .2)
    row = dict(name=name, expectation=expectation, seed=seed, passed=bool(passed),
        river=dict(length_m=length, width_m=width, depth_m=depth, velocity_ms=speed),
        truth=dict(source_m=.25*length, load_kg=mass, response_gain=gain), noise_fraction=noise,
        fit_hydro_nodes=100, fit_wq_nodes=99*refinement+1, train_count=36, holdout_count=12,
        sigma_mgL=sigma, quadrature_relative_error=qerror, load_relative_error=error,
        holdout_truth_nrmse=held_error, fit=result)
    print(json.dumps(dict(name=name, status=result['status'], passed=bool(passed), load_error=error,
                         grid=result.get('diagnostics', {}).get('grid_check')), ensure_ascii=False), flush=True)
    return row


def coupled_solver_check():
    """辅助原生耦合路径检查；同内核加密真值，不冒充独立解析验证。"""
    cfg, _, t, x, _, _, _ = fixture()
    cfg = replace(cfg, k_nitrification=.15, bod_cbod_mgL=2.)
    hydro = uniform_hydro(600., 8., 1., .25, 25)
    def prediction(load):
        wq = solve_water_quality(cfg, hydro, load_kg=load, chainage_m=150., duration_min=3.,
            source_start_h=.03, simulation_hours=float(t.max()), dt_s=15.,
            output_times_h=t, wq_refinement=8)
        return sample_water_quality(wq, t, x)
    exact, baseline = prediction(.5), prediction(0.)
    sigma = float(max(exact-baseline)*.1)
    train = np.arange(len(t)) % 4 != 3
    fit = fit_single_source(cfg, hydro, t[train], x[train], exact[train], sigma, [150.],
        (0., 1.), source_start_h=.03, duration_min=3., dt_s=15., wq_refinement=4,
        holdout=dict(times_h=t[~train], chainages_m=x[~train], concentrations_mgL=exact[~train], sigma_mgL=sigma),
        response_gain_bounds=(1., 1.), calibration_reference='受控仿真假设，非现场校准')
    estimate = fit['estimate']
    passed = bool(estimate is not None and abs(estimate['load_kg']-.5)/.5 < .2
                  and not fit['diagnostics']['linear_ammonia_response'])
    print(json.dumps(dict(name='native_coupled_path', status=fit['status'], passed=passed)), flush=True)
    return dict(scope='同内核8倍网格生成、4倍拟合；仅验证非线性耦合调用路径', passed=passed, fit=fit)


def publish_report(report):
    """显式发布实际通过的离线记录；不会运行当前用户数据或改变任何会话。"""
    if (not report.get('passed') or len(report.get('cases', [])) != 25
            or not (report.get('coupled_solver_check') or {}).get('passed')
            or not all(row['passed'] for row in report['cases'])):
        raise ValueError('仅可发布完整通过的25项主检查及原生耦合辅助检查')
    cases = report['cases']
    cross = [row for row in cases if row['name'].startswith('cross_river_')]
    summary = dict(version=report['version'], date='2026-10-20', seed=report['seed'],
        scope=report['scope'], noiseCases=sum(row['name'].startswith('noise_') for row in cases),
        declineCases=sum(row['expectation'] == 'decline' for row in cases),
        knownBlindSpot=report['known_blind_spot'],
        crossRiver=[dict(length_m=row['river']['length_m'], load_relative_error=row['load_relative_error'],
            holdout_truth_nrmse=row['holdout_truth_nrmse'],
            source_recovered=abs(row['fit']['estimate']['source_m']-row['truth']['source_m']) <= row['river']['length_m']/792)
            for row in cross])
    for path, value in ((ROOT/'docs'/'validation'/'refined_inverse_v2.json', report),
                        (ROOT/'frontend'/'src'/'data'/'refinedInverseValidation.json', summary)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def run(output, group='all', publish=False):
    physical = [(600., 8., 1., .25, .5), (1800., 20., 2., .25, 2.), (5400., 40., 3., .35, 6.)]
    rows = []
    if group in ('all', 'cross_river'):
        rows.extend(experiment(f'cross_river_{i+1}', case) for i, case in enumerate(physical))
    if group in ('all', 'robustness'):
        for noise in (.025, .10, .25):
            for repeat in range(4):
                rows.append(experiment(f'noise_{noise}_{repeat}', physical[0], noise=noise,
                    seed=20261020+repeat, expectation='recover' if noise == .025 else 'recover_or_decline'))
        negatives = [
            ('wrong_velocity', dict(speed_factor=1.3)),
            ('wrong_background', dict(background_bias=.5)),
            ('missing_source', dict(candidates=[.15, .20, .30, .35])),
            ('load_boundary', dict(upper=.25)),
            ('before_emission', dict(early=True)),
            ('holdout_mismatch', dict(holdout_bias=1.)),
            ('coarse_grid', dict(refinement=1)),
            ('unknown_gain', dict(gain=1.5, known_gain=False)),
            ('wide_gain_bounds', dict(gain=1.5, gain_bounds=(.5, 1.5))),
        ]
        rows.extend(experiment(name, physical[0], expectation='decline', **kwargs) for name, kwargs in negatives)
        # 必须保留：假校准记录无法由同一浓度数据检出，不计作正确恢复。
        rows.append(experiment('false_calibration_known_blind_spot', physical[0], gain=1.5,
                               gain_bounds=(1., 1.), expectation='known_confounding'))
    coupled = coupled_solver_check() if group == 'all' else None
    report = dict(version='single_source_checked_grid_v2', scope='离线受控单源恒定流；主验收为解析真值；非现场准确率',
        seed=20261020, gates=dict(load_relative_error_max=.2, holdout_truth_nrmse_max=.15,
            weighted_rmse_max=2., doubled_grid_normalized_rms_max=1., interpretation='工程门槛，不是置信区间'),
        known_blind_spot='虚假的增益校准假设仍可通过拟合与留出检验并给出错误负荷；须外部核实校准、水动力与边界',
        cases=rows, coupled_solver_check=coupled,
        passed=all(row['passed'] for row in rows) and (coupled is None or coupled['passed']))
    output.mkdir(parents=True, exist_ok=True)
    (output/'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    if publish:
        publish_report(report)
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT/'work'/'refined-inverse-validation')
    parser.add_argument('--group', choices=('all', 'cross_river', 'robustness'), default='all')
    parser.add_argument('--publish', action='store_true', help='全部通过后更新技术展示离线结果及完整验收记录')
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output_dir, args.group, args.publish) else 1)
