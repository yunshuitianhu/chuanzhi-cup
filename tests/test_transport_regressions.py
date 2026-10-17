"""独立物理量验收：质量积分、排放覆盖时间、流向与一阶反应解析解。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from river_model.config import YayaoConfig
from river_model.state import HydroResult
from river_model.wq_engine import solve_water_quality


def still_hydro(x, velocity=0, area=100):
    x = np.asarray(x, dtype=float)
    return HydroResult(t=np.array([0.]), chainage=x,
        water_level=np.ones((1, len(x))), discharge=np.full((1, len(x)), velocity * area),
        velocity=np.full((1, len(x)), velocity), area=np.full((1, len(x)), area))


def integrated_kg(concentration, x, area=100):
    # 梯形积分是节点控制体积质量总和，独立于求解器质量诊断代码。
    return float(np.trapezoid(concentration * area, x) / 1000)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.cfg = YayaoConfig(ammonia_nh3_mgL=0., bod_cbod_mgL=0.)

    def solve_without_reaction(self, h, **kwargs):
        with patch('river_model.wq_engine._reaction_step', side_effect=lambda c, n, o, *a: (c, n, o)):
            return solve_water_quality(self.cfg, h, **kwargs)

    def test_one_kg_is_one_kg_across_grids_steps_and_source_locations(self):
        grids = [np.array([0., 100., 200.]), np.linspace(0, 200, 11),
                 np.array([0., 30., 100., 170., 200.])]
        for x in grids:
            for step in (1., 7., 60., 120.):
                for source in (0., 100., 200.):
                    with self.subTest(n=len(x), step=step, source=source):
                        with patch('river_model.wq_engine._fischer_dispersion', return_value=np.zeros(len(x))):
                            result = self.solve_without_reaction(still_hydro(x), load_kg=1,
                                chainage_m=source, duration_min=1, simulation_hours=1/60, dt_s=step)
                        self.assertAlmostEqual(integrated_kg(result.ammonia[-1], x), 1., places=10)
                        self.assertAlmostEqual(result.t[-1], 1/60, places=10)

    def test_real_diffusion_preserves_mass_and_nonnegative_concentration(self):
        x = np.linspace(0, 200, 21)
        with patch('river_model.wq_engine._fischer_dispersion', return_value=np.full(len(x), 20.)):
            result = self.solve_without_reaction(still_hydro(x), load_kg=1,
                chainage_m=100, duration_min=.5, simulation_hours=.1, dt_s=60)
        self.assertAlmostEqual(integrated_kg(result.ammonia[-1], x), 1., places=9)
        self.assertTrue(np.all(result.ammonia >= 0))
        self.assertGreater(np.count_nonzero(result.ammonia[-1] > 1e-8), 3)

    def test_short_simulation_injects_only_covered_fraction(self):
        x = np.linspace(0, 200, 5)
        result = self.solve_without_reaction(still_hydro(x), load_kg=1,
            chainage_m=100, duration_min=1, simulation_hours=1/120, dt_s=7)
        self.assertAlmostEqual(integrated_kg(result.ammonia[-1], x), .5, places=10)

    def test_advection_moves_plume_in_both_directions_and_balances_outflow(self):
        x = np.linspace(0, 1000, 101)
        for speed in (.1, -.1):
            with self.subTest(speed=speed):
                with patch('river_model.wq_engine._fischer_dispersion', return_value=np.zeros(len(x))):
                    result = self.solve_without_reaction(still_hydro(x, speed), load_kg=1,
                        chainage_m=500, duration_min=1, simulation_hours=.1, dt_s=10)
                c = result.ammonia[-1]
                center = np.trapezoid(c * x, x) / np.trapezoid(c, x)
                self.assertGreater((center - 500) * speed, 0)
                ledger = result.params['mass_balance']['ammonia']
                self.assertAlmostEqual(integrated_kg(c, x) + ledger['outflow_kg'], 1., places=9)

    def test_uniform_first_order_decay_matches_analytic_solution(self):
        x = np.linspace(0, 200, 11)
        # 无限供氧的数学极限下应恢复一阶解析解，不把该高 DO 当作现场条件。
        cfg = YayaoConfig(ammonia_nh3_mgL=3., water_temp_C=20., k_nitrification=.1,
                          dissolved_oxygen_mgL=1e8, bod_cbod_mgL=0., k_sediment_oxygen_demand=0.)
        with patch('river_model.wq_engine._owens_gibbs_ka', return_value=np.zeros(len(x))):
            result = solve_water_quality(cfg, still_hydro(x), load_kg=0,
                chainage_m=100, duration_min=1, simulation_hours=1, dt_s=60)
        expected = 3 * np.exp(-.1 / 24)
        np.testing.assert_allclose(result.ammonia[-1], expected, rtol=1e-9)
        self.assertAlmostEqual(result.params['mass_balance']['ammonia']['residual_kg'], 0., places=9)

    def test_bad_source_or_timing_is_rejected(self):
        h = still_hydro([0, 100, 200])
        for kwargs in ({'dt_s': 0}, {'load_kg': -1}, {'chainage_m': -1},
                       {'pollutant_type': 'cod'}, {'duration_min': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                solve_water_quality(self.cfg, h, chainage_m=kwargs.get('chainage_m', 100),
                    **{k: v for k, v in kwargs.items() if k != 'chainage_m'})

    def test_known_bad_tidal_hydrodynamics_cannot_feed_water_quality(self):
        h=still_hydro([0,100,200])
        h.params={'engine':'pure_python_preissmann','tidal_amplitude_m':.8}
        with self.assertRaisesRegex(ValueError,'水量守恒'):
            solve_water_quality(self.cfg,h,chainage_m=100)


if __name__ == '__main__':
    unittest.main()
