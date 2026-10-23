"""Real backend/tool/report wiring with mocked remote model and RAG boundaries."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.session import Session
from backend.agent.tools import execute_tool
from backend.agent.loop import run_agent
from water_quality_mcp.src.water_quality_mcp.agent_safety import CONTEXT_MARKER
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE


def current_cases():
    return [{'标题':'Q07受控参考（非实测）','来源文件':'Q07_current.md',
        '证据片段':[{'证据ID':'Q07_current.md_0','原文':'2018年DO为0 mg/L。'}]}]


def chunk(text=None,name=None):
    calls=[NS(index=0,id='q07-native-component',function=NS(name=name,arguments='{"summary":"ignored"}'))] if name else None
    return NS(choices=[NS(delta=NS(content=text,tool_calls=calls),finish_reason='tool_calls' if name else 'stop')])


class SafetyBackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.s=Session('q07-integration',data_filename='Q07_受控仿真.csv',df=pd.DataFrame({
            'datetime':pd.date_range('2026-10-23',periods=3,freq='5s'),
            'ammonia_nitrogen':[.2]*3,'dissolved_oxygen':[7.]*3}))
        self.s.tool_evidence['search_treatment_cases']=json.dumps({'检索结果':[{
            '标题':'STALE_CASE_POISON','证据片段':[{'证据ID':'old.md_0','原文':'旧轮次资料'}]}]})

    def test_report_prompt_does_not_mix_old_search_with_its_current_retrieval(self):
        context='规则'+CONTEXT_MARKER+json.dumps(current_cases(),ensure_ascii=False)
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases',return_value=context), \
                patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            model=factory.return_value.__enter__.return_value.chat.completions.create
            model.return_value=NS(choices=[NS(finish_reason='stop',message=NS(
                content='历史资料[证据ID:Q07_current.md_0]，不代表当前河段实测。'))])
            raw,failed=execute_tool('generate_final_report',{'summary':'ignored'},self.s,api_key='mock-only')
            self.assertFalse(failed,raw)
            sent=model.call_args.kwargs['messages']
            self.assertNotIn('STALE_CASE_POISON',json.dumps(sent,ensure_ascii=False))
            refs=json.loads(self.s.tool_evidence['report_treatment_cases'])
            self.assertEqual(refs['检索结果'],current_cases())

    def test_real_loop_saves_internal_report_reference_in_current_evidence(self):
        context='规则'+CONTEXT_MARKER+json.dumps(current_cases(),ensure_ascii=False)
        text='历史资料[证据ID:Q07_current.md_0]，不代表当前河段实测。'
        with patch('backend.agent.loop.resolve_api_key',return_value='mock-only'), \
                patch('backend.agent.loop.OpenAI') as outer, \
                patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases',return_value=context), \
                patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as inner:
            outer.return_value.chat.completions.create.side_effect=[[chunk(name='generate_final_report')],[chunk(text=text)]]
            inner.return_value.__enter__.return_value.chat.completions.create.return_value=NS(
                choices=[NS(finish_reason='stop',message=NS(content=text))])
            frames=list(run_agent(self.s,'Q07受控报告'))
        self.assertIn('"status": "succeeded"',frames[-1])
        evidence=self.s.messages[-1]['evidence']
        self.assertEqual(evidence['references'][0]['evidence_ids'],['Q07_current.md_0'])
        self.assertTrue(any(t['name']=='search_treatment_cases' for t in evidence['tools']))

    def test_invalid_rag_parameter_removes_old_success_evidence(self):
        raw,failed=execute_tool('search_treatment_cases',{'query':'Q07','top_k':0},self.s)
        self.assertTrue(failed)
        self.assertFalse(json.loads(raw)['ok'])
        self.assertNotIn('search_treatment_cases',self.s.tool_evidence)

    def http_run(self,answer,failed=False):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id']
            headers={'X-Session-Id':sid}
            try:
                STORE.get(sid).df=self.s.df.copy()
                STORE.get(sid).data_filename=self.s.data_filename
                first=chunk(name='search_treatment_cases')
                first.choices[0].delta.tool_calls[0].function.arguments='{"query":"Q07"}'
                with patch('backend.agent.loop.resolve_api_key',return_value='mock-only'), \
                        patch('backend.agent.loop.OpenAI') as model, \
                        patch('backend.agent.tools.search_treatment_cases',
                            return_value=json.dumps({'error':'受控接口超时'} if failed else {'检索结果':current_cases()})):
                    model.return_value.chat.completions.create.side_effect=[[first],[chunk(text=answer)]]
                    response=client.post('/api/agent/chat',headers=headers,json={'message':'Q07受控测试','auto':False})
                self.assertEqual(response.status_code,200)
                history=client.get('/api/agent/history',headers=headers).json()['messages']
                return response.text,history
            finally:
                STORE.drop(sid)

    def test_http_sse_unknown_citation_has_error_and_no_complete_history(self):
        raw,history=self.http_run('错误引用[证据ID:不存在.md_9]')
        self.assertIn('event: error',raw)
        self.assertFalse(any(m['role']=='assistant' for m in history))
        self.assertTrue(any(m['role']=='error' and '引用未通过核对' in m['content'] for m in history))

    def test_http_sse_failed_interface_is_partial_in_saved_history(self):
        raw,history=self.http_run('本轮接口超时，未获得证据，不能确认。',failed=True)
        self.assertIn('"status": "partial"',raw)
        answer=next(m for m in reversed(history) if m['role']=='assistant')
        self.assertTrue(answer['incomplete'])
        self.assertNotIn('evidence',answer)

    def test_quality_spike_does_not_imply_pollution_alert(self):
        self.s.df=pd.DataFrame({'datetime':pd.date_range('2026-10-23',periods=7,freq='5s'),
            'ammonia_nitrogen':[.2,.2,.2,1.3,.2,.2,.2],'dissolved_oxygen':[7.]*7})
        raw,failed=execute_tool('find_black_spots',{},self.s)
        self.assertFalse(failed,raw)
        raw,failed=execute_tool('get_comprehensive_summary',{},self.s)
        summary=json.loads(raw)['data']
        self.assertGreater(summary['数据质量']['需复核记录数'],0)
        self.assertEqual(summary['黑臭检测']['黑点总数'],0)

    def test_conflicting_same_time_readings_preserve_zero_alert(self):
        self.s.df=pd.DataFrame({'datetime':pd.to_datetime(['2026-10-23 09:00:00']*2),
            'ammonia_nitrogen':[.2,.2],'dissolved_oxygen':[0.,7.]})
        raw,failed=execute_tool('find_black_spots',{},self.s)
        self.assertFalse(failed,raw)
        self.assertEqual(json.loads(raw)['data']['黑点总数'],1)
        self.assertEqual(self.s.black_spots['dissolved_oxygen'].iloc[0],0.)

    def test_failure_reason_redacts_configured_secret_before_stream_log_and_history(self):
        marker='Q07_SYNTHETIC_SECRET_CANARY_ONLY'
        with patch.dict(os.environ,{'CLOUD_TOKEN':marker}), \
                patch('backend.agent.loop.resolve_api_key',return_value='mock-only'), \
                patch('backend.agent.loop.OpenAI') as factory, \
                self.assertLogs('backend.agent.loop',level='ERROR') as logs:
            factory.return_value.chat.completions.create.side_effect=RuntimeError('模拟错误 '+marker)
            frames=list(run_agent(self.s,'Q07错误脱敏检查'))
        self.assertNotIn(marker,''.join(frames))
        self.assertNotIn(marker,json.dumps(self.s.messages))
        self.assertNotIn(marker,' '.join(logs.output))
        self.assertIn('已移除密钥',self.s.messages[-1]['content'])


if __name__=='__main__':
    unittest.main()
