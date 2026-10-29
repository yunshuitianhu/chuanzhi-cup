"""企业预测的确定性排序与真实调用链；全部使用受控虚构名称，不作现场归因。"""
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import Session, STORE
from backend.agent.tools import execute_tool, truncate
from backend.source_prediction import attach_prediction, current_prediction
from water_quality_mcp.src.water_quality_mcp.source_prediction import predict_sources, prediction_text, wants_prediction
from water_quality_mcp.src.water_quality_mcp.trace_engine import _deduplicate_and_rank
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig


def rows():
    return [{'名称': '受控甲公司', '数据源': '高德POI', 'distance': '100', '主要污染物': '氨氮'},
            {'名称': '受控乙公司', '数据源': '高德POI', 'distance': '300'}]


class SourcePredictionTests(unittest.TestCase):
    def test_ranking_returns_named_top_and_explains_non_probability_score(self):
        result = predict_sources(rows(), radius_m=500, pollutants=['氨氮'])
        self.assertEqual(result['首选企业']['名称'], '受控甲公司')
        self.assertEqual(result['首选企业']['排序分'], 77.)
        self.assertEqual(result['首选企业']['匹配指标'], ['氨氮'])
        self.assertIn('不是概率', result['排序说明'])
        self.assertIn('预测首选企业：受控甲公司', prediction_text(result))
        self.assertIn('受控乙公司', prediction_text(result))

    def test_rejects_unlocated_national_permits_outside_radius_stations_and_outlets(self):
        items = [{'名称':'外省受控公司','数据源':'排污许可证'},
                 {'名称':'远处受控公司','distance':501},
                 {'名称':'受控站点','数据源':'国控站点','distance':0},
                 {'名称':'受控排污口','distance':10},
                 {'名称':'工厂宿舍','distance':10}]
        result = predict_sources(items)
        self.assertIsNone(result['首选企业'])
        self.assertIn('排查', result['预测方向'])

    def test_zero_distance_units_nonfinite_and_coordinate_fallback(self):
        items = [{'名称':'受控甲公司','distance':'0m'}, {'名称':'受控乙公司','距离':'0.2公里'},
                 {'名称':'受控丙公司','distance':float('nan')},
                 {'名称':'受控丁公司','lat':30.,'lon':120.}]
        result = predict_sources(items, center=[30.,120.])
        self.assertEqual(len(result['候选企业']), 3)
        self.assertEqual(result['首选企业']['距离_m'], 0.)
        self.assertEqual(set(result['同分候选']), {'受控甲公司','受控丁公司'})

    def test_same_name_merges_actual_sources_and_preserves_spatial_information(self):
        merged = _deduplicate_and_rank([
            {'name':'受控甲公司','distance':'10','数据源':'高德POI','置信度权重':.25},
            {'企业名称':'受控甲公司','主要污染物':'氨氮','数据源':'排污许可证','置信度权重':.35}], AnalysisConfig())
        result = predict_sources(merged, pollutants=['氨氮'])
        self.assertEqual(len(result['候选企业']),1)
        self.assertEqual(len(result['首选企业']['数据来源']),2)
        self.assertEqual(result['首选企业']['距离_m'],10.)
        self.assertIn('同一经营主体', ' '.join(result['首选企业']['缺口']))

    def test_synthetic_data_never_names_real_enterprises(self):
        result = predict_sources(rows(), identity={'kind':'synthetic'})
        self.assertEqual(result['状态'],'synthetic')
        self.assertIsNone(result['首选企业'])
        self.assertNotIn('受控甲公司', prediction_text(result))

    def test_current_scope_does_not_reuse_old_prediction(self):
        s = Session('prediction')
        data = attach_prediction(s, {'综合污染源列表':rows()})
        s.tool_evidence['search_nearby_pollution_sources'] = json.dumps(data)
        self.assertIsNotNone(current_prediction(s))
        s.results_revision += 1
        self.assertIsNone(current_prediction(s))

    def test_agent_tool_uses_valid_track_and_keeps_prediction_in_model_context(self):
        s = Session('prediction', df=pd.DataFrame({'latitude':[30.], 'longitude':[120.], 'ammonia_nitrogen':[8.]}))
        raw = {'综合污染源列表':rows(), '搜索半径_m':500, '国控监测背景':{'长背景':'x'*6000}}
        with patch('backend.agent.tools.multi_source_trace',return_value=json.dumps(raw)):
            output, failed = execute_tool('search_nearby_pollution_sources',{},s)
        self.assertFalse(failed)
        result = json.loads(truncate('search_nearby_pollution_sources',output))
        self.assertEqual(result['data']['污染源预测']['首选企业']['名称'],'受控甲公司')
        self.assertIsNotNone(current_prediction(s))
        self.assertNotIn('国控监测背景',result['data'])

    def test_api_returns_and_remembers_top_enterprise_without_llm(self):
        with TestClient(app) as client:
            sid = client.get('/api/session').json()['session_id']
            try:
                with patch('backend.routers.trace.multi_source_trace',return_value={'综合污染源列表':rows()}):
                    response = client.post('/api/trace/multi-source',headers={'X-Session-Id':sid},
                        json={'lat':30.,'lon':120.,'radius_m':500})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['污染源预测']['首选企业']['名称'],'受控甲公司')
                self.assertIsNotNone(current_prediction(STORE.get(sid)))
            finally:
                STORE.drop(sid)

    def test_intent_separates_definition_from_source_question(self):
        self.assertTrue(wants_prediction('预测污染源是哪家企业'))
        self.assertFalse(wants_prediction('什么是污染源'))

    def test_new_prediction_invalidates_previous_report_and_binds_new_scope(self):
        from backend.source_prediction import remember_prediction
        s = Session('new-prediction', analysis_done=True, analysis_status='succeeded',
                    comprehensive_summary='old-summary')
        remember_prediction(s, {'综合污染源列表':rows()})
        self.assertFalse(s.analysis_done)
        self.assertEqual(s.analysis_status, 'stale')
        self.assertEqual(s.results_revision, 1)
        self.assertIsNotNone(current_prediction(s))

    def test_invalid_agent_coordinates_do_not_start_external_lookup(self):
        for args in ({'lat':float('nan'),'lon':120.}, {'lat':91.,'lon':120.}, {'lat':True,'lon':120.}):
            with patch('backend.agent.tools.multi_source_trace') as search:
                _, failed = execute_tool('search_nearby_pollution_sources',args,Session('invalid'))
            self.assertTrue(failed)
            search.assert_not_called()

    def test_main_chat_retrieves_and_outputs_prediction_before_hedging_model(self):
        from backend.agent.loop import run_agent
        from test_agent_flow_regressions import chunk
        s = Session('predict-main', df=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=2,freq='s'),
            'latitude':[30.,30.], 'longitude':[120.,120.], 'ammonia_nitrogen':[8.,8.], 'dissolved_oxygen':[1.,1.]}))
        with patch('backend.agent.loop.resolve_api_key',return_value='controlled-key'), \
             patch('backend.agent.loop.OpenAI') as factory, \
             patch('backend.agent.tools.multi_source_trace',return_value=json.dumps({'综合污染源列表':rows()})) as search:
            factory.return_value.chat.completions.create.return_value = [chunk(text='具体排放情况仍待核对。')]
            events = list(run_agent(s,'污染源是哪家企业？给出预测',auto=False))
        search.assert_called_once()
        self.assertIn('event: done',events[-1])
        self.assertTrue(s.messages[-1]['content'].startswith('预测首选企业：受控甲公司'))
        sent = factory.return_value.chat.completions.create.call_args.kwargs['messages']
        self.assertIn('污染源预测',json.dumps(sent,ensure_ascii=False))

    def test_side_quote_question_uses_project_prediction_and_preserves_main_report(self):
        import test_text_questions_regressions as fixture
        f = fixture.TextQuestionTests()
        f.setUp()
        try:
            thread = f.thread()
            original = list(f.s.messages)
            prediction = attach_prediction(f.s,{'综合污染源列表':rows()})
            f.s.tool_evidence['search_nearby_pollution_sources'] = json.dumps(prediction)
            with patch('backend.side_chat.resolve_api_key',return_value='controlled-key'), \
                 patch('backend.side_chat.OpenAI') as factory:
                factory.return_value.chat.completions.create.return_value = iter([fixture.chunk('请核对入河排口。')])
                response = f.client.post('/api/side/threads/'+thread['id']+'/chat',headers=f.h,
                    json={'message':'污染源是哪家企业？','request_id':'66666666-6666-4666-8666-666666666666'})
            self.assertIn('event: done',response.text)
            self.assertTrue(f.s.side_threads[0]['messages'][-1]['content'].startswith('预测首选企业：受控甲公司'))
            self.assertEqual(f.s.messages,original)
            self.assertNotIn('tools',factory.return_value.chat.completions.create.call_args.kwargs)
        finally:
            f.tearDown()


if __name__ == '__main__':
    unittest.main()
