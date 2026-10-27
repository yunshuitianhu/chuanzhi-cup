"""Q18 图片接入、只读业务数据、媒体保存与表达规范的受控回归。

所有模型与外部服务均为受控替身；不代表在线模型质量或现场准确率。
"""
import base64
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE, Session
from backend import project_data
from backend.agent.tools import execute_tool, SYSTEM_PROMPT_AUTO, SYSTEM_PROMPT_CHAT
from backend.agent.loop import run_agent
from backend.agent.progress import AgentRun
from backend.agent.response_style import RESPONSE_STYLE
from backend.media_assets import referenced, store_media
from backend.project_codec import dump_project, load_project, ProjectError
from backend.projects import apply_snapshot, repository
from backend.question_context import create_context, resolve_context, ImageAssetContext
from backend.routers.video import assessment
from backend.side_chat import validate_threads


def image(color=(20, 120, 200), size=(80, 120), text='TEXT'):
    canvas = np.zeros((size[1], size[0], 3), np.uint8)
    canvas[:] = color
    if text:
        cv2.putText(canvas, text, (4, size[1] // 2), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
    ok, encoded = cv2.imencode('.png', canvas)
    assert ok
    return encoded.tobytes()


def video():
    from test_video_quality_regressions import video as fixture
    return fixture()


def frame_png():
    from test_video_quality_regressions import frame, texture
    return frame(texture(), 5)


def monitoring(n=30):
    return pd.DataFrame({
        'datetime': pd.date_range('2026-10-26 09:00', periods=n, freq='10s'),
        'latitude': 23 + np.linspace(0, .01, n), 'longitude': 113 + np.linspace(0, .01, n),
        'ammonia_nitrogen': np.linspace(1, 30, n), 'dissolved_oxygen': np.linspace(7, 2, n),
        'turbidity': np.full(n, 9.), 'ph': np.full(n, 7.)})


def chunk(text=None, finish='stop', calls=None):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=calls), finish_reason=finish)])


def tool_call(index, ident, name, args):
    return NS(index=index, id=ident,
              function=NS(name=name, arguments=json.dumps(args, ensure_ascii=False)))


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.session = STORE.get(self.sid)

    def tearDown(self):
        STORE.drop(self.sid)
        self.client.close()

    def upload(self, data=None, name='截图.png', media='image/png'):
        return self.client.post('/api/media', headers=self.headers,
            files={'file': (name, data if data is not None else image(), media)})

    def card(self, meta):
        return self.client.post('/api/agent/context', headers=self.headers, json={
            'kind': 'image_asset', 'asset_id': meta['asset_id'], 'asset_revision': meta['sha256'],
            'project_id': self.session.project_id or None,
            'analysis_revision': self.session.analysis_revision,
            'results_revision': self.session.results_revision})

    def test_upload_decodes_actual_content_and_rejects_fake_extension(self):
        response = self.upload(name='伪装.png', data=b'<html>not an image</html>', media='image/png')
        self.assertEqual(response.status_code, 422)
        response = self.upload(data=image(), name='ok.png')
        self.assertEqual(response.status_code, 200, response.text)
        meta = response.json()
        self.assertRegex(meta['asset_id'], r'^[0-9a-f]{32}$')
        self.assertRegex(meta['sha256'], r'^[0-9a-f]{64}$')
        preview = self.client.get('/api/media/' + meta['asset_id'], headers=self.headers)
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.headers['content-type'], meta['media_type'])
        self.assertEqual(len(preview.content), meta['bytes'])
        self.assertEqual(self.client.get('/api/media/' + meta['asset_id']).status_code, 404)


    def test_declared_pixel_bomb_is_rejected_before_full_decode(self):
        import struct, zlib
        data = struct.pack('>IIBBBBB', 60000, 60000, 8, 2, 0, 0, 0)
        chunk = struct.pack('>I', 13) + b'IHDR' + data + struct.pack('>I', zlib.crc32(b'IHDR' + data) & 0xffffffff)
        signature = b'\x89PNG\r\n\x1a\n'
        response = self.upload(name='huge.png', data=signature + chunk, media='image/png')
        self.assertEqual(response.status_code, 422)
        self.assertIn('像素', response.json()['detail'])

    def test_image_context_binds_hash_and_rejects_other_version_or_session(self):
        meta = self.upload().json()
        response = self.card(meta)
        self.assertEqual(response.status_code, 200, response.text)
        card = response.json()
        self.assertEqual(card['kind'], 'image_asset')
        self.assertTrue(card['title'])
        self.assertNotIn('base64', json.dumps(card, ensure_ascii=False))
        resolved = resolve_context(self.session, card['id'])
        self.assertEqual(resolved['public']['selection']['asset_revision'], meta['sha256'])
        bad = self.client.post('/api/agent/context', headers=self.headers, json={
            'kind': 'image_asset', 'asset_id': meta['asset_id'], 'asset_revision': 'f' * 64,
            'project_id': self.session.project_id or None,
            'analysis_revision': self.session.analysis_revision,
            'results_revision': self.session.results_revision})
        self.assertEqual(bad.status_code, 422)
        other = STORE.get(self.client.get('/api/session').json()['session_id'])
        with self.assertRaises(Exception):
            resolve_context(other, card['id'])
        STORE.drop(other.id)

    def test_unused_temporary_selection_can_be_deleted_without_touching_history(self):
        meta = self.upload().json()
        card = self.card(meta).json()
        self.assertEqual(self.client.delete('/api/media/' + meta['asset_id'], headers=self.headers).status_code, 200)
        self.assertNotIn(meta['asset_id'], self.session.media_assets)
        self.assertEqual(self.client.get('/api/media/' + meta['asset_id'], headers=self.headers).status_code, 404)
        with self.assertRaises(Exception):
            resolve_context(self.session, card['id'])

    def test_history_context_vision_and_side_quote_protect_media(self):
        meta = self.upload().json()
        card = self.card(meta).json()
        self.session.messages.append({'role': 'user', 'content': '解释', 'question_context': card})
        self.assertEqual(self.client.delete('/api/media/' + meta['asset_id'], headers=self.headers).status_code, 409)

        tool = self.upload().json()
        self.session.messages.append({'role': 'assistant', 'content': '看图',
            'vision': {'sent': True, 'images': [{'asset_id': tool['asset_id'], 'sha256': tool['sha256']}]}})
        self.assertEqual(self.client.delete('/api/media/' + tool['asset_id'], headers=self.headers).status_code, 409)

        side = self.upload().json()
        self.session.side_threads.append({'id': 'a' * 32, 'quote': {
            'id': 'b' * 32, 'kind': 'image_asset', 'title': '图片', 'summary': '图',
            'notice': '说明', 'source_url': '/chat', 'scope': {},
            'selection': {'asset_id': side['asset_id'], 'asset_revision': side['sha256']}},
            'messages': [], 'requests': [], 'updated_at': '2026-10-27'})
        self.assertEqual(self.client.delete('/api/media/' + side['asset_id'], headers=self.headers).status_code, 409)

    def test_media_roundtrip_and_old_schema_without_media(self):
        meta = self.upload().json()
        self.session.messages.append({'role': 'assistant', 'content': '保留'})
        raw = dump_project(self.session, '图片项目')
        snapshot = load_project(raw)
        self.assertEqual(list(snapshot.media), [meta['asset_id']])
        restored = Session('restored')
        apply_snapshot(restored, {'id': 'a' * 32, 'revision': 1}, snapshot)
        self.assertEqual(restored._media_bytes[meta['asset_id']], self.session._media_bytes[meta['asset_id']])
        # schema 3 旧包没有图片字段，仍按空附件迁移。
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        state = json.loads(entries['state.json'])
        state['items'] = [item for item in state['items'] if item[0] != 'media_assets']
        entries['state.json'] = json.dumps(state, ensure_ascii=False).encode()
        entries = {name: value for name, value in entries.items() if not name.startswith('media/')}
        from hashlib import sha256
        manifest = json.loads(entries['manifest.json'])
        manifest['schema_version'] = 3
        manifest['contents'] = {name: {'bytes': len(value), 'sha256': sha256(value).hexdigest()}
                                for name, value in entries.items() if name != 'manifest.json'}
        entries['manifest.json'] = json.dumps(manifest).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        old = load_project(out.getvalue())
        self.assertEqual(old.state['media_assets'], {})
        self.assertEqual(old.media, {})


    def test_media_reference_survives_project_roundtrip(self):
        meta = self.upload().json()
        self.session.messages.append({'role': 'assistant', 'content': '看图',
            'vision': {'sent': True, 'images': [{'asset_id': meta['asset_id'],
                                                 'sha256': meta['sha256']}]}})
        snapshot = load_project(dump_project(self.session, '媒体引用'))
        restored = Session('restored-ref')
        apply_snapshot(restored, {'id': 'a' * 32, 'revision': 1}, snapshot)
        self.assertTrue(referenced(restored, meta['asset_id']))

    def test_tampered_media_entry_is_rejected(self):
        self.upload().json()
        raw = dump_project(self.session, '图片项目')
        import io, zipfile
        from hashlib import sha256
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        key = next(name for name in entries if name.startswith('media/'))
        entries[key] = b'not image'
        manifest = json.loads(entries['manifest.json'])
        manifest['contents'][key] = {'bytes': len(entries[key]), 'sha256': sha256(entries[key]).hexdigest()}
        entries['manifest.json'] = json.dumps(manifest).encode()
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            for name, value in entries.items():
                archive.writestr(name, value)
        with self.assertRaises(ProjectError):
            load_project(out.getvalue())


class MediaProjectApiTests(unittest.TestCase):
    def test_export_import_restores_media_into_independent_session(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {'WQ_PROJECT_HOME': temporary}):
            client = TestClient(app)
            sid = client.get('/api/session').json()['session_id']
            headers = {'X-Session-Id': sid}
            try:
                meta = client.post('/api/media', headers=headers,
                    files={'file': ('截图.png', image(), 'image/png')}).json()
                created = client.post('/api/projects', headers=headers,
                    json={'name': '媒体项目', 'from_current': True})
                self.assertEqual(created.status_code, 200, created.text)
                project_id = created.json()['current']['id']
                exported = client.post('/api/projects/' + project_id + '/export', headers=headers, json={})
                self.assertEqual(exported.status_code, 200, exported.text)
                other = client.get('/api/session').json()['session_id']
                other_headers = {'X-Session-Id': other}
                imported = client.post('/api/projects/import', headers=other_headers,
                    files={'file': ('媒体项目.wqproj', exported.content,
                                    'application/octet-stream')},
                    data={'river_form': 'null'})
                self.assertEqual(imported.status_code, 200, imported.text)
                restored = STORE.get(other)
                self.assertIn(meta['asset_id'], restored.media_assets)
                preview = client.get('/api/media/' + meta['asset_id'], headers=other_headers)
                self.assertEqual(preview.status_code, 200)
                self.assertEqual(len(preview.content), meta['bytes'])
                STORE.drop(other)
            finally:
                STORE.drop(sid)
                client.close()


class VisionLoopTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

    def make_session(self, with_video=False):
        session = Session('vision', df=monitoring(8), data_filename='受控.csv')
        if with_video:
            session.video_frames = video()
            session.video_filename = '受控.mp4'
        return session

    def chat(self, session, context_id, responses, message='这张图是什么？', before=None):
        run = AgentRun(session.analysis_revision)
        if context_id is not None:
            run.question_context = resolve_context(session, context_id)
        if before is not None:
            before()
        def create(**kwargs):
            self.calls.append(deepcopy(kwargs['messages']))
            return iter(responses[len(self.calls) - 1])
        with patch('backend.agent.loop.resolve_api_key', return_value='mock-only'), \
             patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect = create
            events = list(run_agent(session, message, run=run))
        return events

    def test_selected_video_frame_is_sent_as_real_image_and_not_stored_inline(self):
        session = self.make_session(with_video=True)
        current = assessment(session)
        card = create_context(session, __import__('backend.question_context', fromlist=['VideoContext']).VideoContext(
            kind='video_frame', frame_id=1, video_revision=current['revision'],
            project_id=None, analysis_revision=session.analysis_revision, results_revision=session.results_revision))
        events = self.chat(session, card['id'], [[chunk('第2帧能看到水面对比纹理。')]])
        self.assertIn('"status": "succeeded"', events[-1])
        image_message = next(message for message in self.calls[0] if isinstance(message.get('content'), list))
        image_part = next(part for part in image_message['content'] if part['type'] == 'image_url')
        self.assertTrue(image_part['image_url']['url'].startswith('data:image/jpeg;base64,'))
        sent = base64.b64decode(image_part['image_url']['url'].split(',', 1)[1])
        self.assertEqual(session.messages[-1]['vision']['images'][0]['sha256'],
                         __import__('hashlib').sha256(sent).hexdigest())
        self.assertIn('第2帧', session.messages[-1]['vision']['images'][0]['label'])
        self.assertNotIn('image_url', json.dumps(session.full_messages, ensure_ascii=False))
        self.assertIn('历史轮次不重复内嵌', json.dumps(session.full_messages, ensure_ascii=False))

    def test_image_asset_is_sent_and_recorded_without_base64_in_history(self):
        session = self.make_session()
        meta = store_media(session, image(), '截图.png')
        card = create_context(session, ImageAssetContext(kind='image_asset', asset_id=meta['asset_id'],
            asset_revision=meta['sha256'], project_id=None,
            analysis_revision=session.analysis_revision, results_revision=session.results_revision))
        events = self.chat(session, card['id'], [[chunk('图片里是蓝色背景和白色文字。')]])
        self.assertIn('event: vision', '\n'.join(events))
        self.assertIn('"status": "sent"', '\n'.join(events))
        self.assertTrue(session.messages[-1]['vision']['sent'])
        self.assertNotIn('base64', json.dumps(session.messages[-1]['vision'], ensure_ascii=False))

    def test_failed_image_is_reported_and_never_claimed_as_seen(self):
        session = self.make_session()
        meta = store_media(session, image(), '截图.png')
        card = create_context(session, ImageAssetContext(kind='image_asset', asset_id=meta['asset_id'],
            asset_revision=meta['sha256'], project_id=None,
            analysis_revision=session.analysis_revision, results_revision=session.results_revision))
        events = self.chat(session, card['id'], [[chunk('本轮没有读到图片，我只看到记录信息。')]],
            before=session._media_bytes.clear)
        joined = '\n'.join(events)
        self.assertIn('"status": "failed"', joined)
        self.assertIn('不得声称', self.calls[0][0]['content'])
        self.assertFalse(session.messages[-1]['vision']['sent'])
        self.assertIn('图片', session.messages[-1]['vision']['errors'][0])

    def test_tool_image_waits_until_all_tool_results_and_uses_display_number(self):
        session = self.make_session(with_video=True)
        revision = assessment(session)['revision']
        calls = [tool_call(0, 'call_a', 'inspect_visual_asset', {'frame_number': 2, 'video_revision': revision}),
                 tool_call(1, 'call_b', 'inspect_visual_asset', {'frame_id': 0, 'video_revision': revision})]
        responses = [[chunk(finish='tool_calls', calls=calls)], [chunk('两帧都是受控纹理。')]]
        self.chat(session, None, responses, message='第2帧和第1帧有什么不同？')
        messages = self.calls[1]
        tool_indexes = [index for index, message in enumerate(messages) if message['role'] == 'tool']
        image_index = next(index for index, message in enumerate(messages)
                           if isinstance(message.get('content'), list))
        self.assertGreater(image_index, max(tool_indexes))
        parts = messages[image_index]['content']
        self.assertEqual(sum(part['type'] == 'image_url' for part in parts), 2)
        self.assertIn('视频第2帧', json.dumps(parts[0], ensure_ascii=False))
        self.assertEqual(len(session.messages[-1]['vision']['images']), 2)


    def test_image_cap_failure_marks_tool_failed_and_round_partial(self):
        session = self.make_session(with_video=True)
        revision = assessment(session)['revision']
        calls = [tool_call(index, 'call_%d' % index, 'inspect_visual_asset',
                           {'frame_id': 0, 'video_revision': revision}) for index in range(5)]
        responses = [[chunk(finish='tool_calls', calls=calls)], [chunk('只发送了前四张图。')]]
        events = self.chat(session, None, responses, message='连续看五次同一帧。')
        done = json.loads(events[-1].split('data: ', 1)[1])
        self.assertEqual(done['status'], 'partial')
        self.assertEqual(done['tool_errors'], 1)
        statuses = []
        for event in events:
            if event.startswith('event: tool_done'):
                statuses.append(json.loads(event.split('data: ', 1)[1])['status'])
        self.assertEqual(statuses.count('succeeded'), 4)
        self.assertEqual(statuses.count('failed'), 1)
        self.assertEqual(session.messages[-1]['vision']['sent'], True)
        self.assertTrue(session.messages[-1]['vision'].get('errors'))
        self.assertTrue(session.messages[-1]['incomplete'])


    def test_plain_text_chat_with_no_monitoring_data_succeeds(self):
        session = Session('empty-chat')
        run = AgentRun(session.analysis_revision)
        calls = []
        def create(**kwargs):
            calls.append(deepcopy(kwargs['messages']))
            return iter([chunk('溶解氧是水里可供生物利用的氧。')])
        with patch('backend.agent.loop.resolve_api_key', return_value='mock-only'),              patch('backend.agent.loop.OpenAI') as factory:
            factory.return_value.chat.completions.create.side_effect = create
            events = list(run_agent(session, '什么是溶解氧？', run=run))
        self.assertIn('"status": "succeeded"', events[-1])
        self.assertFalse(session.messages[0].get('question_context'))
        self.assertIn('什么是溶解氧？', calls[0][-1]['content'])
        self.assertIn('没有监测表', calls[0][0]['content'])


class ProjectDataToolTests(unittest.TestCase):
    def session(self):
        session = Session('data', df=monitoring(30), data_filename='受控.csv')
        session.df.attrs['data_identity'] = {'kind': 'synthetic'}
        return session

    def test_catalog_and_read_statistics_are_server_side_and_paginated(self):
        session = self.session()
        text, failed = execute_tool('get_project_data_catalog', {}, session)
        self.assertFalse(failed)
        catalog = json.loads(text)['data']
        self.assertEqual(catalog['monitoring']['rows'], 30)
        self.assertEqual(catalog['versions']['analysis_revision'], session.analysis_revision)
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
            'indicator': 'ammonia_nitrogen', 'min_value': 5, 'limit': 5}, session)
        self.assertFalse(failed)
        payload = json.loads(text)['data']
        self.assertEqual(payload['total_count'], 26)
        self.assertEqual(payload['returned_count'], 5)
        self.assertTrue(payload['truncated'])
        self.assertEqual(payload['statistics']['minimum'], 5.0)
        self.assertEqual(payload['statistics']['maximum'], 30.0)
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
            'limit': 5, 'offset': 25}, session)
        payload = json.loads(text)['data']
        self.assertEqual(payload['returned_count'], 5)
        self.assertFalse(payload['truncated'])
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records', 'limit': 101}, session)
        self.assertTrue(failed)
        self.assertIn('limit', json.loads(text)['error'])


    def test_indicator_statistics_keep_missing_and_interpolated_in_denominator(self):
        session = Session('missing')
        session.df = pd.DataFrame({
            'datetime': pd.date_range('2026-10-26 09:00', periods=3, freq='10s'),
            'ammonia_nitrogen': [5.0, np.nan, 9.0],
            '__observed_ammonia_nitrogen': [True, True, False],
            'dissolved_oxygen': [7.0, 6.0, 5.0]})
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
            'indicator': 'ammonia_nitrogen', 'limit': 20}, session)
        self.assertFalse(failed)
        data = json.loads(text)['data']
        self.assertEqual(data['total_count'], 3)
        self.assertEqual(data['returned_count'], 3)
        stats = data['statistics']
        self.assertEqual(stats['scope_records'], 3)
        self.assertEqual(stats['valid_candidates'], 1)
        self.assertEqual(stats['valid_observations'], 1)
        self.assertEqual(stats['missing_or_invalid'], 2)
        self.assertIsNone(data['records'][1]['values']['ammonia_nitrogen'])
        self.assertEqual(data['records'][2]['values']['ammonia_nitrogen'], None)
        self.assertEqual(data['records'][2]['interpolated']['ammonia_nitrogen'], 9.0)

        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
            'indicator': 'ammonia_nitrogen', 'min_value': 5, 'limit': 20}, session)
        data = json.loads(text)['data']
        self.assertEqual(data['total_count'], 1)
        self.assertEqual(data['statistics']['scope_records'], 3)
        self.assertEqual(data['statistics']['valid_candidates'], 1)
        self.assertEqual(data['statistics']['excluded_missing_or_invalid'], 2)
        self.assertEqual(data['statistics']['excluded_by_value_filter'], 0)
        self.assertEqual(data['statistics']['missing_or_invalid'], 0)

        session.df['ammonia_nitrogen'] = [None, None, None]
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
            'indicator': 'ammonia_nitrogen'}, session)
        stats = json.loads(text)['data']['statistics']
        self.assertEqual(stats['records'], 3)
        self.assertEqual(stats['valid_observations'], 0)
        self.assertEqual(stats['missing_or_invalid'], 3)
        self.assertIsNone(stats['minimum'])

    def test_no_result_is_not_reported_as_tool_failure(self):
        session = Session('empty')
        text, failed = execute_tool('read_project_data', {'category': 'monitoring_records'}, session)
        self.assertFalse(failed)
        self.assertEqual(json.loads(text)['data']['status'], 'no_result')
        text, failed = execute_tool('read_project_data', {'category': 'black_spots'}, session)
        self.assertFalse(failed)
        self.assertEqual(json.loads(text)['data']['status'], 'no_result')

    def test_cross_project_read_is_read_only_and_version_checked(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {'WQ_PROJECT_HOME': temporary}):
            saved = self.session()
            info = repository().write(saved, '另一个项目', new=True)
            current = Session('current', df=monitoring(2), data_filename='当前.csv')
            text, failed = execute_tool('get_project_data_catalog', {'project_id': info['id']}, current)
            self.assertFalse(failed)
            catalog = json.loads(text)['data']
            self.assertEqual(catalog['source']['kind'], 'saved_project')
            self.assertEqual(catalog['monitoring']['rows'], 30)
            text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
                'project_id': info['id'], 'limit': 3}, current)
            self.assertFalse(failed)
            self.assertEqual(json.loads(text)['data']['returned_count'], 3)
            self.assertEqual(len(current.df), 2)
            self.assertEqual(current.project_id, '')
            text, failed = execute_tool('read_project_data', {'category': 'monitoring_records',
                'project_id': info['id'], 'expected_analysis_revision': 99}, current)
            self.assertTrue(failed)
            self.assertIn('版本', json.loads(text)['error'])


    def test_analysis_summary_is_paged_and_reports_include_excerpts(self):
        session = Session('results')
        session.cross_validation = {'状态': '受控'}
        session.comprehensive_summary = 'A' * 9000
        text, failed = execute_tool('read_project_data', {'category': 'analysis_results',
            'offset': 0}, session)
        data = json.loads(text)['data']
        self.assertFalse(failed)
        self.assertEqual(len(data['comprehensive_summary']), 4000)
        self.assertEqual(data['comprehensive_next_offset'], 4000)
        self.assertTrue(data['comprehensive_truncated'])
        text, failed = execute_tool('read_project_data', {'category': 'analysis_results',
            'offset': 8000}, session)
        data = json.loads(text)['data']
        self.assertEqual(len(data['comprehensive_summary']), 1000)
        self.assertIsNone(data['comprehensive_next_offset'])

        session.messages.append({'role': 'assistant', 'content': '报告正文' * 1200,
            'evidence': {'id': 'bundle-1', 'created_at': '2026-10-27',
                'facts': [{'label': '记录总数', 'value': 3, 'unit': '条'}],
                'references': [{'title': '受控案例'}],
                'analysis_cards': {'cards': [{}, {}]}}})
        text, failed = execute_tool('read_project_data', {'category': 'reports'}, session)
        reports = json.loads(text)['data']['reports']
        self.assertFalse(failed)
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0]['fact_count'], 1)
        self.assertEqual(reports[0]['reference_count'], 1)
        self.assertEqual(reports[0]['card_count'], 2)
        self.assertEqual(len(reports[0]['excerpt']), 4000)
        self.assertTrue(reports[0]['excerpt_truncated'])

    def test_river_model_reads_existing_hydro_and_wq_result_summaries(self):
        from test_projects_regressions import sample_session
        session = sample_session()
        text, failed = execute_tool('read_project_data', {'category': 'river_model'}, session)
        self.assertFalse(failed)
        model = json.loads(text)['data']['model']
        self.assertTrue(model['hydro_result']['present'])
        self.assertTrue(model['wq_result']['present'])
        self.assertIn('water_level', model['hydro_result']['fields'])
        self.assertIn('ammonia', model['wq_result']['fields'])
        self.assertLessEqual(len(model['hydro_result']['fields']['discharge']['last_profile']['chainage_m']), 20)
        self.assertFalse(model['inverse_available'])


class SideImageTests(unittest.TestCase):
    def test_side_thread_accepts_image_selection_and_validates_codec(self):
        client = TestClient(app)
        sid = client.get('/api/session').json()['session_id']
        headers = {'X-Session-Id': sid}
        session = STORE.get(sid)
        try:
            meta = client.post('/api/media', headers=headers,
                files={'file': ('截图.png', image(), 'image/png')}).json()
            card = client.post('/api/agent/context', headers=headers, json={
                'kind': 'image_asset', 'asset_id': meta['asset_id'], 'asset_revision': meta['sha256'],
                'project_id': None, 'analysis_revision': session.analysis_revision,
                'results_revision': session.results_revision}).json()
            thread = client.post('/api/side/threads', headers=headers, json={'context_id': card['id']}).json()
            calls = []
            def create(**kwargs):
                calls.append(deepcopy(kwargs['messages']))
                return iter([chunk('图片里是蓝色背景。')])
            with patch('backend.side_chat.resolve_api_key', return_value='mock'), \
                 patch('backend.side_chat.OpenAI') as factory:
                factory.return_value.chat.completions.create.side_effect = create
                response = client.post('/api/side/threads/' + thread['id'] + '/chat', headers=headers,
                    json={'message': '图里是什么？', 'request_id': str(__import__('uuid').uuid4())})
            self.assertIn('event: vision', response.text)
            self.assertTrue(any(isinstance(message.get('content'), list) for message in calls[0]))
            validate_threads(session.side_threads)
            snapshot = load_project(dump_project(session, '侧边图片'))
            validate_threads(snapshot.state['side_threads'])
            bad = deepcopy(session.side_threads)
            bad[0]['quote']['selection'] = {'asset_id': meta['asset_id'], 'asset_revision': meta['sha256'], 'extra': 1}
            with self.assertRaises(ValueError):
                validate_threads(bad)
        finally:
            STORE.drop(sid)
            client.close()


class ResponseStyleTests(unittest.TestCase):
    def test_style_is_shared_by_chat_auto_and_report(self):
        self.assertIn(RESPONSE_STYLE, SYSTEM_PROMPT_CHAT)
        self.assertIn(RESPONSE_STYLE, SYSTEM_PROMPT_AUTO)
        self.assertIn('不调用 generate_final_report', SYSTEM_PROMPT_CHAT)
        self.assertIn('研判卡片（可选）', SYSTEM_PROMPT_CHAT)
        from water_quality_mcp.src.water_quality_mcp.report import generate_report, REPORT_STYLE
        captured = {}
        response = NS(choices=[NS(finish_reason='stop', message=NS(content='先给结果，再给依据。'))])
        with patch('water_quality_mcp.src.water_quality_mcp.report._try_retrieve_treatment_cases', return_value=''), \
             patch('water_quality_mcp.src.water_quality_mcp.report.OpenAI') as factory:
            factory.return_value.__enter__.return_value.chat.completions.create.return_value = response
            report = generate_report('mock', '{}')
        prompt = factory.return_value.__enter__.return_value.chat.completions.create.call_args.kwargs['messages']
        captured['prompt'] = prompt
        self.assertIn(REPORT_STYLE, prompt[1]['content'])
        self.assertTrue(report.startswith('> 数据身份：'))
        self.assertIn('## 数据与依据（服务端核对）', report)
        self.assertLess(report.index('先给结果'), report.index('## 数据与依据'))


if __name__ == '__main__':
    unittest.main()
