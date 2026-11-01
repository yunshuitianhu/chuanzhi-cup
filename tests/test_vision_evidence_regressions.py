"""Q18 阶段 A 视觉观察：受控模型替身，不访问外部服务。"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.media_assets import store_media
from backend.routers.video import assessment
from backend.session import STORE, Session
from backend.vision import VisionError
from backend.vision_evidence import VisionServiceError, observe, observe_sequence
from test_video_quality_regressions import video


VALID = json.dumps({
    'observations': [
        {'visible': '水面右侧有一条浅色泡沫带', 'where': '画面右侧近岸',
         'uncertainty': ['无法判断是否持续存在']},
    ],
    'not_visible': ['没有看到明确的排口'],
    'quality_notes': ['画面存在轻微反光'],
}, ensure_ascii=False)

SEQ_VALID = json.dumps({
    'frames': [
        {'index': 1, 'visible': '规则棋盘纹理，整体较亮', 'where': '全画面',
         'uncertainty': []},
        {'index': 2, 'visible': '规则棋盘纹理，整体较暗', 'where': '全画面',
         'uncertainty': []},
    ],
    'changes': [
        {'from': 1, 'to': 2, 'visible_change': '第二张整体更暗', 'uncertainty': ['只比较两张图，不代表连续变化']},
    ],
    'not_visible': ['没有看到明确的漂浮物'],
    'quality_notes': [],
}, ensure_ascii=False)

STATIC_VALID = json.dumps({
    'frames': [
        {'index': 1, 'visible': '规则棋盘纹理', 'where': '全画面', 'uncertainty': []},
        {'index': 2, 'visible': '规则棋盘纹理', 'where': '全画面', 'uncertainty': []},
    ],
    'changes': [], 'not_visible': [], 'quality_notes': ['两张图画面相同'],
}, ensure_ascii=False)


class _Completions:
    def __init__(self, content=VALID, error=None, finish='stop'):
        self.content = content
        self.error = error
        self.finish = finish
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return NS(choices=[NS(message=NS(content=self.content), finish_reason=self.finish)])


class _Client:
    def __init__(self, content=VALID, error=None, finish='stop'):
        self.chat = NS(completions=_Completions(content, error, finish))
        self.closed = False

    def close(self):
        self.closed = True


def png_image():
    canvas = np.zeros((90, 120, 3), np.uint8)
    canvas[:] = (20, 120, 200)
    cv2.putText(canvas, 'TEXT', (5, 50), cv2.FONT_HERSHEY_SIMPLEX, .7, (255, 255, 255), 2)
    ok, encoded = cv2.imencode('.png', canvas)
    assert ok
    return encoded.tobytes()


class VisionEvidenceServiceTests(unittest.TestCase):
    def setUp(self):
        self.session = Session(id='vision-evidence', video_frames=video(),
                               video_filename='受控非实拍.mp4')
        self.revision = assessment(self.session)['revision']

    def test_video_frame_is_sent_with_version_and_result_has_no_base64(self):
        client = _Client()
        result = observe(self.session, kind='video_frame', frame_id=1,
                         video_revision=self.revision, question='画面里有什么？',
                         client=client, model='controlled-vision')
        self.assertTrue(result['ok'])
        self.assertEqual(result['observations'][0]['frame_id'], 1)
        self.assertEqual(result['observations'][0]['frame_number'], 2)
        self.assertEqual(result['observations'][0]['time_sec'], 5.)
        self.assertEqual(result['source']['kind'], 'video_frame')
        self.assertEqual(len(result['source']['sha256']), 64)
        self.assertIn('quality_status', result['source_quality'])
        self.assertFalse(result['cached'])
        self.assertNotIn('base64', json.dumps(result, ensure_ascii=False))
        sent = client.chat.completions.calls[0]
        blocks = sent['messages'][1]['content']
        self.assertTrue(any(block.get('type') == 'image_url' for block in blocks))
        self.assertIn('画面里有什么', json.dumps(sent, ensure_ascii=False))

    def test_duplicate_question_uses_cache_and_new_question_calls_again(self):
        client = _Client()
        first = observe(self.session, kind='video_frame', frame_id=0,
                        video_revision=self.revision, question='看泡沫', client=client,
                        model='controlled-vision')
        second = observe(self.session, kind='video_frame', frame_id=0,
                         video_revision=self.revision, question='看泡沫', client=client,
                         model='controlled-vision')
        self.assertFalse(first['cached'])
        self.assertTrue(second['cached'])
        self.assertEqual(len(client.chat.completions.calls), 1)
        observe(self.session, kind='video_frame', frame_id=0,
                video_revision=self.revision, question='看排口', client=client,
                model='controlled-vision')
        self.assertEqual(len(client.chat.completions.calls), 2)

    def test_identical_pixels_in_different_frames_keep_each_frame_source_and_quality(self):
        # video() 的第 0、2 帧纹理相同；第 2 帧人工排除后仍是独立证据。
        frames = video()
        frames[2]['review'] = {'decision': 'exclude', 'reviewer': '受控复核',
                               'notes': '人工排除本帧'}
        self.session.video_frames = frames
        revision = assessment(self.session)['revision']
        client = _Client()
        first = observe(self.session, kind='video_frame', frame_id=0,
                        video_revision=revision, question='看纹理', client=client,
                        model='controlled-vision')
        second = observe(self.session, kind='video_frame', frame_id=2,
                         video_revision=revision, question='看纹理', client=client,
                         model='controlled-vision')
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertTrue(second['cached'])
        self.assertEqual(first['source']['frame_id'], 0)
        self.assertEqual(second['source']['frame_id'], 2)
        self.assertEqual(second['source']['time_sec'], 10.)
        self.assertEqual(second['observations'][0]['frame_id'], 2)
        self.assertEqual(second['observations'][0]['frame_number'], 3)
        self.assertEqual(second['source_quality']['quality_status'], 'needs_review')
        self.assertIn('manual_exclusion', second['source_quality']['quality_flags'])

    def test_image_asset_is_supported_without_video_fields(self):
        meta = store_media(self.session, png_image(), '受控截图.png')
        client = _Client()
        result = observe(self.session, kind='image_asset', asset_id=meta['asset_id'],
                         asset_revision=meta['sha256'], client=client, model='controlled-vision')
        self.assertEqual(result['source']['kind'], 'image_asset')
        self.assertIsNone(result['observations'][0]['frame_id'])
        self.assertEqual(result['source']['asset_id'], meta['asset_id'])

    def test_sequence_sorts_frames_and_returns_changes_without_claiming_process(self):
        client = _Client(SEQ_VALID)
        result = observe_sequence(self.session, frame_ids=[1, 0],
                                  video_revision=self.revision, question='比较明暗',
                                  client=client, model='controlled-vision')
        self.assertEqual(result['kind'], 'video_frames')
        self.assertEqual(result['frame_ids'], [0, 1])
        self.assertEqual([frame['source']['frame_id'] for frame in result['frames']], [0, 1])
        self.assertEqual([frame['source']['time_sec'] for frame in result['frames']], [0., 5.])
        self.assertFalse(result['static_sequence'])
        self.assertEqual(result['changes'][0]['from'], 1)
        self.assertEqual(result['changes'][0]['to'], 2)
        self.assertIn('不代表连续变化', result['changes'][0]['uncertainty'][0])
        self.assertIn('不能补造', result['limits'][-2])
        sent = client.chat.completions.calls[0]['messages'][1]['content']
        self.assertEqual(sum(block.get('type') == 'image_url' for block in sent), 2)
        self.assertIn('第2张=视频第2帧', sent[0]['text'])

    def test_sequence_rejects_duplicates_insufficient_frames_and_excluded_frames(self):
        client = _Client(SEQ_VALID)
        for frame_ids in ([0, 0], [0], list(range(9))):
            with self.subTest(frame_ids=frame_ids):
                with self.assertRaises(VisionError):
                    observe_sequence(self.session, frame_ids=frame_ids,
                                     video_revision=self.revision, client=client,
                                     model='controlled-vision')
        frames = video()
        frames[2]['review'] = {'decision': 'exclude', 'reviewer': '受控复核',
                               'notes': '人工排除'}
        self.session.video_frames = frames
        revision = assessment(self.session)['revision']
        with self.assertRaises(VisionError):
            observe_sequence(self.session, frame_ids=[0, 2], video_revision=revision,
                             client=client, model='controlled-vision')
        self.assertEqual(client.chat.completions.calls, [])

    def test_static_sequence_is_marked_and_cache_is_reused(self):
        client = _Client(STATIC_VALID)
        first = observe_sequence(self.session, frame_ids=[0, 2],
                                 video_revision=self.revision, client=client,
                                 model='controlled-vision')
        second = observe_sequence(self.session, frame_ids=[2, 0],
                                  video_revision=self.revision, client=client,
                                  model='controlled-vision')
        self.assertTrue(first['static_sequence'])
        self.assertTrue(second['cached'])
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertEqual(second['frame_ids'], [0, 2])

    def test_static_sequence_rejects_model_invented_changes(self):
        client = _Client(SEQ_VALID)
        with self.assertRaises(VisionServiceError):
            observe_sequence(self.session, frame_ids=[0, 2],
                             video_revision=self.revision, client=client,
                             model='controlled-vision')
        self.assertEqual(getattr(self.session, '_vision_evidence_cache', {}), {})

    def test_sequence_cache_includes_frame_labels_and_times(self):
        # 0/1 与 2/3 的有序像素相同，但帧号/时间不同；模型提示中的标签也不同。
        client = _Client(SEQ_VALID)
        first = observe_sequence(self.session, frame_ids=[0, 1],
                                 video_revision=self.revision, client=client,
                                 model='controlled-vision')
        second = observe_sequence(self.session, frame_ids=[2, 3],
                                  video_revision=self.revision, client=client,
                                  model='controlled-vision')
        self.assertFalse(first['cached'])
        self.assertFalse(second['cached'])
        self.assertEqual(len(client.chat.completions.calls), 2)
        self.assertEqual(second['frame_ids'], [2, 3])
        self.assertEqual([frame['source']['time_sec'] for frame in second['frames']], [10., 15.])

    def test_non_monotonic_frame_times_are_rejected(self):
        frames = video()
        frames[0]['time_sec'] = 10.
        frames[1]['time_sec'] = 0.
        self.session.video_frames = frames
        revision = assessment(self.session)['revision']
        with self.assertRaises(VisionError):
            observe_sequence(self.session, frame_ids=[0, 1], video_revision=revision,
                             client=_Client(SEQ_VALID), model='controlled-vision')

    def test_sequence_monitoring_context_requires_confirmed_sync_and_preserves_gaps(self):
        frames = video()
        frames[0]['video_settings'] = {**frames[0]['video_settings'],
            'sync_confirmed': True, 'time_offset_sec': 0., 'time_window_sec': 2.}
        df = pd.DataFrame({'datetime': pd.date_range('2026-01-01', periods=4, freq='5s'),
                           'ammonia_nitrogen': [1., 2., 3., 4.],
                           'dissolved_oxygen': [6., 5., 4., 3.]})
        session = Session(id='monitoring-context', df=df, video_frames=frames,
                          video_filename='受控同步视频.mp4')
        revision = assessment(session)['revision']
        result = observe_sequence(session, frame_ids=[0, 1], video_revision=revision,
                                  client=_Client(SEQ_VALID), model='controlled-vision')
        context = result['monitoring_context']
        self.assertEqual(context['status'], 'matched')
        self.assertTrue(context['sync_confirmed'])
        self.assertEqual(context['time_offset_sec'], 0.)
        self.assertEqual(context['window_sec'], 2.)
        self.assertEqual([item['record_count'] for item in context['frames']], [1, 1])
        self.assertEqual(context['frames'][0]['values']['ammonia_nitrogen']['count'], 1)
        self.assertIn('不自动判定一致', context['notice'])

        frames[0]['video_settings']['sync_confirmed'] = False
        session.video_frames = frames
        revision = assessment(session)['revision']
        unconfirmed = observe_sequence(session, frame_ids=[0, 1], video_revision=revision,
                                       client=_Client(SEQ_VALID), model='controlled-vision')
        self.assertEqual(unconfirmed['monitoring_context']['status'], 'sync_unconfirmed')
        self.assertEqual(unconfirmed['monitoring_context']['frames'], [])

        frames[0]['video_settings'] = {**frames[0]['video_settings'],
            'sync_confirmed': True, 'time_offset_sec': 1000.}
        session.video_frames = frames
        revision = assessment(session)['revision']
        missing = observe_sequence(session, frame_ids=[0, 1], video_revision=revision,
                                   client=_Client(SEQ_VALID), model='controlled-vision')
        self.assertEqual(missing['monitoring_context']['status'], 'no_match')

    def test_sequence_without_monitoring_data_reports_no_monitoring(self):
        result = observe_sequence(self.session, frame_ids=[0, 1],
                                  video_revision=self.revision, client=_Client(SEQ_VALID),
                                  model='controlled-vision')
        self.assertEqual(result['monitoring_context']['status'], 'no_monitoring')
        self.assertEqual(result['monitoring_context']['frames'], [])

    def test_stale_frame_or_asset_is_rejected_before_model_call(self):
        client = _Client()
        with self.assertRaises(VisionError):
            observe(self.session, kind='video_frame', frame_id=0,
                    video_revision='0' * 64, client=client, model='controlled-vision')
        with self.assertRaises(VisionError):
            observe(self.session, kind='video_frame', frame_id=99,
                    video_revision=self.revision, client=client, model='controlled-vision')
        with self.assertRaises(VisionError):
            observe(self.session, kind='image_asset', asset_id='0' * 32,
                    asset_revision='0' * 64, client=client, model='controlled-vision')
        other = Session(id='other-project')
        meta = store_media(other, png_image(), '其他项目截图.png')
        with self.assertRaises(VisionError):
            observe(self.session, kind='image_asset', asset_id=meta['asset_id'],
                    asset_revision=meta['sha256'], client=client, model='controlled-vision')
        with self.assertRaises(VisionError):
            observe(Session(id='empty'), kind='video_frame', frame_id=0,
                    video_revision=self.revision, client=client, model='controlled-vision')
        self.assertEqual(client.chat.completions.calls, [])

    def test_malformed_or_unexpected_model_fields_are_rejected(self):
        for content in (
            'not json',
            json.dumps({'observations': [], 'extra': 'x'}),
            json.dumps({'observations': [{'visible': 'x', 'frame_id': 0}]}),
            json.dumps({'observations': [{'visible': ''}]}),
        ):
            with self.subTest(content=content):
                with self.assertRaises(VisionServiceError):
                    observe(self.session, kind='video_frame', frame_id=0,
                            video_revision=self.revision, client=_Client(content),
                            model='controlled-vision')

    def test_empty_observations_with_reasons_are_accepted_but_incomplete_finish_is_not(self):
        empty = json.dumps({'observations': [], 'not_visible': ['画面没有明确对象'],
                            'quality_notes': []}, ensure_ascii=False)
        result = observe(self.session, kind='video_frame', frame_id=0,
                         video_revision=self.revision, client=_Client(empty),
                         model='controlled-vision')
        self.assertEqual(result['observations'], [])
        self.assertEqual(result['not_visible'], ['画面没有明确对象'])
        for finish in ('length', None):
            with self.subTest(finish=finish):
                with self.assertRaises(VisionServiceError):
                    observe(self.session, kind='video_frame', frame_id=1,
                            video_revision=self.revision, client=_Client(finish=finish),
                            model='controlled-vision')

    def test_completely_empty_single_result_is_rejected_and_not_cached(self):
        client = _Client(json.dumps({'observations': [], 'not_visible': [],
                                     'quality_notes': []}, ensure_ascii=False))
        with self.assertRaises(VisionServiceError):
            observe(self.session, kind='video_frame', frame_id=0,
                    video_revision=self.revision, client=client,
                    model='controlled-vision')
        self.assertEqual(len(client.chat.completions.calls), 1)
        self.assertEqual(getattr(self.session, '_vision_evidence_cache', {}), {})

    def test_scope_change_during_model_call_is_rejected_and_not_cached(self):
        class MutatingClient(_Client):
            def __init__(inner):
                super().__init__()
                inner.chat = NS(completions=inner)
                inner.calls = 0

            def create(inner, **kwargs):
                inner.calls += 1
                self.session.project_id = 'b' * 32
                self.session.analysis_revision += 1
                self.session.replace_video(video(), '替换后的受控视频.mp4')
                return NS(choices=[NS(message=NS(content=VALID), finish_reason='stop')])

        client = MutatingClient()
        with self.assertRaises(VisionServiceError) as caught:
            observe(self.session, kind='video_frame', frame_id=0,
                    video_revision=self.revision, client=client,
                    model='controlled-vision')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(client.calls, 1)
        self.assertEqual(getattr(self.session, '_vision_evidence_cache', {}), {})

    def test_service_exception_text_is_not_exposed(self):
        client = _Client(error=RuntimeError('network token=SECRET-TOKEN'))
        with self.assertRaises(VisionServiceError) as caught:
            observe(self.session, kind='video_frame', frame_id=0,
                    video_revision=self.revision, client=client, model='controlled-vision')
        self.assertNotIn('SECRET-TOKEN', str(caught.exception))
        self.assertEqual(caught.exception.status, 502)


class VisionEvidenceApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.session = STORE.get(self.sid)
        self.session.video_frames = video()
        self.session.video_filename = '受控非实拍.mp4'
        self.revision = assessment(self.session)['revision']

    def tearDown(self):
        self.client.close()
        STORE.drop(self.sid)

    def post(self, body):
        return self.client.post('/api/vision/observe', json=body, headers=self.headers)

    def test_api_returns_structured_result_and_does_not_echo_image(self):
        fake = _Client()
        with patch('backend.routers.vision.resolve_api_key', return_value='test-key'), \
             patch('backend.vision_evidence.OpenAI', return_value=fake):
            response = self.post({'kind': 'video_frame', 'frame_id': 1,
                                  'video_revision': self.revision, 'question': '看画面'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['source']['frame_id'], 1)
        self.assertNotIn('base64', response.text)
        self.assertNotIn('test-key', response.text)
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_api_accepts_short_sequence_and_rejects_duplicate_frames(self):
        fake = _Client(SEQ_VALID)
        with patch('backend.routers.vision.resolve_api_key', return_value='test-key'), \
             patch('backend.vision_evidence.OpenAI', return_value=fake):
            response = self.post({'kind': 'video_frames', 'frame_ids': [0, 1],
                                  'video_revision': self.revision})
            duplicate = self.post({'kind': 'video_frames', 'frame_ids': [0, 0],
                                   'video_revision': self.revision})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['frame_ids'], [0, 1])
        self.assertEqual(len(response.json()['frames']), 2)
        self.assertNotIn('base64', response.text)
        self.assertEqual(duplicate.status_code, 422)
        self.assertEqual(len(fake.chat.completions.calls), 1)

    def test_missing_key_is_clear_and_does_not_call_model(self):
        with patch('backend.routers.vision.resolve_api_key', return_value=''), \
             patch('backend.vision_evidence.OpenAI') as factory:
            response = self.post({'kind': 'video_frame', 'frame_id': 0,
                                  'video_revision': self.revision})
        self.assertEqual(response.status_code, 400)
        self.assertIn('DeepSeek API Key', response.json()['detail'])
        factory.assert_not_called()

    def test_request_source_fields_must_match_kind(self):
        cases = [
            {'kind': 'video_frame', 'video_revision': self.revision},
            {'kind': 'video_frame', 'frame_id': 0},
            {'kind': 'image_asset', 'asset_id': '0' * 32},
            {'kind': 'video_frame', 'frame_id': 0, 'video_revision': self.revision,
             'asset_id': '0' * 32},
        ]
        with patch('backend.routers.vision.resolve_api_key', return_value='test-key'):
            for body in cases:
                with self.subTest(body=body):
                    self.assertEqual(self.post(body).status_code, 422)

    def test_stale_video_revision_is_rejected_without_model_call(self):
        fake = _Client()
        with patch('backend.routers.vision.resolve_api_key', return_value='test-key'), \
             patch('backend.vision_evidence.OpenAI', return_value=fake):
            response = self.post({'kind': 'video_frame', 'frame_id': 0,
                                  'video_revision': '0' * 64})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(fake.chat.completions.calls, [])


if __name__ == '__main__':
    unittest.main()
