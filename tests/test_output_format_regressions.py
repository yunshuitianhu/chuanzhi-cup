"""Local prompt-skill integration; mocked responses do not certify model aesthetics."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import pandas as pd

from backend.agent.loop import run_agent
from backend.session import Session
from water_quality_mcp.src.water_quality_mcp.agent_safety import CONTEXT_MARKER
from water_quality_mcp.src.water_quality_mcp.evidence import EVIDENCE_RULES
from water_quality_mcp.src.water_quality_mcp.output_format import (
    MARKDOWN_FORMAT_SKILL, MAX_PROMPT_CHARS, PROMPT_START, PROMPT_END, load_format_skill,
)
from water_quality_mcp.src.water_quality_mcp.report import generate_report


class FormatSkillLoaderTests(unittest.TestCase):
    def test_only_the_reviewed_prompt_block_is_loaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'SKILL.md'
            path.write_text('元信息：不得进入提示\n' + PROMPT_START + '\n只调整排版\n'
                            + PROMPT_END + '\n上游脚本：不得执行', encoding='utf-8')
            self.assertEqual(load_format_skill(path), '\n\n只调整排版')
        self.assertTrue(MARKDOWN_FORMAT_SKILL.strip())
        self.assertLessEqual(len(MARKDOWN_FORMAT_SKILL.strip()), MAX_PROMPT_CHARS)
        self.assertNotIn('BUN_X', MARKDOWN_FORMAT_SKILL)

    def test_missing_file_keeps_existing_behavior(self):
        with tempfile.TemporaryDirectory() as temporary, self.assertLogs(
                'water_quality_mcp.src.water_quality_mcp.output_format', level='WARNING'):
            self.assertEqual(load_format_skill(Path(temporary) / 'missing.md'), '')

    def test_empty_reversed_duplicated_oversized_or_non_utf8_skill_falls_back(self):
        cases = [b'no markers', (PROMPT_START + PROMPT_END).encode(),
                 (PROMPT_END + 'reversed' + PROMPT_START).encode(),
                 (PROMPT_START * 2 + 'duplicated' + PROMPT_END).encode(),
                 (PROMPT_START + 'x' * (MAX_PROMPT_CHARS + 1) + PROMPT_END).encode(), b'\xff\xfe']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'SKILL.md'
            for content in cases:
                with self.subTest(content=content[:35]), self.assertLogs(
                        'water_quality_mcp.src.water_quality_mcp.output_format', level='WARNING'):
                    path.write_bytes(content)
                    self.assertEqual(load_format_skill(path), '')


class FormatSkillRequestTests(unittest.TestCase):
    def test_chat_and_auto_send_the_skill_once_without_losing_evidence_rules(self):
        for auto in (False, True):
            with self.subTest(auto=auto):
                session = Session('format-skill-test', df=pd.DataFrame({
                    'datetime': pd.date_range('2026-01-01', periods=4, freq='10s'),
                    'ammonia_nitrogen': [.2] * 4, 'dissolved_oxygen': [0., 1., 2., 3.],
                }), data_filename='排版契约仿真.csv')
                response = NS(choices=[NS(delta=NS(content='**需要复核**，不能确认成因。',
                                                   tool_calls=None), finish_reason='stop')])
                with patch('backend.agent.loop.resolve_api_key', return_value='test-only'), \
                     patch('backend.agent.loop.OpenAI') as factory:
                    factory.return_value.chat.completions.create.side_effect = lambda **kwargs: iter([response])
                    list(run_agent(session, '解释这批读数', auto=auto))
                sent = factory.return_value.chat.completions.create.call_args.kwargs['messages'][0]['content']
                self.assertEqual(sent.count(MARKDOWN_FORMAT_SKILL), 1)
                self.assertIn(EVIDENCE_RULES, sent)
                # This controlled response does not complete the auto-analysis tool workflow.
                if auto:
                    self.assertFalse(session.analysis_done)

    def test_report_prompt_has_skill_and_preserves_body_zero_and_literal_citation(self):
        ident = '资料\\排版契约_样例.md_0'
        context = CONTEXT_MARKER + json.dumps([{'证据片段': [
            {'证据ID': ident, '原文': '受控资料中的 DO 为 0 mg/L，需复测。'}]}], ensure_ascii=False)
        body = '## 关键依据\n\n**DO 为 0 mg/L**，保留原值并复测。[证据ID:' + ident + ']'
        response = NS(choices=[NS(finish_reason='stop', message=NS(content=body))])
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases', return_value=context), \
             patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = response
            result = generate_report('test-only', '{}')
        sent = factory.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs['messages']
        self.assertIn(MARKDOWN_FORMAT_SKILL, sent[0]['content'])
        self.assertIn(EVIDENCE_RULES, sent[0]['content'])
        self.assertIn(body, result)
        self.assertTrue(result.startswith('> 数据身份：'))
        self.assertTrue(result.endswith('本次未提供物理模型计算结果；未运行不代表求解失败。'))

    def test_pretty_markdown_does_not_bypass_citation_validation(self):
        response = NS(choices=[NS(finish_reason='stop', message=NS(
            content='## 看似正式\n\n**DO 为 0 mg/L**。[证据ID:未经检索.md_0]'))])
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases', return_value=''), \
             patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = response
            with self.assertRaises(ValueError):
                generate_report('test-only', '{}')


if __name__ == '__main__':
    unittest.main()
