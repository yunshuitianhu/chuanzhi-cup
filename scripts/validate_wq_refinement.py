"""固定100个水动力断面，对独立Green函数检查水质细分；不读取现场标签。"""
import argparse
import json
from pathlib import Path
import platform
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from river_model.config import RiverConfig
from river_model.wq_engine import solve_water_quality
from scripts.validate_steady_forward import uniform_hydro, source_reference, dual_widths, l1_relative

LIMITS = {'refined_l1': .08, 'finest_l1': .05, 'error_ratio': .6, 'mass_error_kg': 1e-9}


def benchmark(length=6000., width=30., depth=2., speed=.2, time_s=600., start_s=37., duration_s=60.):
    cfg = RiverConfig(width_avg_m=width, depth_avg_m=depth, depth_range_m=(depth, depth),
        mannings_n=.028, ammonia_nh3_mgL=0., bod_cbod_mgL=0., k_nitrification=0.,
        k_sediment_oxygen_demand=0., k_reaeration_base=0., dissolved_oxygen_mgL=8.)
    x = np.linspace(0., length, 100)
    hydro = uniform_hydro(x, width, depth, speed)
    before = [value.copy() for value in (hydro.chainage, hydro.area, hydro.discharge, hydro.velocity)]
    area = width*depth
    shear = (9.81*depth*(.028*speed/depth**(2/3))**2)**.5
    dispersion = float(np.clip(.011*speed**2*width**2/(depth*shear), .5, 5000.))
    rows, profiles = [], []
    for refinement in (1, 2, 4, 8):
        tick = perf_counter()
        result = solve_water_quality(cfg, hydro, load_kg=1., chainage_m=length/3,
            duration_min=duration_s/60, source_start_h=start_s/3600, simulation_hours=time_s/3600,
            dt_s=60., output_times_h=np.array([time_s/3600]), wq_refinement=refinement)
        elapsed = perf_counter()-tick
        exact = source_reference(result.chainage, area, speed, dispersion, time_s, length/3,
            1., start_s, start_s+duration_s)
        _, widths = dual_widths(result.chainage)
        mass = float(np.dot(result.ammonia[-1], widths*area)/1000)
        rows.append({'refinement': refinement, 'relative_l1': l1_relative(result.ammonia[-1], exact, widths),
            'peak_relative_error': float(abs(result.ammonia[-1].max()-exact.max())/exact.max()),
            'independent_final_mass_kg': mass, 'minimum_concentration': float(result.ammonia.min()),
            'source_snap_m': result.params['source_snap_distance_m'], 'wall_seconds': elapsed,
            'numerics': result.params['numerical_diagnostics']})
        profiles.append((result.chainage, result.ammonia[-1], exact, refinement))
    errors = [r['relative_l1'] for r in rows]
    unchanged = all(np.array_equal(old, current) for old, current in
        zip(before, (hydro.chainage, hydro.area, hydro.discharge, hydro.velocity)))
    report = {'river': {'length_m': length, 'width_m': width, 'depth_m': depth, 'speed_ms': speed},
        'time_s': time_s, 'dispersion_m2s': dispersion, 'hydro_nodes': len(x), 'rows': rows,
        'reference': '有限时段点源Green函数卷积，控制体积积分；零反应、远场边界、源位精确对齐',
        'checks': {'decreasing_error': all(a > b for a, b in zip(errors[:-1], errors[1:])),
            'refined_under_limit': errors[2] < LIMITS['refined_l1'],
            'finest_under_limit': errors[3] < LIMITS['finest_l1'],
            'refined_improves_distribution': errors[2] < LIMITS['error_ratio']*errors[0],
            'refined_improves_peak': rows[2]['peak_relative_error'] < rows[0]['peak_relative_error'],
            'mass_and_positivity': all(abs(r['independent_final_mass_kg']-1.) < LIMITS['mass_error_kg']
                and r['minimum_concentration'] >= 0 for r in rows), 'hydro_unchanged': unchanged}}
    return report, profiles


def run(output, plots=True):
    output.mkdir(parents=True, exist_ok=True)
    cases = [benchmark(3000., 15., 1., .15, 300., 11., 30.), benchmark(),
             benchmark(12000., 60., 2.5, .3, 1200., 73., 120.)]
    report = {'scope': '一维恒定流、均匀水力、已知单源受控算例；不代表现场准确率或反演验收',
        'python': platform.python_version(), 'numpy': np.__version__, 'limits': LIMITS,
        'cases': [case[0] for case in cases]}
    report['passed'] = all(all(case['checks'].values()) for case in report['cases'])
    (output/'refinement_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    if plots:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))
        for ax, (case, profiles) in zip(axes, cases):
            length = case['river']['length_m']
            for x, actual, exact, refinement in profiles:
                if refinement in (1, 4, 8):
                    ax.plot(x-length/3, actual, label=f"WQ {len(x)} nodes (x{refinement})")
                if refinement == 8:
                    ax.plot(x-length/3, exact, 'k--', label='Independent analytical reference')
            ax.set_xlim(-length/30, length/8)
            ax.set_title(f"{length/1000:g} km river / 100 hydro sections")
            ax.set_xlabel('Distance from source (m)'); ax.set_ylabel('NH3-N (mg/L)')
            ax.grid(alpha=.25); ax.legend(fontsize=8)
        fig.tight_layout(rect=(0, .08, 1, 1))
        fig.text(.5, .03, 'Steady uniform test cases only. Source aligned; no reactions. Refinement adds no measured geometry.', ha='center', fontsize=9)
        fig.savefig(output/'refined_profiles.png', dpi=150); plt.close(fig)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'work'/'wq-refinement-validation')
    parser.add_argument('--no-plots', action='store_true')
    args = parser.parse_args()
    report = run(args.output, not args.no_plots)
    print(json.dumps({'passed': report['passed'], 'output': str(args.output),
        'relative_l1': [[r['relative_l1'] for r in c['rows']] for c in report['cases']]}, ensure_ascii=False))
    raise SystemExit(0 if report['passed'] else 1)
