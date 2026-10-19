"""质量复核规则与真实短排放反例：不把质量标记当作删除污染的依据。"""
import base64
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import Session, STORE
from backend.project_codec import dump_project, load_project, ProjectError
from backend.agent.tools import execute_tool, truncate
from backend.map_samples import map_samples
from water_quality_mcp.src.water_quality_mcp.quality_control import assess_quality, QualityConfig
from water_quality_mcp.src.water_quality_mcp.analysis import find_black_spots, quality_report, build_comprehensive_summary
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
from water_quality_mcp.src.water_quality_mcp.loaders import load_data
from water_quality_mcp.src.water_quality_mcp.report import generate_report


def series(nh3, do=None, seconds=None):
    n = len(nh3)
    seconds = np.arange(n) * 5 if seconds is None else seconds
    return pd.DataFrame({'datetime': pd.Timestamp('2026-10-19') + pd.to_timedelta(seconds, unit='s'),
        'ammonia_nitrogen': nh3, 'dissolved_oxygen': np.full(n, 6.) if do is None else do,
        'latitude': np.full(n, 30.), 'longitude': 120. + np.arange(n) * .00001})


class RuleTests(unittest.TestCase):
    def test_isolated_excursion_is_marked_only_at_the_center(self):
        qc = assess_quality(series([.5, .5, 20., .5, .5]))
        self.assertEqual([r['id'] for r in qc.rows if r['flag'] == 3], [2])
        self.assertEqual(qc.rows[2]['tests']['ammonia_nitrogen']['spike'], 3)

    def test_real_one_sample_pollution_pulse_remains_an_alert(self):
        # 仪器突刺与真实短排放可以产生完全相同的输入，算法必须承认无法区分。
        df = series([.5, .5, 20., .5, .5])
        raw, black = find_black_spots(df)
        self.assertEqual(len(black), 1)
        self.assertEqual(float(black.ammonia_nitrogen.iloc[0]), 20.)
        self.assertEqual(json.loads(raw)['数据质量']['需质控复核预警点数'], 1)

    def test_two_observation_short_pulse_is_not_silently_filtered(self):
        df = series([.5, .5, 12., 12., .5, .5])
        raw, black = find_black_spots(df)
        self.assertEqual(len(black), 2)
        self.assertEqual(json.loads(raw)['数据质量']['原始阈值预警点数'], 2)

    def test_all_normal_values_do_not_become_alerts_due_to_list_comparison(self):
        qc = quality_report(series([.3, .4, .5, .4, .3]))
        self.assertEqual(qc.summary['原始阈值预警点数'], 0)

    def test_constant_anoxic_water_is_flagged_for_review_and_kept_as_pollution(self):
        df = series([.5]*8, [0.]*8, np.arange(8)*10)
        raw, black = find_black_spots(df)
        self.assertEqual(len(black), 8)
        self.assertEqual(json.loads(raw)['数据质量']['需质控复核预警点数'], 8)
        self.assertTrue(black.dissolved_oxygen.eq(0).all())

    def test_dense_sampling_does_not_invent_sixty_seconds_of_flat_line(self):
        for seconds in (np.arange(31), [0, 10, 20, 30]):
            qc = assess_quality(series([1.]*len(seconds), seconds=seconds))
            self.assertEqual(qc.summary['规则标记记录数']['持续近恒值'], 0)

    def test_flat_line_duration_boundary_and_custom_configuration(self):
        df = series([1.]*5, seconds=[0, 15, 30, 45, 60])
        self.assertEqual(assess_quality(df).summary['规则标记记录数']['持续近恒值'], 5)
        self.assertEqual(assess_quality(df, QualityConfig(flat_duration_s=61)).summary['规则标记记录数']['持续近恒值'], 0)

    def test_duplicates_are_not_independent_flat_line_support(self):
        qc = assess_quality(series([1.]*30, seconds=[0]*30))
        self.assertEqual(qc.summary['规则标记记录数']['重复时间戳'], 30)
        self.assertEqual(qc.summary['规则标记记录数']['持续近恒值'], 0)

    def test_long_gaps_break_context_and_are_reported(self):
        qc = assess_quality(series([1.]*6, seconds=[0,5,10,200,205,210]))
        self.assertEqual(qc.summary['规则标记记录数']['持续近恒值'], 0)
        self.assertEqual(qc.summary['规则标记记录数']['采样间隔过大'], 1)

    def test_slow_drift_does_not_accumulate_into_flat_line(self):
        df = series(1. + np.arange(120)*.0009, 6. + np.arange(120)*.0009, np.arange(120))
        self.assertEqual(assess_quality(df).summary['规则标记记录数']['持续近恒值'], 0)

    def test_irregular_sampling_uses_the_actual_time_baseline(self):
        df = series([1., 2., 4.], seconds=[0, 10, 30])
        self.assertEqual(assess_quality(df).summary['规则标记记录数']['孤立突刺'], 0)

    def test_unsorted_rows_preserve_ids_and_do_not_mutate_input(self):
        df = series([.5, .5, 20., .5, .5]).iloc[[4,2,0,3,1]].copy()
        before = df.copy(deep=True)
        qc = assess_quality(df)
        self.assertEqual([r['id'] for r in qc.rows if r['flag'] == 3], [1])
        pd.testing.assert_frame_equal(df, before)

    def test_interpolated_missing_measurement_does_not_bridge_spike_context(self):
        df = series([.5, .5, 20., .5, .5])
        df['__observed_ammonia_nitrogen'] = [True, False, True, True, True]
        qc = assess_quality(df)
        self.assertEqual(qc.rows[1]['flag'], 9)
        self.assertEqual(qc.rows[2]['tests']['ammonia_nitrogen']['spike'], 2)

    def test_missing_time_and_single_point_remain_unevaluated(self):
        df = series([1.]); df['datetime'] = pd.NaT
        qc = assess_quality(df)
        self.assertEqual(qc.rows[0]['flag'], 2)
        json.dumps(qc.summary, allow_nan=False)

    def test_empty_table_has_finite_zero_counts(self):
        qc = assess_quality(series([]))
        self.assertEqual(qc.summary['记录数'], 0)
        self.assertEqual(qc.rows, [])
        json.dumps(qc.summary, allow_nan=False)

    def test_thresholds_can_be_changed_without_changing_data(self):
        df = series([.5, .5, 1.9, .5, .5])
        base = assess_quality(df)
        changed = assess_quality(df, QualityConfig(spike_thresholds={'ammonia_nitrogen':2., 'dissolved_oxygen':.5}))
        self.assertEqual(base.rows[2]['flag'], 3)
        self.assertNotEqual(changed.rows[2]['flag'], 3)

    def test_invalid_parameters_are_rejected(self):
        for kwargs in ({'max_gap_s':0}, {'flat_duration_s':np.nan}, {'flat_min_points':True},
                {'spike_thresholds':{'ammonia_nitrogen':0.,'dissolved_oxygen':.5}},
                {'flat_tolerances':{}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): QualityConfig(**kwargs)

    def test_legacy_data_does_not_get_fabricated_original_values(self):
        qc = assess_quality(series([1., 2., 3.]))
        self.assertFalse(qc.summary['原始值已保留']['ammonia_nitrogen'])
        self.assertIsNone(qc.rows[1]['raw_values']['ammonia_nitrogen'])


class RawAndApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id':self.sid}
        self.s = STORE.get(self.sid)
        data = series([.5, .5, 20., .5, .5])
        csv = data.to_csv(index=False).encode()
        self.s.df, report = load_data(base64.b64encode(csv).decode(), '仿真.csv')
        self.assertIsNotNone(self.s.df, report)
        self.s.data_filename = '仿真.csv'

    def tearDown(self):
        STORE.drop(self.sid); self.client.close()

    def test_raw_values_survive_cleaning_and_project_roundtrip(self):
        csv = ('时间,氨氮,溶解氧\n2026-10-19 10:00:00,0.5,6\n'
               '2026-10-19 10:00:05,20,50\n2026-10-19 10:00:10,0.5,6\n').encode()
        df, report = load_data(base64.b64encode(csv).decode(), 'data.csv')
        self.assertIsNotNone(df, report)
        self.assertEqual(df.loc[1,'__raw_dissolved_oxygen'],50)
        self.assertEqual(df.loc[1,'dissolved_oxygen'],6.)
        self.s.df = df
        snapshot = load_project(dump_project(self.s, '质控原值'))
        restored = snapshot.state['df']
        self.assertEqual(restored.loc[1,'__raw_dissolved_oxygen'],50)
        self.assertEqual(assess_quality(restored).rows[1]['flag'],4)

    def test_invalid_text_is_retained_but_never_used_as_numeric_evidence(self):
        csv = ('时间,氨氮,溶解氧\n2026-10-19 10:00:00,0.5,6\n'
               '2026-10-19 10:00:05,ERR,6\n2026-10-19 10:00:10,0.5,6\n').encode()
        df, _ = load_data(base64.b64encode(csv).decode(), 'data.csv')
        qc = assess_quality(df)
        self.assertEqual(qc.rows[1]['flag'],4)
        self.assertEqual(qc.rows[1]['raw_text']['ammonia_nitrogen'],'ERR')
        self.assertFalse(df.loc[1,'__observed_ammonia_nitrogen'])

    def test_quality_endpoint_is_read_only_and_paginated(self):
        self.s.messages = [{'role':'assistant','content':'历史报告'}]
        model = self.s.get_river_state(); version = self.s.analysis_revision
        before = self.s.df.copy(deep=True)
        r = self.client.get('/api/analysis/quality?limit=1', headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['records'][0]['id'],2)
        self.assertEqual(r.json()['records'][0]['input_record_id'],3)
        self.assertEqual(r.json()['summary']['原始阈值预警点数'],1)
        self.assertEqual(self.client.get('/api/analysis/quality?offset=1',headers=self.headers).json()['records'],[])
        pd.testing.assert_frame_equal(before,self.s.df)
        self.assertIs(self.s.river_state,model)
        self.assertEqual(self.s.analysis_revision,version)
        self.assertEqual(len(self.s.messages),1)

    def test_quality_api_strict_validation_and_session_isolation(self):
        for query in ('limit=0','limit=501','offset=-1','category=bogus'):
            self.assertEqual(self.client.get('/api/analysis/quality?'+query,headers=self.headers).status_code,422)
        other = self.client.get('/api/analysis/quality')
        self.assertEqual(other.status_code,400)
        STORE.drop(other.headers['X-Session-Id'])

    def test_quality_config_updates_review_only_and_survives_project_restore(self):
        self.client.post('/api/analysis/black-spots',headers=self.headers).raise_for_status()
        before = self.s.black_spots.copy(deep=True)
        values = self.s.df.copy(deep=True)
        model = self.s.get_river_state()
        self.s.messages = [{'role':'assistant','content':'原报告'}]
        self.s.analysis_done = True; self.s.analysis_status = 'succeeded'
        revision = self.s.analysis_revision
        config = asdict(QualityConfig(spike_thresholds={'ammonia_nitrogen':30.,'dissolved_oxygen':.5}))
        response = self.client.post('/api/analysis/quality-config',json=config,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['summary']['原始阈值预警点数'],1)
        self.assertEqual(response.json()['summary']['需质控复核预警点数'],0)
        # 调门限只改变质量注记，不改变观测、污染评分/分级/聚类，也不撤销物理模型。
        pd.testing.assert_frame_equal(before.drop(columns='__qc_flag'),self.s.black_spots.drop(columns='__qc_flag'))
        pd.testing.assert_frame_equal(values,self.s.df,check_flags=False)
        self.assertIs(self.s.river_state,model)
        self.assertEqual(self.s.analysis_revision,revision)
        self.assertFalse(self.s.analysis_done)
        self.assertEqual(self.s.analysis_status,'stale')
        self.assertEqual(self.s.messages[0]['content'],'原报告')
        restored = Session(id='restored-config',**load_project(dump_project(self.s,'质控阈值')).state)
        self.assertEqual(asdict(restored.analysis_config.quality_control),config)
        self.assertEqual(quality_report(restored.df,restored.analysis_config).summary['需质控复核预警点数'],0)

    def test_bad_or_busy_quality_config_cannot_partially_change_state(self):
        config = asdict(QualityConfig())
        for invalid in ({**config,'flat_min_points':True},{**config,'max_gap_s':0},
                        {**config,'flat_duration_s':'60'},{**config,'unknown':1},
                        {**config,'spike_thresholds':{'ammonia_nitrogen':0.,'dissolved_oxygen':.5}}):
            response = self.client.post('/api/analysis/quality-config',json=invalid,headers=self.headers)
            self.assertEqual(response.status_code,422,response.text)
            self.assertNotIn('quality_control_config',self.s.df.attrs)
        with self.s.operation_lock:
            response = self.client.post('/api/analysis/quality-config',json=config,headers=self.headers)
        self.assertEqual(response.status_code,409)
        self.assertNotIn('quality_control_config',self.s.df.attrs)
        self.assertEqual(self.s.results_revision,0)

    def test_config_calculation_failure_retains_old_parameters_and_detection(self):
        self.client.post('/api/analysis/black-spots',headers=self.headers).raise_for_status()
        before = self.s.black_spots_json; revision = self.s.results_revision
        with patch('backend.routers.analysis.find_black_spots',return_value=('{}',None)):
            response = self.client.post('/api/analysis/quality-config',json=asdict(QualityConfig()),headers=self.headers)
        self.assertEqual(response.status_code,400)
        self.assertNotIn('quality_control_config',self.s.df.attrs)
        self.assertEqual(self.s.black_spots_json,before)
        self.assertEqual(self.s.results_revision,revision)

    def test_import_rejects_invalid_quality_configuration(self):
        self.s.df.attrs['quality_control_config'] = {'max_gap_s':-1}
        with self.assertRaisesRegex(ProjectError,'质控参数'):
            load_project(dump_project(self.s,'错误阈值'))

    def test_detection_comprehensive_map_and_agent_share_quality_counts(self):
        detected = self.client.post('/api/analysis/black-spots',headers=self.headers).json()['result']
        summary = self.client.get('/api/analysis/comprehensive',headers=self.headers).json()
        mapping = self.client.get('/api/charts/trace-map',headers=self.headers).json()
        raw, failed = execute_tool('get_data_summary',{},self.s)
        self.assertFalse(failed)
        agent = json.loads(raw)['data']
        for qc in (detected['数据质量'],summary['数据质量'],mapping['data_quality'],agent['数据质量']):
            self.assertEqual(qc['原始阈值预警点数'],1)
            self.assertEqual(qc['需质控复核预警点数'],1)
        sample = mapping['samples'][2]
        self.assertEqual(sample['quality']['flag'],3)
        self.assertEqual(sample['values']['ammonia_nitrogen'],20.)
        self.assertEqual(sample['quality']['raw_values']['ammonia_nitrogen'],20.)
        # Agent截断时保留主要统计和需复核计数，不把全部质量说明挤在主要指标之前。
        compact = truncate('get_data_summary',raw)
        self.assertIn('需质控复核预警点数',compact)
        self.assertIn('氨氮',compact)

    def test_report_exposes_review_count_without_cancelling_alerts(self):
        self.client.post('/api/analysis/black-spots',headers=self.headers)
        evidence = build_comprehensive_summary(self.s.water_quality_state())
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases',return_value=''), \
             patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = SimpleNamespace(
                choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content='请复核原值。'))])
            report = generate_report('test-only',evidence)
        self.assertIn('需质控复核预警：1',report)
        self.assertIn('仍保留全部预警',report)


if __name__ == '__main__': unittest.main()
