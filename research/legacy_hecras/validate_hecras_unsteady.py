"""HEC-RAS 非恒定流复现入口；默认仅生成工程，--run-real 才启动 COM。

这是理想梯形算例的输入输出检查，不代表现场率定或完整数值精度验收。
"""
import argparse
import json
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from river_model.config import YayaoConfig
from river_model.cross_sections import generate_yayao_cross_sections
from research.legacy_hecras.hecras_bridge import (
    _build_project_file, _build_geometry_file, _build_unsteady_flow_file,
    _build_plan_file, _build_f01_file, run_hecras_simulation,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-real', action='store_true', help='显式启动已安装的 HEC-RAS COM')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'work'/'hecras-validation')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # 仅影响本进程；所有工程保留在指定目录，不覆盖已有工程。
    tempfile.tempdir = str(output)
    cfg = YayaoConfig()
    sections = generate_yayao_cross_sections(n_total=5)
    report = {'mode':'real_engine' if args.run_real else 'input_generation_only',
              'scope':'25h 理想梯形潮汐算例；不是雅瑶水道实测模型，不改变 Web 默认引擎',
              'requested_output_interval_min':5}
    try:
        if args.run_real:
            h = run_hecras_simulation(cfg, sections, upstream_flow_m3s=6.9,
                downstream_stage_m=.5, tidal_amplitude_m=.8, tidal_period_h=12.42,
                duration_h=25., dt_s=30., output_interval_min=5., keep_work_dir=True,
                unsteady=True)
            np.savez(output/'hydro.npz', t=h.t, chainage=h.chainage,
                     water_level=h.water_level, discharge=h.discharge,
                     velocity=h.velocity, area=h.area)
            report['params'] = h.params
            report['checks'] = {
                'unsteady_engine': h.params['engine'].endswith('_unsteady'),
                'requested_time_covered': bool(h.t[-1] >= 25.),
                'area_positive': bool(np.all(h.area > 0)),
                'tidal_flow_reversal': bool(np.any(h.discharge < 0)),
                'output_interval_matches_request': bool(np.allclose(np.diff(h.t)*60,5.,atol=1e-5)),
            }
            storage = np.trapezoid(h.area, h.chainage, axis=1)
            boundary = np.trapezoid(h.discharge[:,0]-h.discharge[:,-1], h.t*3600)
            report['sampled_volume_diagnostic'] = {
                'storage_change_m3':float(storage[-1]-storage[0]),
                'boundary_net_m3':float(boundary),
                'residual_m3':float(storage[-1]-storage[0]-boundary),
                'note':'导出时间序列积分及提供断面推导面积；不能替代引擎内部水量误差报告',
            }
            report['ok'] = all(report['checks'].values())
        else:
            work = tempfile.mkdtemp(prefix='hecras-input-', dir=output)
            _build_project_file(work, include_flow_plan=True)
            _build_geometry_file(work,cfg,sections)
            _build_unsteady_flow_file(work,cfg,6.9,.5,.8,12.42,25.,30.)
            _build_plan_file(work,25.,30.,5.,unsteady=True)
            _build_f01_file(work,cfg,6.9,.5)
            report.update(ok=True, work_dir=work,
                          note='仅工程生成成功，未启动或验证 HEC-RAS 数值计算')
    except Exception:
        report.update(ok=False, error=traceback.format_exc())
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
