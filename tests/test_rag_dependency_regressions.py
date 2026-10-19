"""检索依赖不可用必须明确降级，不能伪称检索到案例。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from water_quality_mcp.src.water_quality_mcp.rag_engine import RAGEngine, search_treatment_cases


class RagDependencyTests(unittest.TestCase):
    def test_index_failure_is_not_reported_as_empty_knowledge(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = RAGEngine(folder)
            with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.get_rag_engine', return_value=engine), \
                 patch.object(engine, 'retrieve', side_effect=KeyError('_type')), \
                 self.assertLogs('water_quality_mcp.src.water_quality_mcp.rag_engine', level='WARNING'):
                result = json.loads(search_treatment_cases('黑臭水体'))
        self.assertIn('检索未完成', result['状态'])
        self.assertNotIn('检索结果', result)

    def test_missing_optional_dependency_returns_unavailable_instead_of_empty_results(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = RAGEngine(folder)
            with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.get_rag_engine', return_value=engine), \
                 patch.object(engine, 'retrieve', side_effect=ModuleNotFoundError('chromadb')):
                result = json.loads(search_treatment_cases('黑臭水体'))
        self.assertIn('依赖不可用', result['状态'])
        self.assertNotIn('检索结果', result)

    def test_successful_retrieval_keeps_the_existing_result_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = RAGEngine(folder)
            hits = [{'metadata': {'title': '参考案例'}, 'distance': .2, 'document': '案例内容'}]
            with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.get_rag_engine', return_value=engine), \
                 patch.object(engine, 'retrieve', return_value=hits):
                result = json.loads(search_treatment_cases('黑臭水体'))
        self.assertEqual(result['检索结果'][0]['标题'], '参考案例')
        self.assertEqual(result['共检索到'], 1)


if __name__ == '__main__':
    unittest.main()
