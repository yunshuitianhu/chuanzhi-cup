"""查漏回归：视频替换、持久化结果读取及交叉印证的异常输入。"""
import base64
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.main import app
from backend.session import STORE, Session
from backend.agent.tools import execute_tool
from water_quality_mcp.src.water_quality_mcp.analysis import cross_validate_video_with_monitoring


def frames():
    ok,image=cv2.imencode('.jpg',np.full((8,8,3),30,dtype=np.uint8))
    assert ok
    encoded=base64.b64encode(image).decode()
    return [{'time_sec':s,'image_base64':encoded} for s in (0,5,10)]


def monitoring():
    return pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=3,freq='5s'),
        'ammonia_nitrogen':[20.]*3,'dissolved_oxygen':[0.]*3,'turbidity':[5.]*3})


class GapApiTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app,raise_server_exceptions=False)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid};self.session=STORE.get(self.sid)
        self.session.df=monitoring();self.session.video_frames=frames()

    def tearDown(self):
        STORE.drop(self.sid);self.client.close()

    def test_replacing_video_preserves_monitoring_detection_model_and_history(self):
        s=self.session;original=s.df
        s.black_spots=original.copy();black=s.black_spots;s.black_spots_json='{"黑点总数":3}'
        model=s.get_river_state();s.river_ui={'form':{'length_m':800}}
        s.messages=[{'role':'assistant','content':'历史报告'}]
        s.analysis_done=True;s.analysis_status='succeeded'
        s.cross_validation={'old':True};s.comprehensive_summary='old'
        s.tool_evidence={'analyze_video':'old','reverse_geocode':'address'}
        revision=s.analysis_revision
        with patch('backend.routers.data.extract_video',return_value=(frames(),'完成')):
            r=self.client.post('/api/data/video',headers=self.headers,
                files={'file':('new.mp4',b'synthetic-video','video/mp4')})
        self.assertEqual(r.status_code,200,r.text)
        self.assertIs(s.df,original);self.assertIs(s.black_spots,black)
        self.assertIs(s.river_state,model);self.assertEqual(s.river_ui['form']['length_m'],800)
        self.assertEqual(s.analysis_revision,revision)
        self.assertEqual(s.messages[0]['content'],'历史报告')
        self.assertIsNone(s.cross_validation);self.assertIsNone(s.comprehensive_summary)
        self.assertNotIn('analyze_video',s.tool_evidence)
        self.assertEqual(s.tool_evidence['reverse_geocode'],'address')
        self.assertFalse(s.analysis_done);self.assertEqual(s.analysis_status,'stale')

    def test_read_saved_results_without_reexecuting_analysis(self):
        self.session.black_spots=self.session.df.copy()
        self.session.black_spots_json=json.dumps({'黑点总数':3,'聚类详情':[{'cluster_id':0,'count':3}]})
        self.session.cross_validation={'时间对齐':{'匹配成功帧数':3}}
        with patch('backend.routers.analysis.find_black_spots') as detector:
            response=self.client.get('/api/analysis/results',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        payload=response.json();detector.assert_not_called()
        self.assertEqual(payload['black_spots']['result']['黑点总数'],3)
        self.assertEqual(payload['cross_validation']['时间对齐']['匹配成功帧数'],3)
        self.assertIsNone(self.session.river_state)
        other=self.client.get('/api/analysis/results')
        self.assertIsNone(other.json()['black_spots'])
        STORE.drop(other.headers['X-Session-Id'])

    def test_constant_video_and_sensor_sequences_do_not_produce_invalid_json(self):
        response=self.client.post('/api/analysis/cross-validate',headers=self.headers,json={})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();json.dumps(data,allow_nan=False)
        self.assertEqual(data['时间对齐']['匹配成功帧数'],3)
        self.assertEqual(data['相关系数矩阵'],[])
        self.assertTrue(data['相关性限制'])

    def test_invalid_cross_validation_inputs_are_rejected_without_mutation(self):
        previous={'retained':True};self.session.cross_validation=previous
        for body in ({'time_window_sec':0},{'time_window_sec':-1},{'time_window_sec':'abc'},
                     {'time_offset_sec':True},{'time_offset_sec':None},{'unexpected':1}):
            with self.subTest(body=body):
                r=self.client.post('/api/analysis/cross-validate',headers=self.headers,json=body)
                self.assertEqual(r.status_code,422,r.text)
                self.assertIs(self.session.cross_validation,previous)

    def test_negative_offset_and_fractional_window_are_supported(self):
        r=self.client.post('/api/analysis/cross-validate',headers=self.headers,
            json={'time_offset_sec':-.25,'time_window_sec':1.5})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['时间对齐']['时间偏移秒'],-.25)
        self.assertEqual(r.json()['时间对齐']['匹配窗口秒'],1.5)

    def test_nonfinite_json_numbers_return_validation_error_not_server_error(self):
        for literal in ('NaN','Infinity','-Infinity','1e400'):
            with self.subTest(literal=literal):
                r=self.client.post('/api/analysis/cross-validate',headers={**self.headers,'Content-Type':'application/json'},
                    content='{"time_window_sec":'+literal+'}')
                self.assertEqual(r.status_code,422,r.text)
                json.dumps(r.json(),allow_nan=False)

    def test_large_finite_timing_returns_readable_error_and_preserves_result(self):
        previous={'keep':True};self.session.cross_validation=previous
        r=self.client.post('/api/analysis/cross-validate',headers=self.headers,
            json={'time_offset_sec':1e100})
        self.assertEqual(r.status_code,400,r.text)
        self.assertIn('时间',r.json()['detail'])
        self.assertIs(self.session.cross_validation,previous)

    def test_corrupt_result_cache_is_reported_without_breaking_the_project(self):
        self.session.black_spots=self.session.df.copy();self.session.black_spots_json='{bad json'
        self.session.cross_validation={'bad':float('nan')}
        r=self.client.get('/api/analysis/results',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertIsNone(r.json()['black_spots']);self.assertIsNone(r.json()['cross_validation'])
        self.assertEqual(len(r.json()['warnings']),2)

    def test_bad_video_frame_is_skipped_and_counted(self):
        self.session.video_frames.extend([{'time_sec':15,'image_base64':'bad-image'},
            {'time_sec':float('inf'),'image_base64':frames()[0]['image_base64']}])
        r=self.client.post('/api/analysis/cross-validate',headers=self.headers,json={})
        self.assertEqual(r.status_code,200,r.text)
        alignment=r.json()['时间对齐']
        self.assertEqual(alignment['总视频帧数'],5)
        self.assertEqual(alignment['无效视频帧数'],2)

    def test_agent_uses_same_timing_validation_and_invalidates_report(self):
        s=self.session;s.analysis_status='succeeded';s.analysis_done=True;s.comprehensive_summary='old'
        raw,failed=execute_tool('cross_validate_video',{'time_window_sec':0},s)
        self.assertTrue(failed);self.assertEqual(s.comprehensive_summary,'old')
        raw,failed=execute_tool('cross_validate_video',{'time_offset_sec':-.25,'time_window_sec':1.5},s)
        self.assertFalse(failed,raw)
        self.assertIsNone(s.comprehensive_summary);self.assertEqual(s.analysis_status,'stale')

    def test_new_detection_revokes_old_trace_evidence_without_resetting_model(self):
        s=self.session;model=s.get_river_state();revision=s.analysis_revision
        s.analysis_status='succeeded';s.analysis_done=True
        s.tool_evidence={'reverse_geocode':'old center','search_nearby_pollution_sources':'old source',
            'search_treatment_cases':'old cases','analyze_video':'same video'}
        r=self.client.post('/api/analysis/black-spots',headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertIs(s.river_state,model);self.assertEqual(s.analysis_revision,revision)
        self.assertEqual(s.tool_evidence,{'analyze_video':'same video'})
        self.assertEqual(s.analysis_status,'stale');self.assertGreater(s.results_revision,0)


class GapSummaryTests(unittest.TestCase):
    def test_legacy_video_percentages_are_not_reported_as_ground_truth_accuracy(self):
        from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
        from water_quality_mcp.src.water_quality_mcp.analysis import build_comprehensive_summary
        state=WaterQualityState();state.df=monitoring()
        legacy={'视频判黑准确率_%':0,'视频判黑召回率_%':50}
        state.cross_validation_result={'黑臭判定一致性':legacy}
        summary=json.loads(build_comprehensive_summary(state))['交叉印证']['黑臭判定一致性']
        self.assertNotIn('视频判黑准确率_%',summary)
        self.assertEqual(summary['视频预警符合率_%'],0)
        self.assertEqual(summary['传感器预警视频覆盖率_%'],50)
        self.assertIn('没有人工真值',summary['指标口径'])
        self.assertIn('视频判黑准确率_%',legacy)

    def test_report_invalidation_during_automatic_run_keeps_the_run_active(self):
        s=Session(id='auto',analysis_status='running',analysis_steps={'get_comprehensive_summary':'succeeded',
            'generate_final_report':'succeeded','get_data_summary':'succeeded'})
        s.set_detection('{}',monitoring())
        self.assertEqual(s.analysis_status,'running')
        self.assertNotIn('generate_final_report',s.analysis_steps)
        self.assertEqual(s.analysis_steps['get_data_summary'],'succeeded')


if __name__=='__main__':unittest.main()
