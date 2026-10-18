"""真实样例暴露的河道结果语义缺陷。"""
import json
import sys
import unittest
from contextlib import ExitStack, chdir
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.state import RiverModelState, WQResult
from river_model import tools


class ArrivalRegressionTests(unittest.TestCase):
    def simulate(self, downstream):
        state = RiverModelState(hydro_result=SimpleNamespace(params={}, chainage=np.array([0., 50., 100.])))
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


class ArtifactRegressionTests(unittest.TestCase):
    def test_failed_generation_does_not_report_a_stale_file(self):
        with TemporaryDirectory() as tmp, chdir(tmp):
            Path('chart.png').write_bytes(b'old output')
            def fail(path):
                Path(path).write_bytes(b'partial new output')
                raise RuntimeError('plot failed')
            files, warnings = tools._generate_files([('chart.png', fail)])
            self.assertEqual(files, [])
            self.assertEqual(Path('chart.png').read_bytes(), b'old output')
            self.assertIn('plot failed', warnings[0]['原因'])
            self.assertEqual(list(Path('.').glob('.river-output-*')), [])

    def test_later_outputs_survive_an_earlier_failure(self):
        with TemporaryDirectory() as tmp, chdir(tmp):
            files, warnings = tools._generate_files([
                ('missing.png', lambda path: None),
                ('good.png', lambda path: Path(path).write_bytes(b'new result')),
            ])
            self.assertEqual(files, ['good.png'])
            self.assertEqual(Path(files[0]).read_bytes(), b'new result')
            self.assertEqual(warnings[0]['文件'], 'missing.png')

    def test_empty_file_is_not_a_successful_output(self):
        with TemporaryDirectory() as tmp, chdir(tmp):
            files, warnings = tools._generate_files([('empty.png', lambda path: Path(path).touch())])
            self.assertEqual(files, [])
            self.assertEqual(len(warnings), 1)

    def test_current_dataframe_mapping_does_not_modify_session(self):
        from river_model.geo_mapping import build_chainage_gps_mapping
        df = pd.DataFrame({'datetime':pd.date_range('2025-08-01', periods=20),
                           'longitude':np.linspace(113.1,113.11,20),
                           'latitude':np.linspace(23.1,23.11,20)})
        original = df.copy(deep=True)
        mapping = build_chainage_gps_mapping(df)
        self.assertEqual(mapping['point_count'], 20)
        self.assertGreater(mapping['total_length_m'], 0)
        pd.testing.assert_frame_equal(df, original)


if __name__ == '__main__':
    unittest.main()
