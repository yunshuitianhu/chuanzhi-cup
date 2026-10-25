"""受控像素及HTTP流程验证；没有人工污染真值，不评估水质准确率。"""
import base64
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
import cv2
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import Session,STORE
from backend.project_codec import dump_project,load_project
from water_quality_mcp.src.water_quality_mcp import video_quality as core
from water_quality_mcp.src.water_quality_mcp.analysis import analyze_video_frames,cross_validate_video_with_monitoring


def texture(dark=False):
    cells=(np.indices((160,240)).sum(axis=0)//4)%2
    gray=(20+cells*60 if dark else 80+cells*80).astype(np.uint8)
    return np.repeat(gray[:,:,None],3,axis=2)


def frame(image,stamp=0):
    ok,data=cv2.imencode('.jpg',image);assert ok
    return {'time_sec':stamp,'image_base64':base64.b64encode(data).decode()}


def settings(**updates):
    return {**core.DEFAULTS,'roi_confirmed':True,'roi_note':'受控水面区域，非实测','reviewer':'测试复核人',
        'sync_confirmed':True,'sync_note':'受控相同时钟',**updates}


def video():
    frames=[frame(texture(i%2==1),i*5) for i in range(4)]
    frames[0]['video_settings']=settings()
    return frames


def monitoring():
    return pd.DataFrame({'datetime':pd.date_range('2026-10-24',periods=4,freq='5s'),
        'ammonia_nitrogen':[.2,12.,.3,15.],'dissolved_oxygen':[7.,0.,6.,.1],
        'turbidity':[5.,20.,8.,30.],'cod':[10.,30.,12.,40.]})


class VideoQualityCoreTests(unittest.TestCase):
    def test_unconfirmed_region_never_becomes_clean_or_black_odor(self):
        rows=core.inspect_video([frame(texture())])['rows']
        self.assertFalse(rows[0]['eligible']);self.assertIsNone(rows[0]['dark_color_cue'])
        text=analyze_video_frames([frame(np.zeros((80,80,3),np.uint8))])
        for claim in ('重度浑浊','清澈','疑似黑臭'):self.assertNotIn(claim,text)

    def test_blur_glare_dark_and_manual_include_do_not_bypass_quality(self):
        for image,flag in [(np.full((80,80,3),120,np.uint8),'low_texture_or_blur'),
                           (np.full((80,80,3),255,np.uint8),'glare'),
                           (np.zeros((80,80,3),np.uint8),'too_dark')]:
            f=frame(image);f['review']={'decision':'include','reviewer':'测试','notes':'人工保留不覆盖质量'}
            q=core.inspect_frame(f,0,settings())
            self.assertIn(flag,q['quality_flags']);self.assertFalse(q['eligible'])

    def test_water_roi_excludes_bright_bank_and_preserves_raw_frame(self):
        image=texture();image[:80]=255;f=frame(image);before=deepcopy(f)
        full=core.inspect_frame(f,0,settings())
        cropped=core.inspect_frame(f,0,settings(roi=[0.,.5,1.,1.]))
        self.assertIn('glare',full['quality_flags']);self.assertTrue(cropped['eligible'])
        self.assertEqual(f,before)

    def test_corrupt_time_image_and_tiny_roi_stay_unusable(self):
        for f in [{'time_sec':0,'image_base64':'bad'},frame(texture(),float('inf'))]:
            self.assertIn('invalid_frame',core.inspect_frame(f,0,settings())['quality_flags'])
        self.assertIn('roi_too_small',core.inspect_frame(frame(texture()),0,settings(roi=[0.,0.,.02,.02]))['quality_flags'])

    def test_settings_and_review_require_human_basis_and_valid_bounds(self):
        for value in [settings(roi=[0,0,0,1]),settings(roi=[False,0,1,1]),settings(reviewer=''),
                      settings(time_window_sec=0),settings(roi_note=''),settings(sync_note='')]:
            with self.assertRaises(ValueError):core.validate_settings(value)
        with self.assertRaises(ValueError):core.validate_review({'decision':'exclude'})

    def test_only_usable_frames_and_matching_human_sync_can_produce_exploratory_numbers(self):
        s=Session('video',df=monitoring(),video_frames=video())
        raw=json.loads(cross_validate_video_with_monitoring(s))
        self.assertEqual(raw['状态'],'探索性线索对照');self.assertEqual(raw['时间对齐']['可分析匹配帧数'],4)
        self.assertIsNotNone(raw['黑臭判定一致性']['一致率_%'])
        self.assertNotIn('视频判黑准确率_%',raw['黑臭判定一致性'])
        mismatch=json.loads(cross_validate_video_with_monitoring(s,1,30))
        self.assertEqual(mismatch['状态'],'未判定');self.assertIsNone(mismatch['黑臭判定一致性']['一致率_%'])
        self.assertEqual(mismatch['相关系数矩阵'],[])
        s.video_frames[0]['video_settings']['sync_confirmed']=False
        self.assertEqual(json.loads(cross_validate_video_with_monitoring(s))['状态'],'未判定')

    def test_per_frame_exclusion_and_roi_override_are_recomputed_not_trusted(self):
        frames=video();frames[1]['review']={'decision':'exclude','notes':'岸边遮挡','reviewer':'测试'}
        quality=core.inspect_video(frames)
        self.assertEqual(quality['summary']['eligible'],3);self.assertEqual(quality['summary']['manual_excluded'],1)
        self.assertIn('manual_exclusion',quality['rows'][1]['quality_flags'])


class VideoQualityApiTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app);self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid};self.s=STORE.get(self.sid)
        self.s.df=monitoring();self.s.video_frames=video();self.s.video_filename='受控视频.mp4'

    def tearDown(self):self.client.close();STORE.drop(self.sid)
    def get(self):
        response=self.client.get('/api/video/quality',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text);return response.json()

    def test_paging_image_evidence_isolation_and_busy_lock(self):
        q=self.get();self.assertEqual(q['summary']['frames'],4)
        image=self.client.get('/api/video/frames/1',params={'expected_revision':q['revision']},headers=self.headers)
        self.assertEqual(image.status_code,200,image.text);self.assertEqual(image.json()['media_type'],'image/jpeg')
        self.assertEqual(image.json()['frame']['time_sec'],5)
        other=self.client.get('/api/video/quality');self.assertEqual(other.status_code,400);STORE.drop(other.headers['X-Session-Id'])
        self.s.operation_lock.acquire()
        try:
            self.assertEqual(self.client.get('/api/video/quality',headers=self.headers).status_code,200)
            change=self.client.put('/api/video/settings',headers=self.headers,
                json={'expected_revision':q['revision'],'settings':settings()})
            self.assertEqual(change.status_code,409)
        finally:self.s.operation_lock.release()

    def test_settings_mutation_revokes_report_and_survives_project_codec(self):
        q=self.get();raw=self.s.video_frames[0]['image_base64']
        self.s.cross_validation={'old':True};self.s.analysis_done=True;self.s.analysis_status='succeeded'
        value=settings(roi=[0,.1,1,.9])
        r=self.client.put('/api/video/settings',json={'expected_revision':q['revision'],'settings':value},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text);self.assertIsNone(self.s.cross_validation)
        self.assertEqual(self.s.analysis_status,'stale');self.assertEqual(self.s.video_frames[0]['image_base64'],raw)
        snapshot=load_project(dump_project(self.s,'受控视频复核'))
        self.assertEqual(snapshot.state['video_frames'][0]['video_settings'],value)
        self.assertEqual(core.inspect_video(snapshot.state['video_frames'])['summary']['frames'],4)

    def test_stale_revision_and_invalid_input_do_not_change_frames(self):
        q=self.get();before=deepcopy(self.s.video_frames)
        bad=self.client.put('/api/video/settings',json={'expected_revision':q['revision'],'settings':settings(reviewer='')},headers=self.headers)
        self.assertEqual(bad.status_code,422);self.assertEqual(self.s.video_frames,before)
        r=self.client.put('/api/video/frames/1',json={'expected_revision':q['revision'],
            'review':{'decision':'exclude','reviewer':'测试','notes':'受控排除'}},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.get()['summary']['manual_excluded'],1)
        stale=self.client.put('/api/video/settings',json={'expected_revision':q['revision'],'settings':settings()},headers=self.headers)
        self.assertEqual(stale.status_code,409)

    def test_settings_and_frame_review_commit_atomically(self):
        q=self.get();before=deepcopy(self.s.video_frames)
        body={'expected_revision':q['revision'],'settings':settings(roi=[0,.2,1,.9]),'frame_id':1,
            'review':{'decision':'exclude','reviewer':'','notes':''}}
        response=self.client.put('/api/video/settings',json=body,headers=self.headers)
        self.assertEqual(response.status_code,422);self.assertEqual(self.s.video_frames,before)
        body['review']={'decision':'exclude','reviewer':'受控测试','notes':'同时保存区域与遮挡帧记录'}
        response=self.client.put('/api/video/settings',json=body,headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.s.video_frames[0]['video_settings']['roi'],[0,.2,1,.9])
        self.assertEqual(self.s.video_frames[1]['review']['decision'],'exclude')

    def test_unopenable_video_releases_decoder_and_removes_temporary_file(self):
        from pathlib import Path
        from water_quality_mcp.src.water_quality_mcp.loaders import extract_video
        with patch('water_quality_mcp.src.water_quality_mcp.loaders.cv2.VideoCapture') as factory:
            cap=factory.return_value;cap.isOpened.return_value=False
            frames,report=extract_video(base64.b64encode(b'controlled').decode(),interval=1)
            cap.release.assert_called_once()
            self.assertFalse(Path(factory.call_args.args[0]).exists())
        self.assertEqual(frames,[]);self.assertIn('无法打开',report)

    def test_invalid_sampling_interval_and_base64_do_not_open_decoder(self):
        from water_quality_mcp.src.water_quality_mcp.loaders import extract_video
        with patch('water_quality_mcp.src.water_quality_mcp.loaders.cv2.VideoCapture') as factory:
            for interval in (0,-1,float('inf'),True,'5'):
                self.assertEqual(extract_video('YQ==',interval)[0],[])
            self.assertEqual(extract_video('invalid!')[0],[])
            factory.assert_not_called()

    def test_upload_keeps_dark_frames_for_review_and_releases_decoder(self):
        from water_quality_mcp.src.water_quality_mcp.loaders import extract_video
        with patch('water_quality_mcp.src.water_quality_mcp.loaders.cv2.VideoCapture') as factory:
            cap=factory.return_value;cap.isOpened.return_value=True
            cap.get.side_effect=lambda prop:1. if prop==cv2.CAP_PROP_FPS else 2.
            cap.read.side_effect=[(True,np.zeros((80,80,3),np.uint8)),(False,None)]
            frames,report=extract_video(base64.b64encode(b'controlled').decode(),interval=1)
            cap.release.assert_called_once()
        self.assertEqual(len(frames),1);self.assertIn('保留 1 个低亮帧',report)
        self.assertIn('too_dark',core.inspect_video(frames)['rows'][0]['quality_flags'])


if __name__=='__main__':unittest.main()
