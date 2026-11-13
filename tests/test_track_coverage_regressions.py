"""Length suggestions for repeated single-corridor tracks and rejected geometry."""
import unittest

import numpy as np
import pandas as pd

from river_model.cruise_alignment import distance
from river_model.parameter_estimator import RiverParameterEstimator


class TrackCoverageTests(unittest.TestCase):
    def setUp(self):
        phase = np.linspace(0, 1, 151)
        self.leg = np.column_stack((30 + .0007 * np.sin(np.pi * phase), 120 + .013 * phase))
        self.estimator = RiverParameterEstimator()

    def extract(self, points, **columns):
        frame = pd.DataFrame({'latitude': points[:, 0], 'longitude': points[:, 1], **columns})
        return self.estimator._extract_gps_info(frame)

    def test_four_passes_measure_the_curved_reach_once(self):
        reverse = self.leg[::-1] + [0.00001, 0.]
        info = self.extract(np.vstack((self.leg, reverse, self.leg, reverse)))
        expected = distance(self.leg[:-1], self.leg[1:]).sum()
        self.assertAlmostEqual(info['total_length_m'] / expected, 1., delta=.015)
        self.assertEqual(info['length_source'], 'gps_repeated_coverage')
        self.assertIsNone(info['gps_mapping'])
        self.assertIsNone(info['estimated_width_m'])

    def test_offset_return_and_missing_observations_are_rejected(self):
        branch = self.leg[::-1].copy()
        branch[:, 0] += .0005 * np.sin(np.linspace(0, np.pi, len(branch)))
        with self.assertRaisesRegex(ValueError, '无法确认沿同一河段重合'):
            self.extract(np.vstack((self.leg, branch)))
        repeated = np.vstack((self.leg, self.leg[::-1]))
        observed = np.ones(len(repeated), dtype=bool)
        observed[50] = False
        with self.assertRaisesRegex(ValueError, '缺失或无效 GPS'):
            self.extract(repeated, __observed_coordinates=observed)

    def test_single_pass_keeps_existing_length_source(self):
        info = self.extract(self.leg)
        self.assertEqual(info['length_source'], 'gps_track_unregistered')
        self.assertAlmostEqual(info['total_length_m'], distance(self.leg[:-1], self.leg[1:]).sum(), delta=.001)
