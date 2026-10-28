"""配置接口只返回“是否填写”，并明确尚未验证；不回显密钥值。"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app


class ConfigStateTests(unittest.TestCase):
    def test_configured_and_unconfigured_have_distinct_unverified_notices(self):
        marker = 'Q07_SYNTHETIC_CONFIG_MARKER_NOT_A_KEY'
        values = {'DEEPSEEK_API_KEY': marker}

        def fake_resolve(name):
            return values.get(name)

        with patch('backend.routers.system.resolve_api_key', side_effect=fake_resolve):
            with TestClient(app) as client:
                sid = client.get('/api/session').json()['session_id']
                response = client.get('/api/config', headers={'X-Session-Id': sid})
                self.assertEqual(response.status_code, 200)
                body = response.json()

        self.assertEqual(body['keys'], {'deepseek': True, 'amap': False, 'starcloud': False})
        state = body['key_state']
        self.assertEqual(state['deepseek']['configured'], True)
        self.assertEqual(state['deepseek']['verified'], False)
        self.assertIn('尚未验证', state['deepseek']['notice'])
        self.assertEqual(state['amap']['configured'], False)
        self.assertIn('未配置', state['amap']['notice'])
        self.assertNotIn(marker, response.text)
        self.assertTrue(all(set(item) == {'configured', 'verified', 'notice'}
                            for item in state.values()))


if __name__ == '__main__':
    unittest.main()
