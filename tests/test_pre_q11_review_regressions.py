"""Q11前复审的故障反例；只使用受控数据，不调用真实大模型。"""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from backend.project_codec import dump_project,load_project
from backend.projects import apply_snapshot
from test_video_quality_regressions import video,monitoring
from water_quality_mcp.src.water_quality_mcp import rag_engine,report

class PreQ11BackendReviewTests(unittest.TestCase):
    def test_restoring_same_video_does_not_trap_frame_evidence_in_409(self):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id'];headers={'X-Session-Id':sid}
            try:
                s=STORE.get(sid);s.df=monitoring();s.video_frames=video()
                old=client.get('/api/video/quality',headers=headers).json()
                snapshot=load_project(dump_project(s,'受控同视频项目副本'))
                apply_snapshot(s,{'id':'0'*32,'revision':1},snapshot)
                q=client.get('/api/video/quality',headers=headers).json()
                self.assertEqual(q['summary'],old['summary'])
                self.assertNotEqual(q['revision'],old['revision'])
                before=deepcopy(s.video_frames)
                stale=client.put('/api/video/settings',headers=headers,json={'expected_revision':old['revision'],'settings':q['settings']})
                self.assertEqual(stale.status_code,409,stale.text)
                self.assertEqual(s.video_frames,before)
                response=client.get('/api/video/frames/0',params={'expected_revision':q['revision']},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
            finally:STORE.drop(sid)

    def test_identical_frame_list_replacement_rebuilds_cached_frame_evidence(self):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id'];headers={'X-Session-Id':sid}
            try:
                session=STORE.get(sid);session.video_frames=video()
                q=client.get('/api/video/quality',headers=headers).json()
                session.video_frames=deepcopy(session.video_frames)
                response=client.get('/api/video/frames/0',params={'expected_revision':q['revision']},headers=headers)
                self.assertEqual(response.status_code,200,response.text)
            finally:STORE.drop(sid)

    def test_rag_initialization_storage_failure_degrades_like_query_failure(self):
        with patch.object(rag_engine,'get_rag_engine',side_effect=PermissionError('controlled denied')):
            result=json.loads(rag_engine.search_treatment_cases('受控检索'))
        self.assertIn('检索未完成',result['状态']);self.assertNotIn('检索结果',result)

    def test_rag_http_initialization_failure_is_503_instead_of_500(self):
        with patch.object(rag_engine,'get_rag_engine',side_effect=OSError('controlled storage failure')):
            with TestClient(app,raise_server_exceptions=False) as client:
                response=client.post('/api/rag/search',json={'query':'受控检索'})
                STORE.drop(response.headers.get('X-Session-Id'))
        self.assertEqual(response.status_code,503,response.text)

    def test_report_model_header_matches_actual_summary_scope(self):
        response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='依据不足，需要复核。'))])
        cases=[({},'本次未提供物理模型计算结果'),
            ({'水动力模拟':{'参数':{'tidal_amplitude_m':0}},'水质模拟':{'污染物':'ammonia'}},'一维恒定流情景'),
            ({'结论限制':'水质模拟来源于未验收潮汐工况，已排除其数值。'},'已排除')]
        for model,label in cases:
            with self.subTest(label=label),patch.object(report,'_try_retrieve_treatment_cases',return_value=''),patch.object(report,'OpenAI') as factory:
                factory.return_value.__enter__.return_value.chat.completions.create.return_value=response
                value=report.generate_report('test-only',json.dumps({'河道模型':model},ensure_ascii=False))
                self.assertIn(label,value)
                if not model:self.assertNotIn('潮汐',value)
