"""四项回答的范围与完整性；受控模型响应不是现场分析验收。"""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
import pandas as pd
from backend.agent.loop import run_agent
from backend.session import Session
from water_quality_mcp.src.water_quality_mcp.river_answer import (
    OVERVIEW_HEADINGS, OVERVIEW_PROMPT, requires_overview, missing_overview_sections)

class RiverAnswerTests(unittest.TestCase):
    def test_scope_covers_auto_first_analysis_and_river_questions(self):
        self.assertTrue(requires_overview('开始',auto=True))
        self.assertTrue(requires_overview('帮我看看',has_data=True,first_answer=True))
        for q in ['河道污染情况怎么样','这条河有哪些问题','当前污染类型和源头是什么']:
            self.assertTrue(requires_overview(q),q)
        for q in ['什么是氨氮','解释污染源头这个概念','按钮在哪里','你好']:
            self.assertFalse(requires_overview(q),q)
        self.assertFalse(requires_overview('分析这个点',has_data=True,first_answer=True,selected_context=True))

    def test_empty_heading_and_checklist_are_not_answers(self):
        self.assertEqual(len(missing_overview_sections('\n'.join(OVERVIEW_HEADINGS))),4)
        self.assertEqual(len(missing_overview_sections('## 河道污染情况\n\n')),4)
        self.assertFalse(missing_overview_sections('\n\n'.join('## '+h+'\n本轮依据尚不足以形成该项判断，需补充当前记录。' for h in OVERVIEW_HEADINGS)))

    def run_answer(self,text):
        session=Session('four-topics',df=pd.DataFrame({'datetime':pd.date_range('2026-01-01',periods=2,freq='10s'),'ammonia_nitrogen':[.2,.2],'dissolved_oxygen':[7.,7.]}))
        response=NS(choices=[NS(delta=NS(content=text,tool_calls=None),finish_reason='stop')])
        with patch('backend.agent.loop.resolve_api_key',return_value='test-only'),patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect=lambda **kw:iter([response])
            events=list(run_agent(session,'请分析当前河道污染情况'))
            prompt=factory.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
        return session,events,prompt

    def test_complete_shape_preserves_body_and_contract(self):
        body='\n\n'.join('## '+h+'\n尚需核对当前证据后给出具体结论。' for h in OVERVIEW_HEADINGS)
        session,events,prompt=self.run_answer(body)
        self.assertIn(OVERVIEW_PROMPT,prompt)
        self.assertEqual(session.messages[-1]['content'],body)
        self.assertFalse(session.messages[-1]['incomplete'])

    def test_missing_topics_are_visible_and_never_committed_as_success(self):
        session,events,prompt=self.run_answer('目前只能给出一段未完成的解释。')
        answer=session.messages[-1]
        self.assertTrue(answer['incomplete'])
        for h in OVERVIEW_HEADINGS:self.assertIn('## '+h,answer['content'])
        self.assertIn('尚未形成',answer['content'])
        self.assertFalse(session.full_messages)
        self.assertFalse(session.analysis_done)

if __name__=='__main__':unittest.main()
