"""2026-10-28 全仓审查发现的回归；受控数据，不访问外部服务。"""
from __future__ import annotations

import ast
import base64
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
import requests
from fastapi import HTTPException

from backend.agent.tools import execute_tool
from backend.agent.loop import run_agent
from backend.map_samples import map_samples
from backend.routers.charts import trace_map
from backend.routers.river import InitRequest, PollutionRequest, init_model, pollution
from backend.routers.trace import _default_center, _track_center
from backend.session import Session
from river_model.state import HydroResult, RiverModelState, WQResult
from water_quality_mcp.src.water_quality_mcp import server
from water_quality_mcp.src.water_quality_mcp import state as wq_state
from water_quality_mcp.src.water_quality_mcp.analysis import build_comprehensive_summary, find_black_spots
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
from water_quality_mcp.src.water_quality_mcp.data_quality import coordinate_mask, coordinate_text
from water_quality_mcp.src.water_quality_mcp.detection_support import spatial_support
from water_quality_mcp.src.water_quality_mcp.geo_tools import get_satellite_image
from water_quality_mcp.src.water_quality_mcp.loaders import load_data
from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState


def gps_table(rows=7, missing=range(1, 6)):
    """首尾有GPS，中间缺GPS且氨氮严重超标；用于坐标消费边界。"""
    rows = list(range(rows))
    frame = pd.DataFrame({
        '时间': pd.date_range('2026-01-01', periods=len(rows), freq='10s').astype(str),
        '经度': [113.02 if i == max(rows) else 113. if i == 0 else None for i in rows],
        '纬度': [23.02 if i == max(rows) else 23. if i == 0 else None for i in rows],
        '氨氮(mg/L)': [.2 if i in (0, max(rows)) else 18. for i in rows],
        '溶解氧(mg/L)': [7.] * len(rows),
    })
    encoded = base64.b64encode(frame.to_csv(index=False).encode()).decode()
    df, report = load_data(encoded, 'review_missing_gps.csv')
    assert df is not None, report
    return df


def old_hydro():
    axis = np.array([0., 500., 1000.])
    return HydroResult(np.array([0.]), axis, np.full((1, 3), 2.), np.full((1, 3), 6.),
                       np.full((1, 3), .1), np.full((1, 3), 60.), {'run_id': 'old-hydro'})


def old_wq():
    axis = np.array([0., 500., 1000.])
    return WQResult(np.array([0., .5]), axis, ammonia=np.ones((2, 3)),
                    params={'hydro_run_id': 'old-hydro'})


class MissingGpsSpatialTests(unittest.TestCase):
    def setUp(self):
        self.df = gps_table()
        self.raw, self.black = find_black_spots(self.df)
        self.result = json.loads(self.raw)
        self.session = Session(id='review-missing-gps', df=self.df, black_spots=self.black,
                               black_spots_json=self.raw)

    def test_missing_gps_is_not_clustered_but_point_alerts_remain(self):
        self.assertEqual(int(coordinate_mask(self.df).sum()), 2)
        self.assertEqual(len(self.black), 5)
        self.assertTrue((self.black['cluster_id'] == -1).all())
        self.assertEqual(self.result['聚类热点数'], 0)
        self.assertIsNone(coordinate_text(self.black.iloc[0]))
        self.assertEqual(coordinate_text(self.df.iloc[0]), '23.000000, 113.000000')

    def test_map_uses_only_observed_coordinates(self):
        mapped = trace_map(self.session)
        self.assertEqual(len(mapped['track']), 2)
        self.assertEqual(len(mapped['heat']), 2)
        self.assertEqual([s['id'] for s in mapped['samples']], [0, 6])
        self.assertEqual(mapped['top5'], [])
        self.assertEqual(mapped['clusters'], [])
        np.testing.assert_allclose(mapped['center'], [23.01, 113.01])
        self.assertEqual(mapped['bounds']['lat'], [23., 23.02])

    def test_map_samples_skip_filled_coordinates(self):
        samples = map_samples(self.df, AnalysisConfig())
        self.assertEqual([s['id'] for s in samples], [0, 6])
        self.assertEqual([s['coord'] for s in samples], [[23., 113.], [23.02, 113.02]])

    def test_spatial_support_breaks_at_unobserved_coordinates(self):
        frame = pd.DataFrame({
            'datetime': pd.date_range('2026-01-01', periods=4, freq='10s'),
            'latitude': [0., .001, .002, .003],
            'longitude': [0.] * 4,
            '__observed_coordinates': [True, False, False, True],
        })
        support = spatial_support(frame, np.full(4, 18.), window_m=50, min_points=2)
        np.testing.assert_array_equal(support, np.zeros(4))

    def test_default_search_centers_ignore_filled_coordinates(self):
        self.assertIsNone(_default_center(self.session))
        self.assertEqual(_track_center(self.session), (23.02, 113.02))
        summary = json.loads(build_comprehensive_summary(self.session.water_quality_state()))
        self.assertEqual(summary['空间分析']['有效GPS记录数'], 2)
        self.assertIsNone(summary['空间分析']['最密集聚类中心'])


class TokenRedactionTests(unittest.TestCase):
    fake = 'review_FAKE_cloud_token_12345'

    def test_satellite_error_does_not_include_url_or_token(self):
        def fail(url, *args, **kwargs):
            raise requests.ConnectionError('network failure for ' + url)
        with patch('water_quality_mcp.src.water_quality_mcp.geo_tools.requests.get', side_effect=fail):
            raw = get_satellite_image(23., 113., 16, self.fake)
        self.assertNotIn(self.fake, raw)
        self.assertIn('卫星影像请求异常', raw)

    def test_tool_error_is_scrubbed_before_next_model_request(self):
        def resolve(name, *args, **kwargs):
            return self.fake if name == 'CLOUD_TOKEN' else 'review_model_key'
        def chunk(text=None, calls=None, finish='stop'):
            return NS(choices=[NS(delta=NS(content=text, tool_calls=calls), finish_reason=finish)])
        call = NS(index=0, id='review-call',
                  function=NS(name='get_satellite_image', arguments='{"lat":23,"lon":113}'))
        result = json.dumps({'ok': False, 'data': None,
                             'error': 'network failure ' + self.fake, 'skipped': False},
                            ensure_ascii=False)
        session = Session(id='review-token', df=pd.DataFrame({
            'datetime': pd.date_range('2026-01-01', periods=2, freq='5s'),
            'ammonia_nitrogen': [.2, .2], 'dissolved_oxygen': [7., 7.]}))
        with patch('backend.agent.loop.resolve_api_key', side_effect=resolve), \
             patch('water_quality_mcp.src.water_quality_mcp.config.resolve_api_key', side_effect=resolve), \
             patch('backend.agent.loop.execute_tool', return_value=(result, True)), \
             patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect = [
                [chunk(calls=[call], finish='tool_calls')],
                [chunk(text='已完成受控检查')],
            ]
            list(run_agent(session, '检查卫星状态'))
            sent = factory.return_value.chat.completions.create.call_args_list[1].kwargs['messages']
        self.assertNotIn(self.fake, json.dumps(sent, ensure_ascii=False))


class LegacyStreamlitLoaderTests(unittest.TestCase):
    def test_legacy_loader_preserves_zero_do_and_observation_masks(self):
        source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        func = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == 'load_data')
        namespace = {'pd': pd, 'np': np, 'os': os, 'base64': base64,
                     'st': NS(session_state=NS())}
        exec(compile(ast.Module(body=[func], type_ignores=[]), 'app.py', 'exec'), namespace)
        table = pd.DataFrame({
            '时间': pd.date_range('2026-01-01', periods=3, freq='10s').astype(str),
            '氨氮(mg/L)': [.2, .2, .2],
            '溶解氧(mg/L)': [7., 0., 7.],
            '浑浊度(NTU)': [10., 10., 10.],
            'pH': [7., 7., 7.],
        })
        stream = io.BytesIO(table.to_csv(index=False).encode())
        stream.name = 'review_DO_zero.csv'
        with tempfile.TemporaryDirectory() as temporary:
            previous = os.getcwd()
            os.chdir(temporary)
            try:
                report, ok = namespace['load_data'](stream)
            finally:
                os.chdir(previous)
        self.assertTrue(ok, report)
        legacy = namespace['st'].session_state.df
        self.assertEqual(legacy['dissolved_oxygen'].tolist(), [7., 0., 7.])
        self.assertTrue(legacy['__observed_dissolved_oxygen'].all())
        self.assertIn('data_identity', legacy.attrs)
        core, _ = load_data(base64.b64encode(stream.getvalue()).decode(), stream.name)
        self.assertEqual(legacy['dissolved_oxygen'].tolist(), core['dissolved_oxygen'].tolist())
        self.assertEqual(len(find_black_spots(legacy)[1]), len(find_black_spots(core)[1]))
        self.assertEqual(len(find_black_spots(legacy)[1]), 1)


class McpReloadTests(unittest.TestCase):
    def setUp(self):
        self.old_state = server.state
        self.old_river = wq_state._RIVER_STATE
        server.state = WaterQualityState()
        wq_state._RIVER_STATE = None

    def tearDown(self):
        server.state = self.old_state
        wq_state._RIVER_STATE = self.old_river

    def seed(self):
        server.state.df = gps_table()
        server.state.data_filename = 'old.csv'
        server.state.black_spots = pd.DataFrame({'cluster_id': [0]})
        server.state.video_frames = [{'frame': 1}]
        server.state.cross_validation_result = {'from': 'old-video'}
        server.state.national_station_result = {'from': 'old-station'}
        river = wq_state._get_river_state()
        river.model_initialized = True

    def test_successful_reload_invalidates_all_data_dependencies(self):
        self.seed()
        normal = server.state.df.copy()
        normal['ammonia_nitrogen'] = .2
        encoded = base64.b64encode(normal.to_csv(index=False).encode()).decode()
        report = server.tool_load_data(encoded, 'new_normal.csv')
        self.assertIn('数据加载完成', report)
        self.assertEqual(float(server.state.df['ammonia_nitrogen'].max()), .2)
        self.assertEqual(server.state.data_filename, 'new_normal.csv')
        self.assertIsNone(server.state.black_spots)
        self.assertEqual(server.state.video_frames, [])
        self.assertIsNone(server.state.cross_validation_result)
        self.assertIsNone(server.state.national_station_result)
        self.assertIsNone(wq_state._RIVER_STATE)

    def test_failed_reload_keeps_previous_analysis(self):
        self.seed()
        old_black = server.state.black_spots
        old_cross = server.state.cross_validation_result
        bad = base64.b64encode(b'not,a\nvalid,table').decode()
        server.tool_load_data(bad, 'bad.csv')
        self.assertIs(server.state.black_spots, old_black)
        self.assertIs(server.state.cross_validation_result, old_cross)

    def test_video_replacement_only_invalidates_cross_validation(self):
        self.seed()
        black = server.state.black_spots
        server.state.replace_video([{'frame': 2}])
        self.assertEqual(server.state.video_frames, [{'frame': 2}])
        self.assertIs(server.state.black_spots, black)
        self.assertIsNone(server.state.cross_validation_result)


class RiverModelStateSyncTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(
            id='review-model', analysis_done=True, analysis_status='succeeded',
            results_revision=7, comprehensive_summary='old-summary',
            river_state=RiverModelState(model_initialized=True, hydro_result=old_hydro(),
                                        wq_result=old_wq()))

    def assert_invalidated(self):
        self.assertFalse(self.session.analysis_done)
        self.assertEqual(self.session.analysis_status, 'stale')
        self.assertEqual(self.session.results_revision, 8)
        self.assertIsNone(self.session.comprehensive_summary)

    def test_pollution_failure_invalidates_completed_report(self):
        with self.assertRaises(HTTPException) as caught:
            pollution(PollutionRequest(chainage_m=2000., simulation_hours=.5), self.session)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIsNone(self.session.river_state.wq_result)
        self.assert_invalidated()

    def test_init_failure_invalidates_completed_report(self):
        def controlled_failure(state, **kwargs):
            state.model_initialized = False
            state.hydro_result = None
            state.wq_result = None
            state.geometry_revision += 1
            return json.dumps({'error': '受控初始化失败'}, ensure_ascii=False)
        with patch('river_model.tools.tool_river_init', side_effect=controlled_failure):
            with self.assertRaises(HTTPException) as caught:
                init_model(InitRequest(), self.session)
        self.assertEqual(caught.exception.status_code, 400)
        self.assert_invalidated()

    def test_agent_pollution_failure_invalidates_completed_report(self):
        raw, failed = execute_tool('simulate_pollution_event',
                                   {'chainage_m': 2000., 'simulation_hours': .5}, self.session)
        self.assertTrue(failed, raw)
        self.assertIsNone(self.session.river_state.wq_result)
        self.assert_invalidated()


if __name__ == '__main__':
    unittest.main()
