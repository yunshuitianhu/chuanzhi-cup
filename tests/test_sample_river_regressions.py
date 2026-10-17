"""真实样例暴露的河道结果语义缺陷。"""
import json
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.state import RiverModelState, WQResult
from river_model import tools


class ArrivalRegressionTests(unittest.TestCase):
    def simulate(self, downstream):
        state = RiverModelState(hydro_result=SimpleNamespace(params={}))
        data = np.column_stack([np.ones(3)*2, np.ones(3)*2, downstream])
        result = WQResult(t=np.array([0., .25, .5]), chainage=np.array([0., 50., 100.]), ammonia=data)
        with ExitStack() as stack:
            stack.enter_context(patch.object(tools, 'solve_water_quality', return_value=result))
            for name in ('plot_concentration_contour', 'plot_concentration_profile',
                         'plot_concentration_timeseries', 'plot_hydro_wq_dashboard'):
                stack.enter_context(patch.object(tools, name))
            return json.loads(tools.tool_river_pollution_event(state, simulation_hours=.5))

    def test_no_threshold_crossing_has_no_arrival_time(self):
        result = self.simulate([2., 2.1, 2.6])
        self.assertIsNone(result['下游到达时间_h'])
        self.assertFalse(result['下游到达判定']['已达到阈值'])

    def test_first_crossing_time_is_reported(self):
        result = self.simulate([2., 3., 4.])
        self.assertEqual(result['下游到达时间_h'], .25)
        self.assertTrue(result['下游到达判定']['已达到阈值'])

    def test_zero_baseline_without_increase_is_not_arrival(self):
        self.assertIsNone(self.simulate([0., 0., 0.])['下游到达时间_h'])


if __name__ == '__main__':
    unittest.main()
