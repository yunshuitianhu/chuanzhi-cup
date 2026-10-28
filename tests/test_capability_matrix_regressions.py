"""N04 配置组合与失败边界的受控矩阵。

只使用假配置、临时项目和受控异常，不调用真实 DeepSeek、地图或 RAG 服务。
配置“已填写”只代表进程环境中存在值，不代表账户额度、权限或网络已验证。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE
from backend.agent.error_text import model_error_message
from water_quality_mcp.src.water_quality_mcp.config import load_env, resolve_api_key

ROOT = Path(__file__).resolve().parents[1]


class ConfigMatrixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {'WQ_PROJECT_HOME': str(Path(self.tmp.name) / 'home')})
        self.env.start()
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.session = STORE.get(self.sid)

    def tearDown(self):
        self.client.close()
        STORE.drop(self.sid)
        self.env.stop()
        self.tmp.cleanup()

    def config(self, values):
        with patch('backend.routers.system.resolve_api_key',
                   side_effect=lambda name: values.get(name)):
            response = self.client.get('/api/config', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_c01_no_keys_still_lists_and_detects_samples_without_ai(self):
        body = self.config({})
        self.assertEqual(body['keys'], {'deepseek': False, 'amap': False, 'starcloud': False})
        self.assertTrue(all(not item['configured'] and not item['verified']
                            for item in body['key_state'].values()))

        samples = self.client.get('/api/data/samples', headers=self.headers)
        self.assertEqual(samples.status_code, 200, samples.text)
        self.assertEqual(len(samples.json()['samples']), 6)

        response = self.client.post('/api/data/samples/S01/load', headers=self.headers, json={
            'project_id': None, 'analysis_revision': self.session.analysis_revision,
            'results_revision': self.session.results_revision, 'detect': True, 'river_form': None})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload['rows'], 1801)
        self.assertEqual(payload['data_identity']['kind'], 'synthetic')
        self.assertTrue(payload['detection']['ok'])
        self.assertEqual(payload['detection']['black_spots'], 0)

        series = self.client.get('/api/charts/time-series', headers=self.headers)
        self.assertEqual(series.status_code, 200, series.text)
        self.assertGreaterEqual(len(series.json()['panels']), 2)

    def test_c02_local_rag_status_and_search_do_not_require_llm_key(self):
        body = self.config({'DEEPSEEK_API_KEY': None})
        self.assertFalse(body['keys']['deepseek'])

        index = Path(self.tmp.name) / 'index'
        index.mkdir()
        (index / 'chroma.sqlite3').write_bytes(b'controlled-index')
        engine = SimpleNamespace(collection=SimpleNamespace(count=lambda: 5),
                                 _retrieval_verified=True, _retrieval_error=None)
        with patch('backend.routers.rag.INDEX_DIR', str(index)), \
                patch('backend.routers.rag.core.optional_dependencies',
                      return_value={'chromadb': True, 'sentence_transformers': True}), \
                patch('backend.routers.rag.get_rag_engine', return_value=engine):
            status = self.client.get('/api/rag/status', headers=self.headers)
            self.assertEqual(status.status_code, 200, status.text)
            state = status.json()
            self.assertTrue(state['available'])
            self.assertEqual(state['state'], 'ready')
            self.assertNotIn('index_dir', state)

            with patch('backend.routers.rag.search_treatment_cases',
                       return_value=json.dumps({'检索结果': [], '共检索到': 0, '查询': '河道'})):
                search = self.client.post('/api/rag/search', headers=self.headers,
                                          json={'query': '河道', 'top_k': 3})
        self.assertEqual(search.status_code, 200, search.text)
        self.assertIn('retrieval_id', search.json())

    def test_c03_to_c05_each_key_is_reported_independently(self):
        names = {'deepseek': 'DEEPSEEK_API_KEY', 'amap': 'AMAP_KEY', 'starcloud': 'CLOUD_TOKEN'}
        for present, env_name in names.items():
            with self.subTest(present=present):
                body = self.config({env_name: 'configured-placeholder-value'})
                expected = {name: name == present for name in names}
                self.assertEqual(body['keys'], expected)
                for name, configured in expected.items():
                    state = body['key_state'][name]
                    self.assertEqual(state['configured'], configured)
                    self.assertFalse(state['verified'])
                    self.assertIn('尚未验证' if configured else '未配置', state['notice'])

    def test_c06_configured_keys_are_not_verified_service_health(self):
        values = {'DEEPSEEK_API_KEY': 'x', 'AMAP_KEY': 'y', 'CLOUD_TOKEN': 'z'}
        body = self.config(values)
        self.assertEqual(body['keys'], {'deepseek': True, 'amap': True, 'starcloud': True})
        self.assertTrue(all(item['configured'] and not item['verified']
                            for item in body['key_state'].values()))
        self.assertTrue(all('尚未验证' in item['notice'] for item in body['key_state'].values()))

    def test_c07_placeholder_and_blank_values_are_unconfigured(self):
        with patch.dict(os.environ, {
                'DEEPSEEK_API_KEY': 'your-deepseek-key-here',
                'AMAP_KEY': '   ',
                'CLOUD_TOKEN': '<your-starcloud-token>'}):
            response = self.client.get('/api/config', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['keys'],
                         {'deepseek': False, 'amap': False, 'starcloud': False})
        self.assertNotIn('your-deepseek-key-here', response.text)
        self.assertNotIn('your-starcloud-token', response.text)

    def test_c08_env_file_is_read_only_when_explicitly_loaded(self):
        path = Path(self.tmp.name) / '.env'
        path.write_text('DEEPSEEK_API_KEY=first-controlled-value\n', encoding='utf-8')
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'seed-value'}):
            load_env(str(path))
            self.assertEqual(resolve_api_key('DEEPSEEK_API_KEY'), 'first-controlled-value')

            path.write_text('DEEPSEEK_API_KEY=second-controlled-value\n', encoding='utf-8')
            # 修改文件本身不会热更新；后端只在启动时调用 load_env()。
            self.assertEqual(resolve_api_key('DEEPSEEK_API_KEY'), 'first-controlled-value')
            load_env(str(path))
            self.assertEqual(resolve_api_key('DEEPSEEK_API_KEY'), 'second-controlled-value')


class ModelFailureTextTests(unittest.TestCase):
    def test_f01_authentication_failure_is_actionable(self):
        message = model_error_message(RuntimeError('401 Unauthorized: invalid api key'))
        self.assertIn('认证失败', message)
        self.assertIn('DEEPSEEK_API_KEY', message)

    def test_f02_quota_and_rate_limit_are_not_retried_forever(self):
        for text in ('429 too many requests', 'Insufficient Balance', '额度不足'):
            with self.subTest(text=text):
                message = model_error_message(RuntimeError(text))
                self.assertTrue('额度' in message or '频率' in message)
                self.assertIn('重试', message)

    def test_f03_permission_failure_is_distinct_from_missing_key(self):
        message = model_error_message(RuntimeError('403 Forbidden: permission denied'))
        self.assertIn('权限不足', message)
        self.assertNotIn('未配置', message)

    def test_f04_timeout_and_network_failure_are_retryable(self):
        for text in ('Connection timed out', 'network unreachable', '请求超时'):
            with self.subTest(text=text):
                message = model_error_message(RuntimeError(text))
                self.assertIn('重试', message)
                self.assertIn('保留', message)

    def test_f05_upstream_server_failure_does_not_claim_success(self):
        message = model_error_message(RuntimeError('502 Bad Gateway: server error'))
        self.assertIn('服务端暂时不可用', message)
        self.assertIn('基础示例', message)

    def test_unknown_chinese_error_is_bounded_and_does_not_echo_long_text(self):
        message = model_error_message(RuntimeError('服务异常：' + '中' * 500))
        self.assertLessEqual(len(message), 300)

    def test_key_like_value_is_not_echoed_by_generic_branch(self):
        secret = 'sk-' + 'a' * 24
        message = model_error_message(RuntimeError(secret))
        self.assertNotIn(secret, message)


if __name__ == '__main__':
    unittest.main()
