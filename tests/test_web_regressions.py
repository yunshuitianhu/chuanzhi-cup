"""Web 核心链路回归：使用合成数据，不调用外部 API 或 LLM。

运行：python -m unittest discover -s tests -p test_web_regressions.py -v
"""
import sys
import unittest
import json
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE
from backend.agent.tools import execute_tool, TOOLS, SYSTEM_PROMPT_CHAT, SYSTEM_PROMPT_AUTO
from backend.agent.loop import run_agent


class WebRegressionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # 使用服务端生成并回传的会话标识。
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.session = STORE.get(self.sid)
        self.session.df = pd.DataFrame({
            'datetime': pd.to_datetime(['2025-08-01 10:00:00']),
            'ammonia_nitrogen': [3.0], 'dissolved_oxygen': [4.0],
        })
        self.session.data_filename = 'original.csv'
        self.session.messages = [{'role': 'user', 'content': '原始记录'}]
        self.session.analysis_done = True

    def tearDown(self):
        STORE.drop(self.sid)
        self.client.close()

    def upload(self, filename='new.csv'):
        return self.client.post('/api/data/upload', headers=self.headers,
                                files={'file': (filename, b'candidate', 'text/csv')})

    def test_invalid_upload_preserves_previous_session(self):
        original = self.session.df
        with patch('backend.routers.data.load_data', return_value=(None, '格式错误')):
            self.assertEqual(self.upload().status_code, 400)
        self.assertIs(self.session.df, original)
        self.assertEqual(self.session.data_filename, 'original.csv')
        self.assertTrue(self.session.analysis_done)
        self.assertEqual(len(self.session.messages), 1)
        self.assertEqual(self.session.analysis_revision, 0)

    def test_successful_upload_replaces_data_and_resets_analysis(self):
        candidate = self.session.df.copy()
        candidate['ammonia_nitrogen'] = 8.0
        with patch('backend.routers.data.load_data', return_value=(candidate, '完成')):
            self.assertEqual(self.upload().status_code, 200)
        self.assertIs(self.session.df, candidate)
        self.assertEqual(self.session.data_filename, 'new.csv')
        self.assertFalse(self.session.analysis_done)
        self.assertEqual(self.session.messages, [])
        self.assertEqual(self.session.analysis_revision, 1)

    def test_invalid_video_preserves_previous_results(self):
        with patch('backend.routers.data.extract_video', return_value=([], '视频无法解码')):
            response = self.client.post('/api/data/video', headers=self.headers,
                                       files={'file': ('bad.mp4', b'bad', 'video/mp4')})
        self.assertEqual(response.status_code, 400)
        self.assertTrue(self.session.analysis_done)

    def test_video_interval_is_validated(self):
        response = self.client.post('/api/data/video?interval=0', headers=self.headers,
                                   files={'file': ('bad.mp4', b'bad', 'video/mp4')})
        self.assertEqual(response.status_code, 422)

    def test_chart_limits_follow_all_target_classes(self):
        expected = {'I': (0.15, 7.5), 'II': (0.5, 6.0), 'III': (1.0, 5.0),
                    'IV': (1.5, 3.0), 'V': (2.0, 2.0)}
        for cls, (nh3, do) in expected.items():
            with self.subTest(cls=cls):
                self.client.post('/api/config/target-class', headers=self.headers,
                                 json={'target_water_class': cls})
                response = self.client.get('/api/charts/time-series', headers=self.headers)
                self.assertEqual(response.status_code, 200)
                chart = response.json()
                self.assertEqual(chart['target_water_class'], cls)
                panels = {p['key']: p for p in chart['panels']}
                self.assertEqual(panels['ammonia_nitrogen']['limit'], nh3)
                self.assertEqual(panels['dissolved_oxygen']['limit'], do)
                self.assertEqual(panels['dissolved_oxygen']['limit_direction'], 'minimum')

    def test_busy_session_rejects_data_replacement_and_config_change(self):
        with self.session.operation_lock:
            self.assertEqual(self.upload().status_code, 409)
            self.assertEqual(self.client.post('/api/config/target-class', headers=self.headers,
                                              json={'target_water_class': 'III'}).status_code, 409)
        self.assertTrue(self.session.analysis_done)

    def test_history_returns_only_current_session_messages(self):
        response = self.client.get('/api/agent/history', headers=self.headers)
        self.assertEqual(response.json()['messages'], self.session.messages)
        other = self.client.get('/api/agent/history')
        self.assertEqual(other.json()['messages'], [])
        STORE.drop(other.headers['X-Session-Id'])

    def test_video_tool_skips_missing_video_and_executes_loaded_video(self):
        result, failed = execute_tool('analyze_video', {}, self.session)
        self.assertFalse(failed)
        self.assertTrue(json.loads(result)['skipped'])
        self.session.video_frames = [{'time_sec': 0}]
        with patch('backend.agent.tools.analyze_video_frames', return_value='视觉线索') as analyze:
            result, failed = execute_tool('analyze_video', {}, self.session)
        analyze.assert_called_once_with(self.session.video_frames)
        self.assertFalse(failed)
        self.assertIn('不能替代', result)

    def test_river_tools_are_registered_and_errors_are_propagated(self):
        names = {t['function']['name'] for t in TOOLS}
        from backend.agent.tools import RIVER_TOOLS
        self.assertTrue(set(RIVER_TOOLS) <= names)
        result, failed = execute_tool('run_hydrodynamic_simulation', {}, self.session)
        self.assertTrue(failed)
        self.assertIn('error', json.loads(result))

    def test_river_auto_detection_receives_session_data(self):
        with patch('river_model.tools.tool_river_init', return_value='{"ok": true}') as init:
            result, failed = execute_tool('init_river_model', {'auto_detect': True}, self.session)
        self.assertFalse(failed)
        self.assertIs(init.call_args.kwargs['auto_detect_data'], self.session.df)

    def test_invalid_tool_arguments_are_rejected_before_execution(self):
        for name, args in [('analyze_video', []), ('reverse_geocode', {}),
                           ('run_hydrodynamic_simulation', {'tidal_amplitude_m': 0.8}),
                           ('run_hydrodynamic_simulation', {'dt_s': -1}),
                           ('init_river_model', {'n_cross_sections': True})]:
            with self.subTest(name=name, args=args):
                with patch('backend.agent.tools._execute_tool') as dispatch:
                    self.assertTrue(execute_tool(name, args, self.session)[1])
                    dispatch.assert_not_called()

    def test_chat_modes_replace_system_prompt_without_losing_history(self):
        self.session.full_messages = [
            {'role': 'system', 'content': SYSTEM_PROMPT_AUTO},
            {'role': 'user', 'content': '历史问题'},
            {'role': 'assistant', 'content': '历史回答'},
        ]
        chunk = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content='新回答', tool_calls=None))])
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI') as factory:
            client = factory.return_value
            client.chat.completions.create.return_value = [chunk]
            events = list(run_agent(self.session, '新问题', auto=False))
            sent = client.chat.completions.create.call_args.kwargs['messages']
            self.assertEqual(sent[0]['content'], SYSTEM_PROMPT_CHAT)
            self.assertEqual(sent[1]['content'], '历史问题')
            client.close.assert_called_once()
        self.assertIn('event: done', events[-1])

    def test_stream_keeps_session_locked_until_completion(self):
        def fake_agent(session, message, auto=False):
            self.assertTrue(session.operation_lock.locked())
            response = self.client.post('/api/config/target-class', headers=self.headers,
                                        json={'target_water_class': 'III'})
            self.assertEqual(response.status_code, 409)
            yield 'event: done\ndata: {}\n\n'

        with patch('backend.routers.agent.run_agent', side_effect=fake_agent):
            response = self.client.post('/api/agent/chat', headers=self.headers,
                                        json={'message': '测试'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.session.operation_lock.locked())

    def test_failed_tool_does_not_mark_auto_analysis_complete(self):
        tool = SimpleNamespace(index=0, id='test-call', function=SimpleNamespace(
            name='reverse_geocode', arguments='[]'))
        first = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content=None, tool_calls=[tool]))])
        final = SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(
            content='坐标参数缺失，无法完成。', tool_calls=None))])
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect = [[first], [final]]
            events = list(run_agent(self.session, '自动分析', auto=True))
        self.assertFalse(self.session.analysis_done)
        self.assertTrue(any('"ok": false' in event for event in events))
        self.assertIn('"tool_errors": 1', events[-1])

    def test_unverified_tidal_numbers_are_excluded_from_summary(self):
        state = self.session.get_river_state()
        state.hydro_result = SimpleNamespace(params={'tidal_amplitude_m': 0.8})
        state.wq_result = object()
        response = self.client.get('/api/analysis/comprehensive', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        river = response.json()['河道模型']
        self.assertIn('结论限制', river)
        self.assertNotIn('水动力模拟', river)
        self.assertNotIn('水质模拟', river)

    def test_report_uses_session_evidence_instead_of_model_supplied_numbers(self):
        with patch('backend.agent.tools.generate_report', return_value='报告') as report:
            _, failed = execute_tool('generate_final_report', {'summary': '伪造数字999999'},
                                     self.session, api_key='test-only')
        self.assertFalse(failed)
        evidence = report.call_args.args[1]
        self.assertNotIn('伪造数字999999', evidence)
        self.assertIn('数据概览', evidence)


if __name__ == '__main__':
    unittest.main()
