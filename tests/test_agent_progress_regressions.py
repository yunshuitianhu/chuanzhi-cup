"""并发/停止/断流契约；远程模型协议为受控模拟，不代表在线模型质量。"""
import asyncio
import json
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE, Session
from backend.agent.loop import run_agent
from backend.agent.progress import AgentRun
from backend.routers.agent import start_run, AgentStreamResponse


def chunk(text=None,name=None):
    calls=[NS(index=0,id='tool',function=NS(name=name,arguments='{}'))] if name else None
    return NS(choices=[NS(delta=NS(content=text,tool_calls=calls),finish_reason='tool_calls' if name else 'stop')])


class AgentProgressTests(unittest.TestCase):
    def setUp(self):
        self.s=Session('progress',df=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=3,freq='5s'),
            'ammonia_nitrogen':[.2]*3,'dissolved_oxygen':[7.]*3}))

    def test_stop_between_tool_notice_and_execution_prevents_call(self):
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory, \
             patch('backend.agent.loop.execute_tool') as execute:
            factory.return_value.chat.completions.create.return_value=[chunk(name='find_black_spots')]
            frames=run_agent(self.s,'开始',auto=True)
            seen=[]
            for frame in frames:
                seen.append(frame)
                if frame.startswith('event: tool\n'):
                    self.assertTrue(self.s.agent_run.request_stop())
            execute.assert_not_called()
        self.assertEqual(self.s.agent_run.status,'cancelled')
        self.assertFalse(self.s.analysis_done)
        self.assertIn('"status": "cancelled"',seen[-1])

    def test_stop_during_tool_holds_data_lock_until_worker_exits(self):
        started=threading.Event(); release=threading.Event()
        self.addCleanup(release.set)
        def execute(*args,**kwargs):
            started.set()
            if not release.wait(3):raise RuntimeError('test wait timed out')
            return json.dumps({'ok':True,'data':{},'skipped':False}),False
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory, \
             patch('backend.agent.loop.execute_tool',side_effect=execute):
            factory.return_value.chat.completions.create.return_value=[chunk(name='get_data_summary')]
            run,events=start_run(self.s,'开始',True)
            self.assertTrue(started.wait(2))
            self.assertTrue(run.request_stop())
            self.assertEqual(run.snapshot()['status'],'stopping')
            self.assertTrue(run.snapshot()['busy'])
            self.assertFalse(self.s.operation_lock.acquire(blocking=False))
            release.set(); self.assertTrue(run.finished.wait(3))
        self.assertEqual(run.status,'cancelled')
        self.assertFalse(self.s.analysis_done)
        self.assertNotIn('get_data_summary',self.s.analysis_steps)
        self.assertTrue(self.s.operation_lock.acquire(blocking=False));self.s.operation_lock.release()
        self.assertFalse(any(message['role']=='assistant' for message in self.s.messages))

    def test_old_run_failure_cannot_clear_replaced_data_state(self):
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=[chunk(text='旧输出')]
            frames=run_agent(self.s,'开始',auto=True)
            for frame in frames:
                if frame.startswith('event: delta\n'):
                    self.s.analysis_revision+=1
                    self.s.analysis_done=True;self.s.analysis_status='succeeded'
                    self.s.messages=[{'role':'assistant','content':'新数据结果'}]
        self.assertTrue(self.s.analysis_done)
        self.assertEqual(self.s.analysis_status,'succeeded')
        self.assertEqual(self.s.messages,[{'role':'assistant','content':'新数据结果'}])

    def test_completed_run_cannot_be_cancelled_after_commit(self):
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=[chunk(text='完成的有效回答')]
            list(run_agent(self.s,'提问'))
        self.assertFalse(self.s.agent_run.request_stop())
        self.assertEqual(self.s.agent_run.status,'succeeded')
        self.assertFalse(self.s.messages[-1]['incomplete'])

    def test_empty_model_reply_is_not_success(self):
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=[chunk(text='')]
            frames=list(run_agent(self.s,'提问'))
        self.assertIn('event: error',frames[-1])
        self.assertEqual(self.s.agent_run.status,'failed')

    def test_disconnect_while_completed_queue_is_full_releases_lock(self):
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            # run + turn + 254 delta恰好填满队列，最终正文已提交，done事件等待写入。
            factory.return_value.chat.completions.create.return_value=[chunk(text='字') for _ in range(254)]
            run,_=start_run(self.s,'提问',False)
            deadline=time.monotonic()+3
            while run.ended is None and time.monotonic()<deadline:time.sleep(.005)
            self.assertEqual(run.status,'succeeded')
            run.close_transport()
            self.assertTrue(run.finished.wait(2))
        self.assertTrue(self.s.operation_lock.acquire(blocking=False));self.s.operation_lock.release()

    def test_transport_failure_before_body_is_consumed_signals_stop(self):
        run=AgentRun(0,managed=True)
        async def body():
            yield 'event: run\ndata: {}\n\n'
        response=AgentStreamResponse(body(),run=run)
        async def send(message):
            raise OSError('client disconnected')
        async def receive():
            return {'type':'http.disconnect'}
        with self.assertRaises(Exception):
            asyncio.run(response({'type':'http','asgi':{'spec_version':'2.4'}},receive,send))
        self.assertTrue(run.transport_closed.is_set())
        self.assertTrue(run.cancel.is_set())

    def test_progress_stop_is_available_while_session_is_exclusive(self):
        client=TestClient(app)
        sid=client.get('/api/session').json()['session_id'];headers={'X-Session-Id':sid}
        session=STORE.get(sid);session.agent_run=AgentRun(session.analysis_revision,managed=True)
        session.operation_lock.acquire()
        try:
            self.assertEqual(client.get('/api/agent/run',headers=headers).status_code,200)
            wrong=client.post('/api/agent/runs/old/stop',headers=headers)
            self.assertEqual(wrong.status_code,404)
            self.assertFalse(session.agent_run.cancel.is_set())
            response=client.post(f'/api/agent/runs/{session.agent_run.id}/stop',headers=headers)
            self.assertTrue(response.json()['stop_accepted'])
            self.assertEqual(client.post('/api/config/target-class',headers=headers,json={'target_water_class':'III'}).status_code,409)
        finally:
            session.operation_lock.release();STORE.drop(sid);client.close()

    def test_partial_steps_survive_but_stop_report_is_not_evidence(self):
        self.s.analysis_steps={'find_black_spots':'succeeded','generate_final_report':'succeeded'}
        self.s.tool_evidence={'generate_final_report':'unfinished'}
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=[chunk(text='正文')]
            for frame in run_agent(self.s,'提问'):
                if frame.startswith('event: delta\n'):self.s.agent_run.request_stop()
        self.assertEqual(self.s.analysis_steps,{'find_black_spots':'succeeded'})
        self.assertNotIn('generate_final_report',self.s.tool_evidence)

    def test_generator_initialization_failure_releases_lock_and_returns_failure(self):
        with patch('backend.routers.agent.run_agent',side_effect=RuntimeError('initialization failed')):
            run,events=start_run(self.s,'提问',True)
            self.assertTrue(run.finished.wait(2))
        self.assertEqual(run.status,'failed')
        self.assertFalse(self.s.operation_lock.locked())
        self.assertIn('event: error',events.get_nowait())


if __name__=='__main__':unittest.main()
