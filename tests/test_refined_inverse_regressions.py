"""独立网格反演的拒绝条件与线性捷径契约；原生解析验收另见验证脚本。"""
from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from river_model.inverse import fit_single_source
from river_model.wq_grid import refined_chainage
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality
from river_model.config import RiverConfig
from scripts.validate_refined_inverse import uniform_hydro, analytic_observations


class RefinedInverseTests(unittest.TestCase):
    def setUp(self):
        self.cfg = RiverConfig(k_nitrification=0.)
        self.hydro = uniform_hydro(100., 8., 1., .25, 3)
        self.t = np.array([.01, .02, .03, .04])
        self.x = np.array([40., 50., 60., 70.])
        self.held_t = self.t+.005
        self.c = .2+.5*(1+self.t)*(1+self.x/100)
        self.held_c = .2+.5*(1+self.held_t)*(1+self.x/100)

    def mock_forward(self, config, hydro, **kwargs):
        x = refined_chainage(hydro.chainage, kwargs['wq_refinement'])
        t = np.unique(np.r_[0., kwargs['output_times_h']])
        # 与时间、位置相关的受控响应；不替代原生输移精度验证。
        load = kwargs['load_kg']
        amplitude = load if config.k_nitrification == 0 else load/(1+.1*load)
        c = .2+amplitude*(1+t[:, None])*(1+x[None, :]/100)
        return SimpleNamespace(t=t, chainage=x, ammonia=c,
            params={'numerical_diagnostics': {'transport_steps_upper_bound': 4}})

    def fit(self, **changes):
        args = dict(holdout=dict(times_h=self.held_t, chainages_m=self.x,
                    concentrations_mgL=self.held_c, sigma_mgL=.001),
                    response_gain_bounds=(1., 1.), calibration_reference='受控测试，非现场记录')
        args.update(changes)
        with patch('river_model.inverse.solve_water_quality', side_effect=self.mock_forward):
            return fit_single_source(self.cfg, self.hydro, self.t, self.x, self.c, .001,
                                     [12.5], (0., 1.), **args)

    def test_source_candidates_snap_to_water_quality_grid(self):
        result = self.fit()
        self.assertEqual(result['status'], 'identified_in_scenario')
        self.assertEqual(result['estimate']['source_m'], 12.5)
        self.assertEqual(result['source_grid_min_spacing_m'], 12.5)
        self.assertAlmostEqual(result['estimate']['load_kg'], .5)
        self.assertFalse(result['diagnostics']['holdout']['used_for_optimization'])

    def test_unknown_calibration_refuses_a_good_conditional_fit(self):
        for changes in (dict(response_gain_bounds=None), dict(calibration_reference=None)):
            result = self.fit(**changes)
            self.assertEqual(result['status'], 'calibration_unverified')
            self.assertIsNone(result['estimate'])
            self.assertIsNotNone(result['conditional_fit'])

    def test_wide_gain_interval_cannot_resolve_load(self):
        result = self.fit(response_gain_bounds=(.5, 1.5))
        self.assertEqual(result['status'], 'load_unresolved')
        self.assertIsNone(result['estimate'])

    def test_small_mass_does_not_become_boundary_only_because_of_units(self):
        t, x = self.t, self.x
        c = .2+1e-4*(1+t)*(1+x/100)
        held = .2+1e-4*(1+self.held_t)*(1+x/100)
        with patch('river_model.inverse.solve_water_quality', side_effect=self.mock_forward):
            result = fit_single_source(self.cfg, self.hydro, t, x, c, 1e-7, [12.5], (0., 2e-4),
                holdout=dict(times_h=self.held_t, chainages_m=x, concentrations_mgL=held, sigma_mgL=1e-7),
                response_gain_bounds=(1., 1.), calibration_reference='受控测试')
        self.assertEqual(result['status'], 'identified_in_scenario')
        self.assertAlmostEqual(result['estimate']['load_kg'], 1e-4)

    def test_missing_holdout_is_not_accepted(self):
        self.assertEqual(self.fit(holdout=None)['status'], 'holdout_missing')

    def test_holdout_values_do_not_change_fit_or_candidate_ranking(self):
        good = self.fit()
        self.held_c = self.held_c+.1
        bad = self.fit()
        self.assertEqual(bad['status'], 'holdout_mismatch')
        self.assertEqual(good['candidate_fits'], bad['candidate_fits'])
        self.assertIsNone(bad['estimate'])

    def test_grid_error_rejects_otherwise_good_fit(self):
        original = self.mock_forward
        def unresolved(config, hydro, **kwargs):
            result = original(config, hydro, **kwargs)
            if kwargs['wq_refinement'] == 8:
                result.ammonia = .2+2*(result.ammonia-.2)
            return result
        self.mock_forward = unresolved
        self.assertEqual(self.fit()['status'], 'numerical_unresolved')

    def test_overlapping_holdout_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '训练集与留出集'):
            self.fit(holdout=dict(times_h=self.t, chainages_m=self.x,
                                 concentrations_mgL=self.c, sigma_mgL=.001))

    def test_invalid_gain_refinement_and_budget_rejected(self):
        for changes in (dict(response_gain_bounds=(0., 1.)), dict(response_gain_bounds=(1.1, 2.)),
                        dict(response_gain_bounds=(1., np.nan)), dict(wq_refinement=8),
                        dict(wq_refinement=True), dict(max_forward_evaluations=1),
                        dict(calibration_reference='')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.fit(**changes)
        with self.assertRaisesRegex(ValueError, '次数超预算'):
            self.fit(max_forward_evaluations=2)

    def test_inconsistent_hydrodynamics_is_rejected(self):
        self.hydro.velocity *= 2
        with self.assertRaisesRegex(ValueError, 'u=Q/A'):
            self.fit()

    def test_cumulative_work_budget_stops_without_estimate(self):
        original = self.mock_forward
        def expensive(config, hydro, **kwargs):
            result = original(config, hydro, **kwargs)
            result.params['numerical_diagnostics']['transport_steps_upper_bound'] = 2_000_000
            return result
        self.mock_forward = expensive
        with self.assertRaisesRegex(ValueError, '累计节点更新超预算'):
            self.fit()

    def test_inputs_remain_unchanged_and_old_commands_remain_importable(self):
        from scripts.validate_steady_inverse import run as cross_river
        from scripts.validate_inverse_robustness import run as robustness
        self.assertTrue(callable(cross_river) and callable(robustness))
        snapshots = [a.copy() for a in (self.t, self.x, self.c, self.hydro.area, self.hydro.discharge)]
        self.fit()
        for actual, expected in zip((self.t, self.x, self.c, self.hydro.area, self.hydro.discharge), snapshots):
            np.testing.assert_array_equal(actual, expected)

    def test_nonlinear_response_uses_optimizer_and_recovers_mass(self):
        self.cfg = replace(self.cfg, k_nitrification=.15)
        amplitude = .5/1.05
        self.c = .2+amplitude*(1+self.t)*(1+self.x/100)
        self.held_c = .2+amplitude*(1+self.held_t)*(1+self.x/100)
        result = self.fit()
        self.assertEqual(result['status'], 'identified_in_scenario')
        self.assertAlmostEqual(result['estimate']['load_kg'], .5, places=4)
        self.assertFalse(result['diagnostics']['linear_ammonia_response'])

    def test_native_no_nitrification_is_linear_in_ammonia_load(self):
        cfg = RiverConfig(width_avg_m=8., depth_avg_m=1., depth_range_m=(1., 1.),
            ammonia_nh3_mgL=.2, k_nitrification=0., bod_cbod_mgL=5.)
        hydro = uniform_hydro(100., 8., 1., .25, 11)
        values = []
        for load in (0., 1., .37):
            result = solve_water_quality(cfg, hydro, load_kg=load, chainage_m=25.,
                simulation_hours=.04, duration_min=1., dt_s=5., wq_refinement=4,
                output_times_h=[.01, .02, .03, .04])
            values.append(sample_water_quality(result, self.t, [40., 40., 40., 40.]))
        np.testing.assert_allclose(values[2], values[0]+.37*(values[1]-values[0]), atol=1e-12)

    def test_analytic_reference_integrates_mass_without_native_solver(self):
        x = np.linspace(-200., 800., 4001)
        c = analytic_observations(np.full(len(x), .1), x, width=8., depth=1.,
            speed=.25, source=100., mass=.5, start=.01, duration=1.)
        self.assertAlmostEqual(float(np.trapezoid(c-.2, x)*8/1000), .5, places=10)


if __name__ == '__main__':
    unittest.main()
