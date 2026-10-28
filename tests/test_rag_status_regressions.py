"""RAG 状态：阶段可区分、缺依赖/缺索引不冒充不可用原因，且不暴露本机路径。"""
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.main import app


class RagStatusTests(unittest.TestCase):
    def status(self, dependencies, index_dir):
        with patch('backend.routers.rag.INDEX_DIR', index_dir), \
                patch('backend.routers.rag.core.optional_dependencies', return_value=dependencies):
            with TestClient(app) as client:
                sid = client.get('/api/session').json()['session_id']
                response = client.get('/api/rag/status', headers={'X-Session-Id': sid})
                self.assertEqual(response.status_code, 200)
                return response.json()

    def test_missing_dependencies_name_the_stage(self):
        with tempfile.TemporaryDirectory() as folder:
            body = self.status({'chromadb': False, 'sentence_transformers': False}, folder)
        self.assertEqual(body['state'], 'dependency_missing')
        self.assertEqual(body['stage'], 'dependencies')
        self.assertEqual(body['missing_dependencies'], ['chromadb', 'sentence_transformers'])
        self.assertIn('依赖缺失', body['notice'])
        self.assertNotIn('index_dir', body)

    def test_missing_index_names_build_stage_not_dependency(self):
        with tempfile.TemporaryDirectory() as folder:
            body = self.status({'chromadb': True, 'sentence_transformers': True}, folder)
        self.assertEqual(body['state'], 'index_missing')
        self.assertEqual(body['stage'], 'index_build')
        self.assertFalse(body['has_index'])
        self.assertNotIn('index_dir', body)


if __name__ == '__main__':
    unittest.main()
