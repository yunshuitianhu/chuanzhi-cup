"""独立化学计量、无氧极限、氮质量积分和高精度 ODE 参照。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.integrate import solve_ivp

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.wq_engine import _reaction_step, solve_water_quality
from river_model.config import YayaoConfig
from test_transport_regressions import still_hydro, integrated_kg


class ReactionTests(unittest.TestCase):
    def react(self, n=3., oxygen=2., cbod=0., seconds=3600., **overrides):
        args = dict(H=np.array([2.]), u=np.array([0.]), K_d=0., K_s=0.,
                    beta1=.15, SOD=0., O_sat=8., T_C=20., dt_s=seconds,
                    theta_d=1.047, theta_n=1.072, theta_rea=1.024)
        args.update(overrides)
        with patch('river_model.wq_engine._owens_gibbs_ka', return_value=np.array([0.])):
            return _reaction_step(np.array([cbod]), np.array([n]), np.array([oxygen]), **args)

    def test_nitrification_consumes_stoichiometric_oxygen(self):
        _, nitrogen, oxygen = self.react()
        self.assertLess(nitrogen[0], 3.)
        self.assertLess(oxygen[0], 2.)
        self.assertAlmostEqual(2.-oxygen[0], 4.57*(3.-nitrogen[0]), places=12)

    def test_no_oxygen_prevents_nitrification(self):
        _, nitrogen, oxygen = self.react(oxygen=0., seconds=86400.)
        self.assertEqual(nitrogen[0], 3.)
        self.assertEqual(oxygen[0], 0.)

    def test_low_oxygen_slows_nitrification(self):
        low = self.react(oxygen=.05)[1][0]
        high = self.react(oxygen=8.)[1][0]
        self.assertGreater(low, high)

    def test_increased_ammonia_now_changes_do(self):
        low = self.react(n=1.)[2][0]
        high = self.react(n=10.)[2][0]
        self.assertGreater(low, high)

    def test_extreme_nitrogen_cannot_consume_more_oxygen_than_available(self):
        _, nitrogen, oxygen = self.react(n=560.15, oxygen=.01, seconds=3600.)
        self.assertGreaterEqual(oxygen[0], 0.)
        self.assertGreaterEqual(nitrogen[0], 560.15 - .01/4.57 - 1e-10)
        self.assertAlmostEqual(.01-oxygen[0], 4.57*(560.15-nitrogen[0]), places=9)

    def test_zero_do_allows_settling_but_no_aerobic_cbod_oxidation(self):
        carbon, _, oxygen = self.react(cbod=5., oxygen=0., seconds=86400., K_d=.2, K_s=.05)
        self.assertAlmostEqual(carbon[0], 5*np.exp(-.05), places=6)
        self.assertEqual(oxygen[0], 0.)

    def test_coupled_reaction_matches_independent_radau_reference(self):
        # 显式写出科学方程并使用不同求解器，不调用被测 RHS 或质量账本。
        def ode(t, y):
            L, N, O = y
            carbon = .2*L*O/(.5+O)
            nitrogen = .15*N*O/(.6+O)
            return np.array([-carbon-.05*L, -nitrogen,
                .3*(8.-O)-carbon-4.57*nitrogen-.5/2*O/(.5+O)])/86400.
        expected = solve_ivp(ode, (0., 21600.), [5., 3., 2.], method='Radau',
                             rtol=1e-11, atol=1e-12)
        self.assertTrue(expected.success)
        with patch('river_model.wq_engine._owens_gibbs_ka', return_value=np.array([.3])):
            actual = _reaction_step(np.array([5.]), np.array([3.]), np.array([2.]),
                np.array([2.]), np.array([.05]), .2, .05, .15, .5,
                8., 20., 21600., 1.047, 1.072, 1.024)
        np.testing.assert_allclose(np.array(actual)[:,0], expected.y[:,-1], rtol=2e-6, atol=1e-8)

    def test_transport_and_reaction_preserve_nitrogen_and_oxygen_budget(self):
        x = np.linspace(0, 200, 11)
        cfg = YayaoConfig(bod_cbod_mgL=0., ammonia_nh3_mgL=3., nitrate_n_mgL=.5,
                          water_temp_C=20., k_sediment_oxygen_demand=0.)
        with patch('river_model.wq_engine._owens_gibbs_ka', return_value=np.zeros(len(x))):
            result = solve_water_quality(cfg, still_hydro(x), load_kg=1.,
                chainage_m=100., duration_min=1., simulation_hours=1., dt_s=7.)
        initial = integrated_kg(result.ammonia[0]+result.nitrate[0], x)
        final = integrated_kg(result.ammonia[-1]+result.nitrate[-1], x)
        self.assertAlmostEqual(final-initial, 1., places=9)
        for ledger in result.params['mass_balance'].values():
            self.assertAlmostEqual(ledger['residual_kg'], 0., places=8)
        n_removed = -result.params['mass_balance']['ammonia']['reaction_change_kg']
        n_added = result.params['mass_balance']['nitrate']['reaction_change_kg']
        oxygen_removed = -result.params['mass_balance']['do']['reaction_change_kg']
        self.assertAlmostEqual(n_removed, n_added, places=10)
        self.assertAlmostEqual(oxygen_removed, 4.57*n_removed, places=9)
        self.assertTrue(np.all(result.nitrate >= 0))

    def test_invalid_reaction_parameters_are_rejected(self):
        for args in ({'beta1':-1.}, {'oxygen_half_sat_n':0.}, {'T_C':float('nan')}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.react(**args)


if __name__ == '__main__':
    unittest.main()
