"""使用已有仿真坐标/时间真值验证配准，不读取源真值参与智能体推理。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from river_model.cruise_alignment import audit_track, project_points, utc_times


def run(directory, output):
    rows = []
    for metadata_path in sorted((directory/'真值与配置').glob('S??_场景参数与源真值.json')):
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        case_id = metadata['case_id']
        truth = pd.read_csv(directory/'真值与配置'/f'{case_id}_逐点真值.csv')
        geographic = json.loads((directory/'真值与配置'/f'{case_id}_河道与假定污染源.geojson').read_text(encoding='utf-8'))
        line = next(feature['geometry']['coordinates'] for feature in geographic['features']
                    if feature['geometry']['type']=='LineString')
        path = [[lat,lon] for lon,lat in line]
        actual_x, _, ambiguous = project_points(truth[['latitude_wgs84','longitude_wgs84']].to_numpy(),path,5.)
        error = abs(actual_x-truth.chainage_m.to_numpy())
        times = utc_times(truth['时间'],'Asia/Shanghai')
        origin = pd.Timestamp(metadata['simulation_start']).tz_localize('Asia/Shanghai').tz_convert('UTC')
        actual_t = np.asarray((times-origin).total_seconds())/3600
        time_error = abs(actual_t-truth.model_time_h.to_numpy())*3600
        audit_df = pd.DataFrame(dict(latitude=truth.latitude_wgs84,longitude=truth.longitude_wgs84,datetime=truth['时间']))
        row = dict(case=case_id,records=len(truth),p95_chainage_error_m=float(np.quantile(error,.95)),
            max_chainage_error_m=float(error.max()),max_time_error_s=float(time_error.max()),
            ambiguous_records=int(ambiguous.sum()),track_audit=audit_track(audit_df),
            passed=bool(np.quantile(error,.95)<2. and time_error.max()<1e-7))
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    report = dict(scope='已有V2仿真位置噪声约0.7m、已知参考线与时间原点；非现场定位精度，未检验真实流向或传感器时钟',
        gates=dict(p95_chainage_error_m=2.,max_time_error_s=1e-7),cases=rows,
        passed=len(rows)==6 and all(row['passed'] for row in rows))
    output.mkdir(parents=True,exist_ok=True)
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args = parser.parse_args()
    raise SystemExit(0 if run(args.synthetic_dir,args.output_dir) else 1)
