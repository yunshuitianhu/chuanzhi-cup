"""多排放事件的质量、边界与旧接口等价性。"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.config import RiverConfig
from river_model.wq_engine import solve_water_quality
from test_transport_regressions import still_hydro


class MultiSourceTests(unittest.TestCase):
    def run_model(self, **kwargs):
        config = RiverConfig(k_cbod_decay=0., k_nitrification=0.,
                             k_sediment_oxygen_demand=0., bod_cbod_mgL=0.)
        return solve_water_quality(config, still_hydro(np.linspace(0, 200, 11)),
                                   simulation_hours=.1, dt_s=13., **kwargs)

    def event(self, **kw):
        event = dict(source_id='A', pollutant_type='ammonia', load_kg=2.,
                     chainage_m=80., duration_min=1., source_start_h=.01)
        event.update(kw)
        return event

    def test_one_explicit_event_matches_legacy_interface(self):
        event = self.event()
        a = self.run_model(**{k: v for k, v in event.items() if k != 'source_id'})
        b = self.run_model(point_sources=[event])
        for field in ('ammonia', 'cbod', 'dissolved_oxygen', 'nitrate', 't'):
            np.testing.assert_array_equal(getattr(a, field), getattr(b, field))

    def test_overlapping_sources_conserve_each_component_and_order(self):
        events = [self.event(), self.event(source_id='B', chainage_m=120., load_kg=3.),
                  self.event(source_id='B', pollutant_type='cbod', load_kg=4.)]
        a = self.run_model(point_sources=events)
        b = self.run_model(point_sources=list(reversed(events)))
        np.testing.assert_allclose(a.ammonia, b.ammonia, atol=1e-12)
        np.testing.assert_allclose(a.dissolved_oxygen, b.dissolved_oxygen, atol=1e-12)
        for kind, mass in (('ammonia', 5.), ('cbod', 4.)):
            ledger = a.params['mass_balance'][kind]
            self.assertAlmostEqual(ledger['injected_kg'], mass, places=10)
            self.assertAlmostEqual(ledger['residual_kg'], 0., places=9)
        self.assertEqual(a.params['source_mode'], 'explicit_events')
        self.assertIsNone(a.params['chainage_m'])

    def test_partial_and_future_events_only_inject_overlap(self):
        events = [self.event(source_start_h=.09, duration_min=6., load_kg=10.),
                  self.event(source_id='future', source_start_h=.2)]
        result = self.run_model(point_sources=events)
        self.assertAlmostEqual(result.params['mass_balance']['ammonia']['injected_kg'], 1., places=10)
        self.assertAlmostEqual(result.params['point_sources'][0]['injected_kg'], 1., places=10)
        self.assertEqual(result.params['point_sources'][1]['injected_kg'], 0.)
        self.assertIn(.09, result.t)

    def test_empty_list_is_clean_control(self):
        result = self.run_model(point_sources=[])
        self.assertEqual(result.params['point_sources'], [])
        self.assertEqual(result.params['mass_balance']['ammonia']['injected_kg'], 0.)

    def test_invalid_events_fail_instead_of_silently_falling_back(self):
        for events in ({}, [None], [{}], [self.event(load_kg=-1)],
                       [self.event(chainage_m=201)], [self.event(duration_min=0)],
                       [self.event(source_start_h=float('nan'))], [self.event(pollutant_type='cod')]):
            with self.subTest(events=events), self.assertRaises(ValueError):
                self.run_model(point_sources=events)


if __name__ == '__main__':
    unittest.main()
