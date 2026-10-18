"""恒定流反演压力检查：含噪、参数/背景失配、缺测与不可检测的仪器增益混淆。

预先固定噪声种子和门槛。实验保留独立观测，不调用外部服务或 LLM。
通过代表实现符合这些情景约束，不代表在任意河流均能识别真实来源。
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from river_model.config import RiverConfig
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality
from river_model.inverse import fit_single_source
from validate_steady_inverse import uniform_hydro


def run(output):
    length, speed, load, start, duration = 600., .25, .5, .03, 3.
    cfg = RiverConfig(length_m=length, width_avg_m=8., depth_avg_m=1.,
        ammonia_nh3_mgL=.2, bod_cbod_mgL=0., dissolved_oxygen_mgL=8.,
        k_nitrification=.15, k_sediment_oxygen_demand=0., depth_range_m=(1.,1.))
    t = np.repeat(start+length/speed/3600*np.linspace(.1,.8,12),4)
    x = np.tile(length*np.array([.55,.65,.75,.85]),12)
    train = np.arange(len(t)) % 3 != 0
    truth_hydro = uniform_hydro(length,8.,1.,speed,81)
    def prediction(config, hydro, source, mass):
        result = solve_water_quality(config,hydro,load_kg=mass,chainage_m=source,
            source_start_h=start,duration_min=duration,simulation_hours=float(t.max()),
            dt_s=15. if len(hydro.chainage)==81 else 45.,output_times_h=t)
        return sample_water_quality(result,t,x)
    exact = prediction(cfg,truth_hydro,.4*length,load)
    baseline = prediction(cfg,truth_hydro,.4*length,0.)
    peak = float(max(exact-baseline))
    scenarios = [
        {'name':'noise_2.5_percent','noise':.025,'expect':'recover'},
        {'name':'noise_10_percent','noise':.10,'expect':'recover_or_decline'},
        {'name':'noise_25_percent','noise':.25,'expect':'recover_or_decline'},
        {'name':'velocity_plus_30_percent','noise':.025,'speed_factor':1.3,'expect':'decline'},
        {'name':'background_bias','noise':.025,'background_bias':peak*.5,'expect':'decline'},
        {'name':'truth_source_missing','noise':.025,'candidates':[.2,.3,.5,.6],'expect':'decline'},
        {'name':'load_bound_too_low','noise':.025,'upper':load*.5,'expect':'decline'},
        {'name':'signal_not_yet_observed','noise':0.,'early':True,'expect':'decline'},
        # 负例必须保留：仪器对污染增量的未知乘性偏差可伪装成更大的排放负荷。
        {'name':'hidden_sensor_gain_1.5','noise':.025,'gain':1.5,'expect':'known_confounding'},
    ]
    for index, case in enumerate(scenarios):
        case['seed']=20261018+index
    # 每个噪声水平8个独立固定种子，避免只展示一次有利的抽样。
    scenarios=[dict(case,seed=case['seed']+100*repeat,repeat=repeat)
               for case in scenarios[:3] for repeat in range(8)]+scenarios[3:]
    reports=[]
    for case in scenarios:
        sigma=max(.0005,peak*max(case['noise'],.025))
        observed=baseline+(exact-baseline)*case.get('gain',1.)
        observed=np.maximum(0,observed+np.random.default_rng(case['seed']).normal(0,
            peak*case['noise'],len(t)))
        fit_cfg=replace(cfg,ammonia_nh3_mgL=.2+case.get('background_bias',0.))
        hydro=uniform_hydro(length,8.,1.,speed*case.get('speed_factor',1.),41)
        fit_t=t.copy()
        if case.get('early'):
            fit_t=np.repeat(np.linspace(0,start*.8,12),4)
            observed=np.full_like(t,.2)
        result=fit_single_source(fit_cfg,hydro,fit_t[train],x[train],observed[train],sigma,
            length*np.array(case.get('candidates',[.2,.3,.4,.5,.6])),
            (0.,case.get('upper',2*load)),source_start_h=start,duration_min=duration,dt_s=45.)
        estimate=result['estimate']
        error=nrmse=None
        if estimate:
            error=abs(estimate['load_kg']-load)/load
            held=prediction(fit_cfg,hydro,estimate['source_m'],estimate['load_kg'])[~train]
            nrmse=float(np.sqrt(np.mean((held-exact[~train])**2))/peak)
        recovered=bool(estimate and abs(estimate['source_m']-.4*length)<=length/40
                       and error<.2 and nrmse<.15)
        expected=case['expect']
        if expected=='recover':
            passed=recovered
        elif expected=='recover_or_decline':
            passed=estimate is None or recovered
        elif expected=='decline':
            passed=estimate is None
        else:
            passed=bool(estimate and error>.2)
        row={'scenario':case,'passed':bool(passed),'sigma_mgL':sigma,
             'load_relative_error':error,'holdout_truth_nrmse':nrmse,'fit':result}
        reports.append(row)
        print(json.dumps({'case':case['name'],'status':result['status'],'passed':bool(passed),
                          'load_error':error,'holdout_nrmse':nrmse}),flush=True)
    report={'seed':20261018,'scope':'单源恒定流合成压力实验；真值81断面/15s，拟合41断面/45s；32训练点、16留出点',
        'gates':{'identified_case_load_error_max':.2,'identified_case_holdout_truth_nrmse_max':.15,
                 'weighted_rmse_max':2.,'load_probe_relative_change':.2,'load_probe_sse_increase_min':9.,
                 'interpretation':'工程筛查门槛，非统计置信区间'},
        'known_blind_spot':'未知仪器增益与负荷可混淆；低残差不保证真实负荷正确，须独立校准传感器和水动力参数',
        'cases':reports,'passed':all(r['passed'] for r in reports)}
    output.mkdir(parents=True,exist_ok=True)
    (output/'robustness.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,
                                                   allow_nan=False),encoding='utf-8')
    return report['passed']


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=ROOT/'work'/'inverse-robustness')
    args=parser.parse_args()
    raise SystemExit(0 if run(args.output_dir) else 1)
