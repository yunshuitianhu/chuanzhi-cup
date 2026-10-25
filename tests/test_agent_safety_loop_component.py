"""Run the real loop/progress with SDK, scientific tools and bundle I/O stubbed.

This tests completion control and citation wiring, not HTTP, LLM behavior or
scientific correctness. Isolated module names avoid replacing production imports.
"""
import ast
import importlib.util
import json
import math
from uuid import uuid4
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from water_quality_mcp.src.water_quality_mcp.rag_retrieval import compact_tool_result


def chunk(text=None, name=None, args='{}'):
    calls = [NS(index=0,id='test-call',function=NS(name=name,arguments=args))] if name else None
    return NS(choices=[NS(delta=NS(content=text,tool_calls=calls),finish_reason='tool_calls' if name else 'stop')])


def outcome(ok=True, ident='Q07_受控.md_0'):
    return json.dumps({'ok':ok,'data':{'检索结果':[{'标题':'Q07受控，非实测',
        '证据片段':[{'证据ID':ident,'原文':'2018年DO为0 mg/L。'}]}]} if ok else None,
        'error':None if ok else '模拟接口超时','skipped':False},ensure_ascii=False)


def load_loop():
    prefix = '_q07_loop_component'
    packages = {name:ModuleType(name) for name in (prefix, prefix+'.agent')}
    packages[prefix].__path__ = [str(ROOT/'backend')]
    packages[prefix+'.agent'].__path__ = [str(ROOT/'backend/agent')]
    tools = ModuleType(prefix+'.agent.tools')
    tools.SYSTEM_PROMPT_AUTO = tools.SYSTEM_PROMPT_CHAT = '受控组件测试'
    tools.TOOLS = []
    tools.execute_tool = MagicMock()
    tools.truncate = lambda name, text: compact_tool_result(text) if name=='search_treatment_cases' else text
    evidence = ModuleType(prefix+'.report_evidence')
    evidence.utc_now = lambda:'controlled-time'
    evidence.capture_tool = lambda name,args,raw,started: {'name':name,'arguments':args,'output':raw}
    evidence.build_bundle = lambda session,traces,run_id: {'tools':traces}
    codec = ModuleType(prefix+'.project_codec')
    codec._scrubber = lambda: (lambda text:text)
    sdk = ModuleType('openai')
    sdk.OpenAI = MagicMock()
    http = ModuleType('httpx')
    http.Timeout = MagicMock()
    quality = ModuleType('water_quality_mcp.src.water_quality_mcp.data_quality')
    quality.measurement_error = lambda df: None
    modules = {**packages, tools.__name__:tools, evidence.__name__:evidence,codec.__name__:codec,
        'openai':sdk,'httpx':http,quality.__name__:quality}
    with patch.dict(sys.modules,modules):
        spec = importlib.util.spec_from_file_location(prefix+'.agent.loop',ROOT/'backend/agent/loop.py')
        loop = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loop)
    return loop


class LoopCompletionComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = load_loop()

    def setUp(self):
        self.session = NS(analysis_revision=1,agent_run=None,analysis_status='idle',
            analysis_done=False,analysis_steps={},tool_evidence={},data_identity={'kind':'synthetic'},
            full_messages=[],messages=[],df=None,video_frames=[],black_spots=None)

    def run_sequence(self, sequence, tool_result=None, dispatch=None, auto=False):
        with patch.object(self.loop,'resolve_api_key',return_value='mock-only'), \
                patch.object(self.loop,'OpenAI') as factory, \
                patch.object(self.loop,'execute_tool',side_effect=dispatch,
                    return_value=(tool_result or outcome(),False)):
            factory.return_value.chat.completions.create.side_effect = sequence
            frames = list(self.loop.run_agent(self.session,'Q07受控问题',auto=auto))
            factory.return_value.close.assert_called_once()
        return frames

    def test_unknown_citation_does_not_commit_success(self):
        frames = self.run_sequence([[chunk(name='search_treatment_cases')],
            [chunk(text='结论[证据ID:伪造.md_9]')]])
        self.assertIn('event: error',frames[-1])
        self.assertFalse(any(m['role']=='assistant' for m in self.session.messages))
        self.assertFalse(any('event: done' in f for f in frames))

    def test_valid_current_citation_commits(self):
        frames = self.run_sequence([[chunk(name='search_treatment_cases')],
            [chunk(text='2018年DO为0[证据ID:Q07_受控.md_0]，同期未知。')]])
        self.assertIn('"status": "succeeded"',frames[-1])
        self.assertIn('evidence',self.session.messages[-1])

    def test_old_chat_citation_requires_current_retrieval(self):
        self.session.full_messages = [{'role':'assistant','content':'旧引用[证据ID:Q07_受控.md_0]'}]
        frames = self.run_sequence([[chunk(text='沿用[证据ID:Q07_受控.md_0]')]])
        self.assertIn('event: error',frames[-1])
        self.assertEqual(len(self.session.full_messages),1)

    def test_failed_interface_cannot_be_a_complete_chat(self):
        def dispatch(*args):
            return outcome(ok=False),True
        frames = self.run_sequence([[chunk(name='search_treatment_cases')],
            [chunk(text='接口超时，未取得证据，请稍后重试。')]],dispatch=dispatch)
        self.assertIn('"status": "partial"',frames[-1])
        self.assertTrue(self.session.messages[-1]['incomplete'])
        self.assertNotIn('evidence',self.session.messages[-1])

    def test_partial_answer_stays_visible_but_not_in_next_model_context(self):
        previous=[{'role':'user','content':'上一轮已完成的问题'},{'role':'assistant','content':'上一轮完整回答'}]
        self.session.full_messages=list(previous)
        self.run_sequence([[chunk(name='get_data_summary')],[chunk(text='未完整核验的受控回答')]],
            dispatch=lambda *a:(outcome(ok=False),True))
        self.assertEqual(self.session.agent_run.status,'partial')
        self.assertTrue(self.session.messages[-1]['incomplete'])
        self.assertEqual(self.session.full_messages,previous)

    def test_failed_auto_answer_cannot_reappear_as_verified_assistant_history(self):
        self.run_sequence([[chunk(name='get_data_summary')],[chunk(text='受控失败正文')]],
            dispatch=lambda *a:(outcome(ok=False),True),auto=True)
        self.assertEqual(self.session.agent_run.status,'failed')
        self.assertTrue(self.session.messages[-1]['incomplete'])
        self.assertEqual(self.session.full_messages,[])
        with patch.object(self.loop,'resolve_api_key',return_value='mock-only'),patch.object(self.loop,'OpenAI') as factory:
            factory.return_value.chat.completions.create.return_value=[chunk(text='重新核对当前数据')]
            list(self.loop.run_agent(self.session,'普通追问'))
            context=factory.return_value.chat.completions.create.call_args.kwargs['messages']
        self.assertFalse(any(m.get('content')=='受控失败正文' for m in context))

    def test_failed_interface_does_not_allow_data_inside_failed_wrapper(self):
        def dispatch(*args):
            return outcome().replace('"ok": true','"ok": false'),True
        frames = self.run_sequence([[chunk(name='search_treatment_cases')],
            [chunk(text='引用[证据ID:Q07_受控.md_0]')]],dispatch=dispatch)
        self.assertIn('event: error',frames[-1])

    def test_internal_report_references_join_actual_bundle(self):
        def dispatch(*args):
            self.session.tool_evidence['report_treatment_cases'] = json.dumps(json.loads(outcome())['data'])
            return json.dumps({'ok':True,'data':'历史资料[证据ID:Q07_受控.md_0]'}),False
        frames = self.run_sequence([[chunk(name='generate_final_report')],
            [chunk(text='历史资料[证据ID:Q07_受控.md_0]')]],dispatch=dispatch)
        self.assertIn('"status": "succeeded"',frames[-1])
        traces = self.session.messages[-1]['evidence']['tools']
        self.assertTrue(any(t['arguments'].get('via')=='generate_final_report' for t in traces))

    def test_model_exception_has_error_and_no_complete_answer(self):
        frames = self.run_sequence([TimeoutError('simulated timeout')])
        self.assertIn('event: error',frames[-1])
        self.assertEqual(self.session.agent_run.status,'failed')
        self.assertFalse(any(m['role']=='assistant' for m in self.session.messages))


class ToolParameterComponentTests(unittest.TestCase):
    def test_real_dispatch_rejects_invalid_top_k_before_execution(self):
        # Compile only the existing validator and exact RAG tool specification;
        # the unavailable scientific tool dependencies are outside this unit.
        tree = ast.parse((ROOT/'backend/agent/tools.py').read_text(encoding='utf-8-sig'))
        helper = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_tool')
        validator = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_dispatch_validated')
        specs = next(n.value for n in tree.body if isinstance(n,ast.AnnAssign) and getattr(n.target,'id','')=='TOOLS')
        rag = next(n for n in specs.elts if isinstance(n,ast.Call) and isinstance(n.args[0],ast.Constant)
            and n.args[0].value=='search_treatment_cases')
        ns = {'json':json,'math':math,'_execute_tool':MagicMock(return_value=('{}',False))}
        exec(compile(ast.Module(body=[helper,validator],type_ignores=[]),'<real tool validator>','exec'),ns)
        ns['TOOLS'] = [eval(compile(ast.Expression(rag),'<real RAG spec>','eval'),ns)]
        for value in (0,-1,11,True,1.5,'2'):
            raw,failed = ns['_dispatch_validated']('search_treatment_cases',{'query':'Q07','top_k':value},None)
            self.assertTrue(failed,(value,raw))
        ns['_execute_tool'].assert_not_called()
        raw,failed = ns['_dispatch_validated']('search_treatment_cases',{'query':'Q07','top_k':2},None)
        self.assertFalse(failed)


class ReferenceBundleComponentTests(unittest.TestCase):
    def build(self, cases):
        tree = ast.parse((ROOT/'backend/report_evidence.py').read_text(encoding='utf-8-sig'))
        function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_bundle')
        ns = {'_data':lambda trace:json.loads(trace['output'])['data'],
            'fingerprints':lambda session:{},'uuid4':uuid4,'utc_now':lambda:'component-time',
            'VERSION':'component-test','EVIDENCE_VERSION':'component-test'}
        exec(compile(ast.Module(body=[function],type_ignores=[]),'<real bundle function>','exec'),ns)
        session = NS(df=None,data_identity={'kind':'synthetic'},data_filename='受控.csv',target_water_class='V')
        trace = {'id':'component-trace','name':'search_treatment_cases','status':'succeeded',
            'output':json.dumps({'data':{'检索结果':cases}})}
        return ns['build_bundle'](session,[trace],'component-run')

    def test_null_cases_and_snippets_do_not_crash_completed_bundle(self):
        self.assertEqual(self.build(None)['references'],[])
        refs = self.build([{'标题':'受控','证据片段':None}])['references']
        self.assertEqual(refs[0]['evidence_ids'],[])

    def test_only_literal_string_ids_are_saved(self):
        refs = self.build([{'证据片段':[{'证据ID':None},{'证据ID':'Q07_受控.md_0'}]}])['references']
        self.assertEqual(refs[0]['evidence_ids'],['Q07_受控.md_0'])


if __name__=='__main__':
    unittest.main()
