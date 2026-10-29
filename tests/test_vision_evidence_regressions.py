"""Q18 阶段 A 视觉观察：受控模型替身，不访问外部服务。"""
from __future__ import annotations

import base64
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.main import app
from backend.media_assets import store_media
from backend.routers.video import assessment
from backend.session import STORE, Session
from backend.vision import VisionError
from backend.vision_evidence import VisionServiceError, observe
from test_video_quality_regressions import video


VALID = json.dumps({
    'observations': [
        {'visible': '水面右侧有一条浅色泡沫带', 'where': '画面右侧近岸',
         'uncertainty': ['无法判断是否持续存在']},
    ],
    'not_visible': ['没有看到明确的排口'],
    'quality_notes': ['画面存在轻微反光'],
}, ensure_ascii=False)


class _Completions:
    def __init__(self, content=VALID, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return NS(choices=[NS(message=NS(content=self.content))])


class _Client:
    def __init__(self, content=VALID, error=None):
        self.chat = NS(completions=_Completions(content, error))
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

    def test_image_asset_is_supported_without_video_fields(self):
        meta = store_media(self.session, png_image(), '受控截图.png')
        client = _Client()
        result = observe(self.session, kind='image_asset', asset_id=meta['asset_id'],
                         asset_revision=meta['sha256'], client=client, model='controlled-vision')
        self.assertEqual(result['source']['kind'], 'image_asset')
        self.assertIsNone(result['observations'][0]['frame_id'])
        self.assertEqual(result['source']['asset_id'], meta['asset_id'])

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
