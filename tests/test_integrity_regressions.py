"""第二轮审查的正确性回归：缺测、状态依赖、流程终态与质量核算。"""
import base64
import json
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import Session, STORE
from water_quality_mcp.src.water_quality_mcp.loaders import load_data
from water_quality_mcp.src.water_quality_mcp.analysis import find_black_spots, build_comprehensive_summary
from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
from river_model.state import RiverModelState, HydroResult, WQResult
from river_model import tools as river_tools
from backend.agent.loop import run_agent, REQUIRED_ANALYSIS_STEPS
from backend.agent.tools import execute_tool


def hydro(amplitude=0):
    return HydroResult(t=np.array([0., 1.]), chainage=np.array([0., 100., 200.]),
                       water_level=np.ones((2, 3)), discharge=np.ones((2, 3)),
                       velocity=np.ones((2, 3)) * .01, area=np.ones((2, 3)) * 100,
                       params={'tidal_amplitude_m': amplitude})


def monitoring():
    return pd.DataFrame({'datetime': pd.to_datetime(['2025-08-01 10:00:00']),
                         'ammonia_nitrogen': [3.], 'dissolved_oxygen': [4.]})


class DataValidityTests(unittest.TestCase):
    def test_positive_detection_without_gps_does_not_invent_coordinates(self):
        df = monitoring()
        df['ammonia_nitrogen'] = 20.
        _, black = find_black_spots(df)
        state = WaterQualityState()
        state.df, state.black_spots = df, black
        data = json.loads(build_comprehensive_summary(state))
        self.assertIsNone(data['黑臭检测']['Top5最严重点位'][0]['经纬度'])

    def test_zero_spots_is_still_a_completed_detection_in_summary(self):
        df = monitoring()
        _, black = find_black_spots(df)
        state = WaterQualityState()
        state.df, state.black_spots = df, black
        self.assertEqual(json.loads(build_comprehensive_summary(state))['黑臭检测']['黑点总数'], 0)
    def test_invalid_upload_does_not_replace_a_valid_session(self):
        with TestClient(app) as client:
            sid = client.get('/api/session').json()['session_id']
            session = STORE.get(sid)
            original = monitoring()
            session.df = original
            response = client.post('/api/data/upload', headers={'X-Session-Id': sid},
                files={'file': ('bad.csv', b'datetime,foo\n2025-08-01 10:00:00,1\n', 'text/csv')})
            self.assertEqual(response.status_code, 400)
            self.assertIs(session.df, original)
            STORE.drop(sid)
    def test_missing_and_all_empty_measurements_are_rejected(self):
        for csv in ('datetime,foo\n2025-08-01 10:00:00,1\n',
                    'datetime,ammonia_nitrogen,dissolved_oxygen\n2025-08-01 10:00:00,,\n'):
            with self.subTest(csv=csv):
                df, report = load_data(base64.b64encode(csv.encode()).decode(), 'data.csv')
                self.assertIsNone(df)
                self.assertTrue(report)

    def test_direct_detector_rejects_data_without_evidence(self):
        df = monitoring().drop(columns=['ammonia_nitrogen', 'dissolved_oxygen'])
        result, black = find_black_spots(df)
        self.assertIn('error', json.loads(result))
        self.assertIsNone(black)

    def test_interpolation_is_not_counted_as_observed_evidence(self):
        csv = ('datetime,ammonia_nitrogen,dissolved_oxygen\n'
               '2025-08-01 10:00:00,3,4\n2025-08-01 10:00:10,,\n')
        df, _ = load_data(base64.b64encode(csv.encode()).decode(), 'data.csv')
        result, _ = find_black_spots(df)
        data = json.loads(result)
        self.assertEqual(data['有效判定点数'], 1)
        self.assertEqual(data['未判定点数'], 1)
        self.assertNotIn('所有采样点', data['提示'])

    def test_zero_dissolved_oxygen_is_valid_severe_observation(self):
        csv = 'datetime,ammonia_nitrogen,dissolved_oxygen\n2025-08-01 10:00:00,3,0\n'
        df, _ = load_data(base64.b64encode(csv.encode()).decode(), 'data.csv')
        result, black = find_black_spots(df)
        self.assertEqual(json.loads(result)['黑点总数'], 1)
        self.assertEqual(float(black.iloc[0]['dissolved_oxygen']), 0)


class ModelDependencyTests(unittest.TestCase):
    def test_reinit_invalidates_all_dependent_results(self):
        state = RiverModelState(model_initialized=True, hydro_result=hydro(),
                                wq_result=Mock(), scenarios={'old': {}})
        with patch.object(river_tools, 'get_engine_info', return_value='test'):
            river_tools.tool_river_init(state, n_cross_sections=5)
        self.assertFalse(state.has_hydro())
        self.assertFalse(state.has_wq())
        self.assertEqual(state.scenarios, {})

    def test_hydro_rerun_invalidates_old_wq_even_on_failure(self):
        state = RiverModelState(model_initialized=True, hydro_result=hydro(),
                                wq_result=Mock(), scenarios={'old': {}})
        with patch.object(river_tools, 'run_hydrodynamic', side_effect=RuntimeError('test')):
            result = json.loads(river_tools.tool_river_hydro_simulation(state))
        self.assertIn('error', result)
        self.assertFalse(state.has_hydro())
        self.assertFalse(state.has_wq())
        self.assertEqual(state.scenarios, {})

    def test_wq_with_wrong_hydro_origin_is_invalid(self):
        h = hydro()
        h.params['run_id'] = 'new'
        state = RiverModelState(hydro_result=h,
                                wq_result=SimpleNamespace(params={'hydro_run_id': 'old'}))
        self.assertFalse(state.has_wq())

    def test_do_profiles_use_lower_limit_and_target_class(self):
        state = RiverModelState(wq_result=WQResult(t=np.array([0.]),
            chainage=np.array([0., 100., 200.]), dissolved_oxygen=np.array([[.5, 1., 8.]])))
        result = json.loads(river_tools.tool_river_concentration_profile(state, 0, 'do', 'III'))
        self.assertEqual(result['标准限值_mgL'], 5.)
        self.assertEqual([p['桩号_m'] for p in result['超标断面']], [0., 100.])

    def test_cod_is_not_silently_replaced_by_cbod(self):
        state = RiverModelState(hydro_result=hydro(), wq_result=Mock())
        self.assertIn('error', json.loads(river_tools.tool_river_pollution_event(state, pollutant_type='cod')))
        self.assertIn('error', json.loads(river_tools.tool_river_concentration_profile(state, constituent='cod')))


class RiverApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.session = STORE.get(self.sid)
        self.headers = {'X-Session-Id': self.sid}
        state = self.session.get_river_state()
        state.model_initialized = True
        state.config.avg_discharge_m3s = 123
        state.config.high_water_mean_m = 2.3
        state.hydro_result = hydro()

    def tearDown(self):
        STORE.drop(self.sid)
        self.client.close()

    def test_omitted_hydro_parameters_are_left_to_current_config(self):
        with patch.object(river_tools, 'tool_river_hydro_simulation', return_value='{}') as solve:
            response = self.client.post('/api/river/hydro', headers=self.headers, json={})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('upstream_flow_m3s', solve.call_args.kwargs)
        self.assertNotIn('downstream_stage_m', solve.call_args.kwargs)

    def test_explicit_zero_values_are_preserved(self):
        with patch.object(river_tools, 'tool_river_hydro_simulation', return_value='{}') as solve:
            self.client.post('/api/river/hydro', headers=self.headers,
                             json={'upstream_flow_m3s': 0, 'downstream_stage_m': 0})
        self.assertEqual(solve.call_args.kwargs['upstream_flow_m3s'], 0)
        self.assertEqual(solve.call_args.kwargs['downstream_stage_m'], 0)
        with patch.object(river_tools, 'tool_river_pollution_event', return_value='{}') as pollute:
            response = self.client.post('/api/river/pollution', headers=self.headers,
                                        json={'chainage_m': 0, 'load_kg': 0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(pollute.call_args.kwargs['chainage_m'], 0)
        self.assertEqual(pollute.call_args.kwargs['load_kg'], 0)

    def test_invalid_numbers_and_model_failures_are_not_success(self):
        for body in ({'dt_s': 'oops'}, {'duration_h': -1}, {'dt_s': 0}):
            self.assertEqual(self.client.post('/api/river/hydro', headers=self.headers,
                                              json=body).status_code, 422)
        with patch.object(river_tools, 'tool_river_hydro_simulation', return_value='{"error":"failed"}'):
            response = self.client.post('/api/river/hydro', headers=self.headers, json={})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('结果可信度', response.json())

    def test_release_timing_is_validated_and_forwarded(self):
        with patch.object(river_tools, 'tool_river_pollution_event', return_value='{}') as solve:
            response = self.client.post('/api/river/pollution', headers=self.headers,
                json={'source_start_h': .25, 'duration_min': 7., 'output_interval_s': 30.})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(solve.call_args.kwargs['source_start_h'], .25)
        self.assertEqual(solve.call_args.kwargs['duration_min'], 7.)
        self.assertEqual(solve.call_args.kwargs['output_interval_s'], 30.)
        self.assertEqual(solve.call_args.kwargs['wq_refinement'], 4)
        for body in ({'source_start_h': -1}, {'source_start_h': '1'}, {'output_interval_s': 0}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/river/pollution', headers=self.headers,
                                                  json=body).status_code, 422)

    def test_water_quality_refinement_is_strict_and_forwarded(self):
        for value in (True, 1., '4', 3, 16):
            self.assertEqual(self.client.post('/api/river/pollution', headers=self.headers,
                json={'wq_refinement': value}).status_code, 422)
        for value in (1, 2, 4, 8):
            with patch.object(river_tools, 'tool_river_pollution_event', return_value='{}') as solve:
                response = self.client.post('/api/river/pollution', headers=self.headers, json={'wq_refinement': value})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(solve.call_args.kwargs['wq_refinement'], value)

    def test_profile_interpolates_and_rejects_outside_time_range(self):
        st = self.session.get_river_state()
        st.wq_result = WQResult(t=np.array([0., 1.]), chainage=st.hydro_result.chainage,
                               ammonia=np.array([[1., 2., 3.], [3., 4., 5.]]))
        r = self.client.get('/api/river/profile?time_h=0.25', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['时刻_h'], .25)
        self.assertEqual(r.json()['最大浓度_mgL'], 3.5)
        self.assertEqual(self.client.get('/api/river/profile?time_h=2', headers=self.headers).status_code, 400)


class AgentCompletionTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(id='regression', df=monitoring())

    @staticmethod
    def text_chunk(text):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop', delta=SimpleNamespace(content=text, tool_calls=None))])

    @staticmethod
    def tool_chunk(name, args=None):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='tool_calls', delta=SimpleNamespace(content=None,
            tool_calls=[SimpleNamespace(index=0, id=f'call-{name}',
                function=SimpleNamespace(name=name, arguments=json.dumps(args or {})))]))])

    def test_no_tool_answer_is_not_completed_analysis(self):
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value = [self.text_chunk('完成')]
            list(run_agent(self.session, '自动分析', auto=True))
        self.assertFalse(self.session.analysis_done)
        self.assertEqual(self.session.analysis_status, 'failed')

    def test_required_artifacts_and_steps_allow_success(self):
        sequence = [[self.tool_chunk(name, {'summary': 'ignored'} if name == 'generate_final_report' else {})]
                    for name in ('get_data_summary', 'find_black_spots', 'get_comprehensive_summary', 'generate_final_report')]
        sequence.append([self.text_chunk('结论')])
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI') as factory, \
                patch('backend.agent.tools.generate_report', return_value='实际报告'):
            factory.return_value.chat.completions.create.side_effect = sequence
            list(run_agent(self.session, '自动分析', auto=True))
        self.assertTrue(self.session.analysis_done)
        self.assertEqual(self.session.analysis_status, 'succeeded')

    def test_failed_retry_clears_previous_completion(self):
        self.session.analysis_done = True
        self.session.analysis_status = 'succeeded'
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI') as factory, \
                self.assertLogs('backend.agent.loop', level='ERROR'):
            factory.return_value.chat.completions.create.side_effect = RuntimeError('test failure')
            list(run_agent(self.session, '自动分析', auto=True))
        self.assertFalse(self.session.analysis_done)
        self.assertEqual(self.session.analysis_status, 'failed')

    def test_generator_close_marks_cancelled(self):
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI'):
            stream = run_agent(self.session, '自动分析', auto=True)
            next(stream)
            stream.close()
        self.assertEqual(self.session.analysis_status, 'cancelled')
        self.assertFalse(self.session.analysis_done)

    def test_plain_text_tool_failure_cannot_enter_evidence(self):
        self.session.tool_evidence['reverse_geocode'] = 'old evidence'
        with patch('backend.agent.tools.reverse_geocode', return_value='逆地理编码失败'):
            result, failed = execute_tool('reverse_geocode', {'lat': 23.12, 'lon': 113.14}, self.session)
        self.assertTrue(failed)
        self.assertFalse(json.loads(result)['ok'])
        self.assertNotIn('reverse_geocode', self.session.tool_evidence)

    def test_empty_generated_report_is_a_tool_failure(self):
        with patch('backend.agent.tools.generate_report', return_value=None):
            _, failed = execute_tool('generate_final_report', {'summary': 'ignored'},
                                     self.session, api_key='test-only')
        self.assertTrue(failed)


if __name__ == '__main__':
    unittest.main()
