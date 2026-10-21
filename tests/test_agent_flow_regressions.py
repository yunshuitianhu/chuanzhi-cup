"""流式协议和自动分析的失败边界；模拟远程协议，不声称为在线LLM验收。"""
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from backend.session import Session
from backend.agent.loop import run_agent
from backend.agent.tools import execute_tool


def chunk(text=None, calls=None, finish='stop'):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=calls), finish_reason=finish)])


def call(name, arguments='{}', index=0, call_id='call-test'):
    return NS(index=index, id=call_id, function=NS(name=name, arguments=arguments))


class AgentFlowTests(unittest.TestCase):
    def setUp(self):
        self.s = Session(id='flow', data_filename='controlled_仿真.xlsx', df=pd.DataFrame({
            'datetime':pd.date_range('2025-01-01', periods=4, freq='5s'),
            'ammonia_nitrogen':[.2]*4, 'dissolved_oxygen':[7.]*4}))

    def run_sequence(self, sequence, auto=True, turns=15):
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
             patch('backend.agent.loop.OpenAI') as factory, \
             patch('backend.agent.loop.MAX_TURNS', turns), \
             patch('backend.agent.tools.generate_report', return_value='受控报告，证据不足需复核'):
            factory.return_value.chat.completions.create.side_effect = sequence
            frames = list(run_agent(self.s, '开始', auto=auto))
            factory.return_value.close.assert_called_once()
        return frames

    def complete_tools(self):
        return [[chunk(calls=[call(name, '{"summary":"ignored"}' if name == 'generate_final_report' else '{}')],
                       finish='tool_calls')]
                for name in ('get_data_summary', 'find_black_spots', 'get_comprehensive_summary', 'generate_final_report')]

    def test_success_requires_stop_after_required_actual_tools(self):
        frames = self.run_sequence(self.complete_tools()+[[chunk(text='已完成筛查，不能确认来源')]])
        self.assertTrue(self.s.analysis_done)
        self.assertIn('"status": "succeeded"', frames[-1])
        self.assertIsNotNone(self.s.black_spots)

    def test_truncated_final_does_not_save_as_a_complete_answer(self):
        frames = self.run_sequence(self.complete_tools()+[[chunk(text='截断半句', finish='length')]])
        self.assertFalse(self.s.analysis_done)
        self.assertEqual(self.s.analysis_status, 'failed')
        self.assertIn('event: error', frames[-1])
        self.assertFalse(any(m['role'] == 'assistant' and m['content'] == '截断半句' for m in self.s.messages))

    def test_missing_or_filtered_finish_is_not_a_normal_chat_success(self):
        for finish in (None, 'content_filter'):
            with self.subTest(finish=finish):
                frames = self.run_sequence([[chunk(text='不完整', finish=finish)]], auto=False)
                self.assertIn('event: error', frames[-1])
                self.assertFalse(any('event: done' in frame for frame in frames))

    def test_incomplete_tool_json_never_executes_a_tool(self):
        with patch('backend.agent.loop.execute_tool') as execute:
            frames = self.run_sequence([[chunk(calls=[call('find_black_spots', '{')], finish='length')]])
            execute.assert_not_called()
        self.assertIn('event: error', frames[-1])

    def test_split_tool_arguments_are_reassembled_by_index(self):
        frames = self.run_sequence([[chunk(calls=[call('get_data_summary', '{')], finish=None),
            chunk(calls=[call('', '}', call_id='')], finish='tool_calls')], [chunk(text='摘要完成')]])
        self.assertEqual(self.s.analysis_steps['get_data_summary'], 'succeeded')
        self.assertIn('"status": "partial"', frames[-1])

    def test_auto_restart_does_not_send_old_tool_or_report_context(self):
        self.s.full_messages = [{'role':'assistant','content':'旧报告999999'},
                               {'role':'tool','content':'旧参数999999','tool_call_id':'old'}]
        with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
             patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value = [chunk(text='请补齐证据')]
            list(run_agent(self.s, '重新分析', auto=True))
            sent = factory.return_value.chat.completions.create.call_args.kwargs['messages']
            self.assertNotIn('999999', json.dumps(sent, ensure_ascii=False))

    def test_loaded_video_is_a_report_prerequisite(self):
        self.s.video_frames = [{'time_sec':0.}]
        self.s.analysis_status = 'running'
        self.s.analysis_steps = {name:'succeeded' for name in
            ('get_data_summary','find_black_spots','get_comprehensive_summary')}
        with patch('backend.agent.tools.generate_report') as report:
            raw, failed = execute_tool('generate_final_report', {'summary':'ignored'}, self.s, api_key='test-only')
        self.assertTrue(failed)
        report.assert_not_called()
        self.assertIn('视频', raw)

    def test_turn_limit_summary_is_also_checked_for_truncation(self):
        frames = self.run_sequence([[chunk(calls=[call('get_data_summary')], finish='tool_calls')],
                                    [chunk(text='达到上限后的截断', finish='length')]], turns=1)
        self.assertIn('event: error', frames[-1])
        self.assertFalse(self.s.analysis_done)

    def test_generated_report_is_not_cut_at_generic_tool_limit(self):
        from backend.agent.tools import truncate
        body = json.dumps({'ok':True,'data':'受控报告正文'*600},ensure_ascii=False)
        self.assertEqual(truncate('generate_final_report',body),body)

    def test_missing_report_finish_is_rejected(self):
        from water_quality_mcp.src.water_quality_mcp.report import generate_report
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases',return_value=''), \
             patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = NS(
                choices=[NS(finish_reason=None,message=NS(content='正文'))])
            with self.assertRaisesRegex(ValueError,'未完整生成'):
                generate_report('test-only','{}')


if __name__ == '__main__':
    unittest.main()
