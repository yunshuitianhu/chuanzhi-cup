"""报告快照与原始记录关联；受控数据用于契约验收，不代表现场准确率。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import Session, STORE
from backend.agent.loop import run_agent
from backend.agent.tools import execute_tool
from backend.project_codec import dump_project, load_project, STATE_FIELDS
from backend.report_evidence import (build_bundle, capture_tool, utc_now, bundle_status,
    public_bundle, fact_records, fingerprints, TOOL_LIMIT)
from river_model.state import RiverModelState, HydroResult


def sample_session():
    df = pd.DataFrame({'datetime': pd.date_range('2026-10-22', periods=6, freq='1min'),
        '__input_record_id': range(1, 7), 'latitude': [30, 30, 30, np.nan, 30, 30],
        'longitude': [120]*6, '__observed_coordinates': [True, False, True, True, True, True],
        'ammonia_nitrogen': [.2, 99., 5., 5., np.nan, .2],
        '__observed_ammonia_nitrogen': [True, False, True, True, False, True],
        'dissolved_oxygen': [7., 2., 0., 1., np.nan, 7.],
        '__observed_dissolved_oxygen': [True, False, True, True, False, True]}, index=range(10, 16))
    return Session('evidence-test', df=df, data_filename='仿真_契约样本.csv')


def make_bundle(session, tools=('get_data_summary', 'find_black_spots')):
    traces = []
    for name in tools:
        started = utc_now()
        raw, failed = execute_tool(name, {}, session)
        assert not failed, raw
        traces.append(capture_tool(name, {}, raw, started))
    bundle = build_bundle(session, traces, 'controlled-test')
    session.messages.append({'role': 'assistant', 'content': '受控回答', 'evidence': bundle})
    return bundle


class ReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.s = sample_session()

    def test_mean_and_extreme_use_original_observations_and_preserve_zero(self):
        b = make_bundle(self.s)
        mean = fact_records(self.s, b, 'ammonia_nitrogen.mean', 0, 20)
        self.assertEqual(mean['fact']['value'], 2.6)
        self.assertEqual([r['position'] for r in mean['rows']], [0, 2, 3, 5])
        maximum = fact_records(self.s, b, 'ammonia_nitrogen.max', 0, 20)
        self.assertEqual([r['position'] for r in maximum['rows']], [2, 3])
        minimum = fact_records(self.s, b, 'dissolved_oxygen.min', 0, 20)
        self.assertEqual(minimum['fact']['value'], 0)
        self.assertEqual(minimum['rows'][0]['values']['dissolved_oxygen'], 0)
        self.assertEqual(minimum['rows'][0]['map_sample_id'], 2)
        self.assertEqual(minimum['rows'][0]['input_record_id'], 3)
        self.assertEqual(bundle_status(self.s, b)[0], 'current')

    def test_missing_and_interpolated_gps_do_not_get_map_links(self):
        b = make_bundle(self.s)
        rows = fact_records(self.s, b, 'records', 0, 20)['rows']
        self.assertIsNone(rows[1]['map_sample_id'])
        self.assertIsNone(rows[3]['coord'])
        self.assertIsNone(rows[1]['values']['ammonia_nitrogen'])
        self.assertEqual(rows[1]['interpolated']['ammonia_nitrogen'], 99.)
        gps = fact_records(self.s, b, 'gps', 0, 20)
        self.assertEqual(gps['total'], 4)

    def test_rounding_does_not_add_near_extreme_rows(self):
        self.s.df.loc[12, 'ammonia_nitrogen'] = 5.004
        self.s.df.loc[13, 'ammonia_nitrogen'] = 5.003
        b = make_bundle(self.s)
        result = fact_records(self.s, b, 'ammonia_nitrogen.max', 0, 20)
        self.assertEqual(result['fact']['value'], 5.)
        self.assertEqual([r['position'] for r in result['rows']], [2])

    def test_detection_links_by_original_id_even_with_duplicate_dataframe_index(self):
        self.s.df.index = [0]*6
        b = make_bundle(self.s)
        rows = fact_records(self.s, b, 'alerts', 0, 20)
        self.assertEqual(rows['total'], len(self.s.black_spots))
        self.assertEqual([r['position'] for r in rows['rows']], [2, 3])
        self.s.df.drop(columns='__input_record_id', inplace=True)
        b = make_bundle(self.s)
        fact = next(f for f in b['facts'] if f['id'] == 'alerts')
        self.assertIsNone(fact['record_count'])
        with self.assertRaisesRegex(ValueError, '唯一记录'):
            fact_records(self.s, b, 'alerts', 0, 20)

    def test_data_config_result_and_video_changes_make_evidence_stale(self):
        def replace_values(s):
            s.df['ammonia_nitrogen'] = [.3]*6
        for mutate in [replace_values,
                lambda s: setattr(s, 'target_water_class', 'III'),
                lambda s: setattr(s, 'cross_validation', {'new': True}),
                lambda s: setattr(s, 'video_filename', 'replacement.mp4')]:
            s = sample_session(); b = make_bundle(s); mutate(s)
            self.assertEqual(bundle_status(s, b)[0], 'stale')

    def test_model_change_stales_evidence_but_output_relocation_does_not(self):
        self.s.river_state = RiverModelState()
        b = make_bundle(self.s)
        self.s.river_state.output_dir = 'another-machine/runtime'
        self.assertEqual(bundle_status(self.s, b)[0], 'current')
        self.s.river_state.geometry_revision += 1
        self.assertEqual(bundle_status(self.s, b)[0], 'stale')

    def test_real_artifact_roundtrip_preserves_current_evidence(self):
        from backend.projects import apply_snapshot
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary);source=directory/'source';source.mkdir()
            output=source/'river_hydro_profile.png';output.write_bytes(b'controlled-artifact')
            self.s.river_state=RiverModelState(output_dir=str(source))
            self.s.river_ui={'hydro':{'输出文件':[str(output)],'文件生成说明':[]}}
            b=make_bundle(self.s)
            self.assertEqual(bundle_status(self.s,b)[0],'current')
            snapshot=load_project(dump_project(self.s,'受控附件迁移'))
            destination=directory/'restored';destination.mkdir()
            restored=Session('another-session')
            with patch('backend.projects.runtime_directory',return_value=str(destination)):
                apply_snapshot(restored,{'id':'1'*32,'revision':1},snapshot)
            self.assertTrue(Path(restored.river_ui['hydro']['输出文件'][0]).is_file())
            self.assertEqual(bundle_status(restored,restored.messages[-1]['evidence'])[0],'current')
            restored.river_ui['alignment']={'confirmed':True,'reference_path':[[30,120],[30,120.01]]}
            self.assertEqual(bundle_status(restored,restored.messages[-1]['evidence'])[0],'stale')

    def test_old_fingerprint_format_is_explicitly_stale(self):
        b=make_bundle(self.s);b['version']='report-evidence-v1'
        status,notice=bundle_status(self.s,b)
        self.assertEqual(status,'stale');self.assertIn('格式',notice)

    def test_project_round_trip_preserves_snapshot_without_runtime_revision_dependency(self):
        self.s.river_state = RiverModelState()
        values = np.array([[1., 2.]])
        self.s.river_state.hydro_result = HydroResult(t=np.array([0.]), chainage=np.array([0., 100.]),
            water_level=values.copy(), discharge=values.copy(), velocity=values.copy(),
            area=values.copy(), params={'run_id': 'controlled-array-serialization'})
        b = make_bundle(self.s)
        raw = dump_project(self.s, '仿真证据迁移')
        restored = Session('another-session')
        snapshot = load_project(raw)
        for key in STATE_FIELDS:
            setattr(restored, key, snapshot.state[key])
        restored.analysis_revision += 100
        restored.river_state.output_dir = 'moved/runtime'
        self.assertEqual(fingerprints(restored), fingerprints(self.s))
        self.assertEqual(bundle_status(restored, restored.messages[-1]['evidence'])[0], 'current')
        self.assertEqual(restored.messages[-1]['evidence']['id'], b['id'])
        restored.river_state.hydro_result.water_level[0, 0] += .1
        self.assertEqual(bundle_status(restored, restored.messages[-1]['evidence'])[0], 'stale')

    def test_no_fresh_summary_does_not_backfill_facts_and_failed_tools_stay_failed(self):
        self.assertIsNone(build_bundle(self.s, [], 'no-tools'))
        trace = capture_tool('get_data_summary', {}, json.dumps({'ok': False, 'data': None,
            'error': '受控失败', 'skipped': False}), utc_now())
        b = build_bundle(self.s, [trace], 'failed-summary')
        self.assertEqual(b['facts'], [])
        self.assertEqual(b['tools'][0]['status'], 'failed')
        long = capture_tool('get_data_summary', {}, json.dumps({'ok': True, 'data': 'x'*TOOL_LIMIT}), utc_now())
        self.assertTrue(long['truncated'])
        self.assertEqual(build_bundle(self.s, [long], 'long')['facts'], [])

    def test_tampered_fact_or_tool_output_is_rejected_and_case_urls_not_invented(self):
        b = make_bundle(self.s)
        changed = deepcopy(b); changed['facts'][0]['value'] += 1
        self.assertEqual(bundle_status(self.s, changed)[0], 'stale')
        changed = deepcopy(b); changed['tools'][0]['output'] = '{}'
        self.assertEqual(bundle_status(self.s, changed)[0], 'stale')
        changed = deepcopy(b); changed['facts'][0]['tool_id'] = []
        self.assertEqual(public_bundle(self.s, changed)['status'], 'stale')
        trace = capture_tool('search_treatment_cases', {'query': '受控查询'}, json.dumps({'ok': True,
            'data': {'检索结果': [{'标题': '<script>测试</script>', '地点': '受控地点'}]}}), utc_now())
        b = build_bundle(self.s, [trace], 'rag-controlled')
        self.assertNotIn('url', b['references'][0])
        self.assertIn('未提供', b['references'][0]['notice'])

    def test_http_session_isolation_paging_stale_rejection_and_busy_lock(self):
        client = TestClient(app)
        sid = client.get('/api/session').json()['session_id']
        s = STORE.get(sid);s.df=self.s.df;s.data_filename=self.s.data_filename
        b = make_bundle(s);headers={'X-Session-Id': sid};url='/api/agent/evidence/'+b['id']
        self.assertEqual(client.get(url).status_code, 404)
        self.assertEqual(client.get(url, headers=headers).json()['status'], 'current')
        rows = client.get(url+'/facts/records/rows?offset=2&limit=2', headers=headers).json()
        self.assertEqual([r['position'] for r in rows['rows']], [2, 3])
        self.assertEqual(client.get(url+'/facts/records/rows?limit=1000', headers=headers).status_code, 422)
        self.assertEqual(client.get(url+'/facts/foreign/rows', headers=headers).status_code, 404)
        self.assertEqual(client.get(url+'/records/2', headers=headers).json()['map_sample_id'], 2)
        self.assertEqual(client.get(url+'/records/-1', headers=headers).status_code, 404)
        history = client.get('/api/agent/history', headers=headers).json()['messages'][-1]
        self.assertEqual(history['evidence'], {'id': b['id']})
        saved_tools=b['tools']; b['tools']=None
        self.assertEqual(client.get(url, headers=headers).json()['status'], 'stale')
        self.assertEqual(client.get(url+'/tools/'+saved_tools[0]['id'], headers=headers).status_code, 404)
        b['tools']=saved_tools
        s.operation_lock.acquire()
        try:
            self.assertEqual(client.get(url, headers=headers).status_code, 409)
        finally:
            s.operation_lock.release()
        s.df.iloc[0, s.df.columns.get_loc('ammonia_nitrogen')] = .4
        self.assertEqual(client.get(url, headers=headers).json()['status'], 'stale')
        self.assertEqual(client.get(url+'/records/2', headers=headers).status_code, 409)
        self.assertEqual(client.get(url+'/facts/records/rows', headers=headers).status_code, 409)
        tool = client.get(url+'/tools/'+b['tools'][0]['id'], headers=headers).json()
        self.assertEqual(tool['status'], 'stale')
        self.assertIn('记录数', tool['tool']['output'])

    def test_successful_agent_commits_actual_tool_snapshot_cancelled_run_does_not(self):
        def chunk(text=None, name=None):
            calls=[NS(index=0,id='actual',function=NS(name=name,arguments='{}'))] if name else None
            return NS(choices=[NS(delta=NS(content=text,tool_calls=calls),finish_reason='tool_calls' if name else 'stop')])
        with patch('backend.agent.loop.resolve_api_key', return_value='controlled'), patch('backend.agent.loop.OpenAI') as client:
            client.return_value.chat.completions.create.side_effect=[[chunk(name='get_data_summary')], [chunk(text='受控摘要')]]
            list(run_agent(self.s, '受控问题'))
            b = self.s.messages[-1]['evidence']
            self.assertEqual(bundle_status(self.s, b)[0], 'current')
            self.assertIn('records', [f['id'] for f in b['facts']])
            s = sample_session()
            client.return_value.chat.completions.create.side_effect=[[chunk(name='get_data_summary')]]
            for frame in run_agent(s, '受控停止'):
                if frame.startswith('event: tool_done'):
                    s.agent_run.request_stop()
            self.assertFalse(any(m.get('evidence') for m in s.messages))

    def test_evidence_creation_failure_does_not_commit_final_model_context(self):
        self.s.full_messages = [{'role': 'user', 'content': '上次已保存上下文'}]
        chunk = NS(choices=[NS(delta=NS(content='不能保存的最终正文', tool_calls=None), finish_reason='stop')])
        with patch('backend.agent.loop.resolve_api_key', return_value='controlled'), patch('backend.agent.loop.OpenAI') as client, \
             patch('backend.agent.loop.build_bundle', side_effect=ValueError('受控证据构造失败')):
            client.return_value.chat.completions.create.return_value = [chunk]
            list(run_agent(self.s, '受控问题'))
        self.assertEqual(self.s.agent_run.status, 'failed')
        self.assertEqual(self.s.full_messages, [{'role': 'user', 'content': '上次已保存上下文'}])
        self.assertFalse(any(m['role'] == 'assistant' for m in self.s.messages))


if __name__ == '__main__':
    unittest.main()
