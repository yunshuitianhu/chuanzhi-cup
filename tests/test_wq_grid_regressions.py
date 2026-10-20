"""水质网格独立细分：物理积分、非均匀几何、资源上界、Web和项目回环。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from river_model.config import RiverConfig
from river_model.state import HydroResult
from river_model.wq_grid import refined_chainage
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality


def fixture(speed=.2):
    x = np.array([0., 30., 100., 180., 240.])
    area = 40.+x*.1
    flow = np.full_like(area, 50.*speed)
    hydro = HydroResult(t=np.array([0.]), chainage=x, area=area[None, :],
        discharge=flow[None, :], velocity=(flow/area)[None, :],
        water_level=(area/20)[None, :], params={'run_id': 'test_hydro', 'tidal_amplitude_m': 0.})
    cfg = RiverConfig(length_m=240., width_avg_m=20., depth_avg_m=2., depth_range_m=(1., 4.),
        ammonia_nh3_mgL=0., bod_cbod_mgL=0., k_nitrification=0.,
        k_sediment_oxygen_demand=0., k_reaeration_base=0., dissolved_oxygen_mgL=8.)
    return cfg, hydro


class WaterQualityGridTests(unittest.TestCase):
    def test_subdivision_keeps_all_native_nodes_and_integrated_geometry(self):
        _, hydro = fixture()
        for factor in (1, 2, 4, 8):
            refined = refined_chainage(hydro.chainage, factor)
            np.testing.assert_array_equal(refined[::factor], hydro.chainage)
            self.assertEqual(len(refined), (len(hydro.chainage)-1)*factor+1)
            area = np.interp(refined, hydro.chainage, hydro.area[0])
            self.assertAlmostEqual(float(np.trapezoid(area, refined)),
                float(np.trapezoid(hydro.area[0], hydro.chainage)), places=10)

    def test_mass_and_both_flow_directions_on_nonuniform_refined_grid(self):
        for speed in (.2, -.2):
            cfg, hydro = fixture(speed)
            before = {k: getattr(hydro, k).copy() for k in
                ('t', 'chainage', 'area', 'velocity', 'discharge', 'water_level')}
            result = solve_water_quality(cfg, hydro, load_kg=1., chainage_m=103.4,
                source_start_h=7/3600, duration_min=.5, simulation_hours=.05,
                dt_s=60., wq_refinement=4)
            area = np.interp(result.chainage, hydro.chainage, hydro.area[0])
            mass = float(np.trapezoid(result.ammonia[-1]*area, result.chainage)/1000)
            ledger = result.params['mass_balance']['ammonia']
            self.assertAlmostEqual(mass+ledger['outflow_kg']-ledger['inflow_kg'], 1., places=10)
            self.assertGreaterEqual(float(result.ammonia.min()), 0.)
            info = result.params['numerical_diagnostics']
            self.assertEqual(info['hydro_grid_nodes'], 5)
            self.assertEqual(info['grid_nodes'], 17)
            self.assertLessEqual(info['transport_substeps'], info['transport_steps_upper_bound'])
            center = float(np.trapezoid(result.ammonia[-1]*area*result.chainage, result.chainage)/(mass*1000))
            self.assertGreater((center-result.params['source_effective_chainage_m'])*speed, 0.)
            for key, old in before.items():
                np.testing.assert_array_equal(old, getattr(hydro, key))

    def test_source_quantization_improves_and_partial_injection_is_unchanged(self):
        cfg, hydro = fixture(0.)
        offsets = []
        for factor in (1, 4, 8):
            result = solve_water_quality(cfg, hydro, load_kg=2., chainage_m=45.2,
                source_start_h=7/3600, duration_min=1., simulation_hours=37/3600,
                dt_s=60., wq_refinement=factor)
            offsets.append(result.params['source_snap_distance_m'])
            self.assertAlmostEqual(result.params['mass_balance']['ammonia']['injected_kg'], 1., places=11)
            self.assertIn(7/3600, result.t)
        self.assertLess(offsets[1], offsets[0]); self.assertLessEqual(offsets[2], offsets[1])

    def test_invalid_refinement_and_runaway_work_are_rejected_before_integration(self):
        cfg, hydro = fixture()
        for factor in (True, 1., '4', 0, 3, 16):
            with self.subTest(factor=factor), self.assertRaisesRegex(ValueError, '细分数'):
                solve_water_quality(cfg, hydro, wq_refinement=factor)
        with self.assertRaisesRegex(ValueError, '1201'):
            refined_chainage(np.linspace(0, 1000, 201), 8)
        with patch('river_model.wq_engine.conservative_step') as advance:
            with self.assertRaisesRegex(ValueError, '计算量过大'):
                solve_water_quality(cfg, hydro, simulation_hours=1., dt_s=1e-9, wq_refinement=8)
            advance.assert_not_called()
        with patch('river_model.wq_engine.conservative_step') as advance:
            with self.assertRaisesRegex(ValueError, '输出数组过大'):
                # 用大河段使稳定性工作量较小，单独检验输出内存门限。
                wide = fixture(0.)[1]
                wide.chainage = np.linspace(0, 100000., 151)
                wide.area = np.full((1,151),40.); wide.discharge = np.zeros((1,151))
                wide.velocity = np.zeros((1,151))
                solve_water_quality(cfg, wide, point_sources=[], simulation_hours=1., dt_s=60.,
                    wq_refinement=8, output_times_h=np.linspace(0,1,2000))
            advance.assert_not_called()

    def test_green_function_reference_improves_with_100_hydro_sections(self):
        from scripts.validate_wq_refinement import benchmark
        report, _ = benchmark()
        self.assertTrue(all(report['checks'].values()), report['checks'])
        self.assertEqual([r['numerics']['grid_nodes'] for r in report['rows']], [100,199,397,793])

    def test_refined_axes_survive_project_roundtrip_playback_and_sampling(self):
        from backend.project_codec import dump_project, load_project
        from backend.river_playback import playback_data
        from backend.session import Session
        from river_model.cross_sections import generate_cross_sections
        cfg, hydro = fixture()
        result = solve_water_quality(cfg, hydro, load_kg=1., chainage_m=100.,
            simulation_hours=.05, duration_min=.5, wq_refinement=4)
        result.params['hydro_run_id'] = 'test_hydro'
        session = Session(id='refined-roundtrip')
        state = session.get_river_state()
        state.config, state.hydro_result, state.wq_result = cfg, hydro, result
        state.cross_sections = generate_cross_sections(5, channel_length_m=240.)
        state.model_initialized = True
        session.river_ui = {'form': {'wq_refinement': 4}, 'pollution': {'数值诊断': result.params['numerical_diagnostics']}}
        restored = load_project(dump_project(session, '网格回环')).state
        model = restored['river_state']
        self.assertEqual(len(model.hydro_result.chainage), 5)
        self.assertEqual(len(model.wq_result.chainage), 17)
        np.testing.assert_array_equal(model.wq_result.ammonia, result.ammonia)
        self.assertEqual(restored['river_ui'], session.river_ui)
        payload = playback_data(model, None, 'ammonia', 'V', 121)
        self.assertEqual(len(payload['chainage']), 17)
        sample = sample_water_quality(model.wq_result, np.array([.025]), np.array([100.]), 'ammonia')
        self.assertTrue(np.isfinite(sample).all())


if __name__ == '__main__':
    unittest.main()
