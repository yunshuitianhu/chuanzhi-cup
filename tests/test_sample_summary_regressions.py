"""真实样例暴露的统计摘要缺陷；不需要外部文件或服务。"""
import json
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from water_quality_mcp.src.water_quality_mcp.analysis import data_summary


class SummaryRegressionTests(unittest.TestCase):
    def test_do_minimum_zero_with_other_maximum_indicators(self):
        df = pd.DataFrame({'datetime': pd.date_range('2025-08-01', periods=3),
                           'dissolved_oxygen': [2., 0., 5.36],
                           'ammonia_nitrogen': [4., 560.15, 0.], 'ph': [5.16, 6., 7.38]})
        result = json.loads(data_summary(df))
        self.assertEqual(result['溶解氧']['极值'], 0.)
        self.assertEqual(result['氨氮']['极值'], 560.15)
        self.assertEqual(result['pH']['极值'], 7.38)

    def test_empty_indicator_serializes_as_null_in_strict_json(self):
        df = pd.DataFrame({'datetime': pd.date_range('2025-08-01', periods=2),
                           'dissolved_oxygen': [float('nan'), float('inf')]})
        result = json.loads(data_summary(df))
        self.assertIsNone(result['溶解氧']['极值'])
        self.assertIsNone(result['溶解氧']['均值'])
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
