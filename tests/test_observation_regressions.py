"""走航时空观测算子与排放时刻的独立验收。无需 LLM、网络或 HEC-RAS。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.config import YayaoConfig
from river_model.observations import sample_water_quality
from river_model.state import HydroResult, WQResult
from river_model.wq_engine import solve_water_quality


class ObservationTests(unittest.TestCase):
    def setUp(self):
        # 含 x*t 项的双线性解析场，非均匀时空网格；可精确核对逐点而非笛卡尔积。
        self.t, self.x = np.array([0., .3, 2.]), np.array([0., 20., 100.])
        self.field = 2 + 3*self.t[:, None] + .1*self.x + .2*self.t[:, None]*self.x
        self.result = WQResult(t=self.t, chainage=self.x, ammonia=self.field,
                               dissolved_oxygen=np.zeros_like(self.field))

    def test_moving_observations_match_analytic_field_in_original_order(self):
        t, x = np.array([1.1, 0, .2, 2., .2]), np.array([55., 100., 11., 0, 11.])
        actual = sample_water_quality(self.result, t, x)
        self.assertEqual(actual.shape, t.shape)
        np.testing.assert_allclose(actual, 2 + 3*t + .1*x + .2*t*x, atol=1e-13)

    def test_grid_nodes_boundaries_and_zero_do_are_preserved(self):
        np.testing.assert_array_equal(sample_water_quality(self.result, self.t, self.x), self.field.diagonal())
        np.testing.assert_array_equal(sample_water_quality(self.result, [0., 2.], [0., 100.], 'do'), [0., 0.])
        self.assertEqual(len(sample_water_quality(self.result, [], [])), 0)

    def test_no_extrapolation_or_silent_constituent_substitution(self):
        for t, x in (([-.1], [50]), ([2.1], [50]), ([1], [-1]), ([1], [101])):
            with self.subTest(t=t, x=x), self.assertRaisesRegex(ValueError, '禁止外推'):
                sample_water_quality(self.result, t, x)
        for constituent in ('cod', 'nitrate'):
            with self.subTest(constituent=constituent), self.assertRaises(ValueError):
                sample_water_quality(self.result, [1], [50], constituent)

    def test_invalid_axes_fields_and_paired_queries_rejected(self):
        for t, x in (([1, 2], [50]), ([[1]], [[50]]), ([float('nan')], [50])):
            with self.subTest(t=t), self.assertRaises(ValueError):
                sample_water_quality(self.result, t, x)
        for result in (WQResult(t=np.array([0., 0.]), chainage=self.x, ammonia=self.field[:2]),
                       WQResult(t=self.t, chainage=self.x, ammonia=self.field[:, :2]),
                       WQResult(t=self.t, chainage=self.x, ammonia=self.field * np.nan)):
            with self.assertRaises(ValueError):
                sample_water_quality(result, [0.], [0.])

    def test_single_time_and_single_station(self):
        r = WQResult(t=np.array([0.]), chainage=np.array([5.]), ammonia=np.array([[7.]]))
        np.testing.assert_array_equal(sample_water_quality(r, [0., 0.], [5., 5.]), [7., 7.])
        with self.assertRaises(ValueError):
            sample_water_quality(r, [.1], [5.])


class ReleaseTimingTests(unittest.TestCase):
    def solve(self, **kwargs):
        x = np.array([0., 100., 200.])
        h = HydroResult(t=np.array([0.]), chainage=x, water_level=np.ones((1, 3)),
                        discharge=np.zeros((1, 3)), velocity=np.zeros((1, 3)), area=np.full((1, 3), 100.))
        cfg = YayaoConfig(ammonia_nh3_mgL=0., bod_cbod_mgL=0.)
        with patch('river_model.wq_engine._reaction_step', side_effect=lambda c, n, o, *a: (c, n, o)), \
             patch('river_model.wq_engine._fischer_dispersion', return_value=np.zeros(3)):
            return solve_water_quality(cfg, h, load_kg=1., chainage_m=80., duration_min=1.,
                                       **{'simulation_hours': .1, 'dt_s': 70., **kwargs})

    def test_delayed_release_matches_exact_piecewise_mass_curve(self):
        # 23 秒起排，持续 60 秒；时间点不与 70 秒积分步长对齐。
        times_s = np.array([0., 7., 23., 24., 47., 83., 90., 360.])
        r = self.solve(source_start_h=23/3600, output_times_h=times_s/3600)
        mass = np.trapezoid(r.ammonia * 100., r.chainage, axis=1) / 1000
        expected = np.clip((r.t*3600 - 23) / 60, 0, 1)
        np.testing.assert_allclose(mass, expected, atol=1e-13)
        self.assertAlmostEqual(r.params['mass_balance']['ammonia']['residual_kg'], 0., places=12)
        self.assertEqual(r.params['source_effective_chainage_m'], 100.)
        self.assertEqual(r.params['source_snap_distance_m'], 20.)

    def test_partial_and_future_release_do_not_inject_full_load(self):
        for start_s, expected in ((340., 1/3), (360., 0.), (400., 0.)):
            r = self.solve(source_start_h=start_s/3600)
            mass = np.trapezoid(r.ammonia[-1] * 100., r.chainage) / 1000
            self.assertAlmostEqual(mass, expected, places=12)

    def test_short_runs_have_regular_output_and_requested_times_are_exact(self):
        r = self.solve(simulation_hours=.5, dt_s=60.)
        np.testing.assert_allclose(r.t, np.arange(31)/60)
        r = self.solve(output_times_h=np.array([.0091, .0337]))
        for value in (.0091, .0337, 0., .1):
            self.assertLess(np.min(abs(r.t - value)), 1e-15)

    def test_invalid_timing_and_excessive_output_rejected(self):
        for kwargs in ({'source_start_h': -.1}, {'source_start_h': float('inf')},
                       {'output_interval_s': 0.}, {'output_interval_s': .001},
                       {'output_times_h': [-.01]}, {'output_times_h': [.2]},
                       {'output_times_h': [[0.]]}, {'output_times_h': [float('nan')]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.solve(**kwargs)

    def test_time_varying_hydrodynamics_must_cover_simulation(self):
        x = np.array([0., 100., 200.])
        h = HydroResult(t=np.array([0., .1]), chainage=x, water_level=np.ones((2, 3)),
                        discharge=np.zeros((2, 3)), velocity=np.zeros((2, 3)), area=np.full((2, 3), 100.))
        with self.assertRaisesRegex(ValueError, '未覆盖'):
            solve_water_quality(YayaoConfig(), h, chainage_m=100., simulation_hours=.5)


if __name__ == '__main__':
    unittest.main()
