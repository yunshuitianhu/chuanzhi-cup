"""N01—N03 示例文件、目录校验、载入接口、项目保护和交付过滤的受控回归。

示例是程序生成的仿真数据；测试不调用真实模型或外部地图服务，不把示例
当作现场精度证据。项目写入使用临时 WQ_PROJECT_HOME，不触碰用户项目。
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE, SessionStore
from backend.projects import repository
from backend import sample_catalog
from scripts.package_delivery import source_allowed
from water_quality_mcp.src.water_quality_mcp.loaders import load_data

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'data' / 'samples'
MANIFEST = SAMPLES / 'manifest.json'


def monitoring(n=5):
    return pd.DataFrame({'datetime': pd.date_range('2026-01-01', periods=n, freq='5s'),
                         'ammonia_nitrogen': [.2] * n, 'dissolved_oxygen': [7.] * n})


def manifest_payload():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


class SampleFileTests(unittest.TestCase):
    def test_manifest_lists_six_synthetic_csvs_without_private_paths(self):
        payload = sample_catalog.list_samples()
        self.assertEqual(payload['manifest_version'], 1)
        self.assertEqual(payload['data_kind'], 'synthetic')
        self.assertEqual([item['id'] for item in payload['samples']],
                         ['S01', 'S02', 'S03', 'S04', 'S05', 'S06'])
        raw_manifest = manifest_payload()
        for item, raw in zip(payload['samples'], raw_manifest['samples']):
            self.assertTrue(raw['file'].endswith('.csv'))
            self.assertIn('仿真', raw['file'])
            self.assertNotIn(':\\\\', item['source_reference'])
            self.assertNotIn('file', item)
            self.assertFalse(item['has_video'])

    def test_csv_preserves_raw_excel_order_time_coordinates_zeros_and_missing(self):
        for item in manifest_payload()['samples']:
            source = ROOT / '仿真数据' / item['source_reference'].split(' · ')[0].split('/', 1)[1]
            if not source.is_file():
                self.skipTest('开发侧原始 Excel 未随交付源码提供；本机已另行做过原始对照')
            raw = pd.read_excel(source, sheet_name='仿真走航数据', dtype=object)
            csv = pd.read_csv(SAMPLES / item['file'])
            self.assertEqual(list(raw.columns), list(csv.columns))
            self.assertEqual(str(raw['时间'].iloc[0]), str(csv['时间'].iloc[0]))
            self.assertEqual(str(raw['时间'].iloc[-1]), str(csv['时间'].iloc[-1]))
            self.assertEqual(len(raw), len(csv))
            for column in ('经度', '纬度', '氨氮(mg/L)', '溶解氧(mg/L)'):
                left = pd.to_numeric(raw[column], errors='coerce')
                right = pd.to_numeric(csv[column], errors='coerce')
                self.assertEqual(int(left.isna().sum()), int(right.isna().sum()))
                self.assertEqual(int((left == 0).sum()), int((right == 0).sum()))
                pd.testing.assert_series_equal(left, right, check_names=False, check_dtype=False)
            if item['id'] == 'S06':
                self.assertEqual(int(pd.to_numeric(raw['氨氮(mg/L)'], errors='coerce').isna().sum()), 24)
                self.assertEqual(int(pd.to_numeric(raw['溶解氧(mg/L)'], errors='coerce').isna().sum()), 24)

    def test_production_loader_consumes_all_samples_and_keeps_observation_masks(self):
        for item in manifest_payload()['samples']:
            raw = (SAMPLES / item['file']).read_bytes()
            frame, report = load_data(base64.b64encode(raw).decode('ascii'), item['file'])
            self.assertIsNotNone(frame, report)
            self.assertEqual(len(frame), 1801)
            self.assertEqual(frame.attrs['data_identity']['kind'], 'synthetic')
            self.assertIn('datetime', frame)
            self.assertIn('ammonia_nitrogen', frame)
            self.assertIn('dissolved_oxygen', frame)
            if item['id'] == 'S06':
                self.assertEqual(int((~frame['__observed_ammonia_nitrogen']).sum()), 24)
                self.assertEqual(int((~frame['__observed_dissolved_oxygen']).sum()), 24)
                self.assertEqual(int(frame['__raw_ammonia_nitrogen'].isna().sum()), 24)

    def test_manifest_hash_and_size_match_actual_bytes(self):
        for item in manifest_payload()['samples']:
            raw = (SAMPLES / item['file']).read_bytes()
            self.assertEqual(len(raw), item['size_bytes'])
            self.assertEqual(sample_catalog.get_sample(item['id'])[1], raw)

    def test_sample_paths_are_not_exposed_by_package_filter(self):
        self.assertTrue(source_allowed('data/samples/S01_仿真_正常背景.csv'))
        self.assertTrue(source_allowed('data/samples/manifest.json'))
        self.assertFalse(source_allowed('仿真数据/S01_仿真_正常背景.xlsx'))


class SampleApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.env = patch.dict(os.environ, {'WQ_PROJECT_HOME': str(Path(self.tmp.name) / 'home')})
        self.env.start()
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.session = STORE.get(self.sid)

    def tearDown(self):
        self.client.close()
        STORE.drop(self.sid)
        self.env.stop()
        self.tmp.cleanup()

    def body(self, **updates):
        return {'project_id': self.session.project_id or None,
                'analysis_revision': self.session.analysis_revision,
                'results_revision': self.session.results_revision,
                'detect': False, 'river_form': None, **updates}

    def load(self, sample_id='S01', **updates):
        return self.client.post(f'/api/data/samples/{sample_id}/load',
                                headers=self.headers, json=self.body(**updates))

    def test_list_and_load_in_independent_demo_project(self):
        response = self.client.get('/api/data/samples', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['samples']), 6)
        response = self.load('S01')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['rows'], 1801)
        self.assertEqual(data['data_identity']['kind'], 'synthetic')
        self.assertEqual(data['video_frames'], 0)
        self.assertFalse(data['detection']['ok'])
        self.assertEqual(self.session.project_id, data['project_state']['current']['id'])
        self.assertEqual(len(self.session.df), 1801)
        self.assertIn('S01_仿真', self.session.data_filename)
        status = self.client.get('/api/data/status', headers=self.headers).json()
        self.assertTrue(status['has_data'])
        self.assertEqual(status['rows'], 1801)
        self.assertEqual(status['data_identity']['kind'], 'synthetic')
        self.assertEqual(status['sample_meta']['id'], 'S01')
        self.assertEqual(status['sample_meta']['version'], data['sample']['version'])
        self.assertEqual(status['video_frames'], 0)
        from backend.session import SessionStore
        restored = SessionStore().get(self.sid)
        self.assertEqual(restored.df.attrs['sample_meta']['id'], 'S01')

    def test_s01_zero_alerts_and_s06_detection_are_not_failures(self):
        response = self.load('S01', detect=True)
        self.assertEqual(response.status_code, 200, response.text)
        detection = response.json()['detection']
        self.assertTrue(detection['ok'])
        self.assertEqual(detection['black_spots'], 0)
        self.assertEqual(detection['clusters'], 0)
        response = self.load('S06', detect=True)
        self.assertEqual(response.status_code, 200, response.text)
        detection = response.json()['detection']
        self.assertTrue(detection['ok'])
        self.assertGreater(detection['black_spots'], 0)
        self.assertTrue(self.session.black_spots_json)

    def test_unknown_path_and_manifest_errors_do_not_replace_current_data(self):
        self.assertEqual(self.load('NOPE').status_code, 404)
        self.session.df = monitoring()
        self.session.data_filename = '保留_仿真.csv'
        before = (len(self.session.df), self.session.data_filename, self.session.analysis_revision)
        broken = manifest_payload()
        broken['samples'][0]['sha256'] = '0' * 64
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'manifest.json'
            path.write_text(json.dumps(broken, ensure_ascii=False), encoding='utf-8')
            with patch.object(sample_catalog, 'MANIFEST_PATH', path):
                self.assertEqual(self.client.get('/api/data/samples', headers=self.headers).status_code, 503)
                self.assertEqual(self.load('S01').status_code, 503)
        self.assertEqual((len(self.session.df), self.session.data_filename, self.session.analysis_revision), before)


    def test_duplicate_manifest_id_is_resource_error_not_empty_list(self):
        payload = manifest_payload()
        payload['samples'].append(deepcopy(payload['samples'][0]))
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / 'manifest.json'
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with patch.object(sample_catalog, 'MANIFEST_PATH', path):
                response = self.client.get('/api/data/samples', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn('重复', response.json()['detail'])

    def test_busy_and_version_conflicts_do_not_start_work(self):
        self.session.operation_lock.acquire()
        try:
            self.assertEqual(self.load('S01').status_code, 409)
        finally:
            self.session.operation_lock.release()
        self.assertEqual(self.load('S01', analysis_revision=99).status_code, 409)
        self.assertEqual(self.load('S01', results_revision=99).status_code, 409)
        self.assertEqual(self.load('S01', project_id='f' * 32).status_code, 409)
        self.assertEqual(self.load('S01', extra='no').status_code, 422)

    def test_parse_failure_keeps_current_project_and_data(self):
        self.session.df = monitoring()
        self.session.data_filename = '保留_仿真.csv'
        before = (len(self.session.df), self.session.data_filename)
        with patch('backend.routers.data.data_service.parse_monitoring', return_value=(None, '受控解析失败')):
            response = self.load('S01')
        self.assertEqual(response.status_code, 503)
        self.assertEqual((len(self.session.df), self.session.data_filename), before)

    def test_detection_failure_reports_loaded_data_separately(self):
        with patch('backend.routers.data.find_black_spots', side_effect=RuntimeError('受控检测失败')):
            response = self.load('S02', detect=True)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['rows'], 1801)
        self.assertTrue(data['data_identity']['kind'] == 'synthetic')
        self.assertFalse(data['detection']['ok'])
        self.assertIn('受控检测失败', data['detection']['error'])
        self.assertIn('S02_仿真', self.session.data_filename)

    def test_existing_project_is_saved_and_can_be_reopened(self):
        first = self.load('S01').json()
        first_id = first['project_state']['current']['id']
        second = self.load('S06').json()
        second_id = second['project_state']['current']['id']
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(len(self.client.get('/api/projects', headers=self.headers).json()['projects']), 2)
        reopened = self.client.post(f'/api/projects/{first_id}/open', headers=self.headers, json={})
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertIn('S01_仿真', self.session.data_filename)
        self.assertEqual(len(self.session.df), 1801)
        self.assertEqual(self.session.df.attrs['sample_meta']['id'], 'S01')

    def test_upload_regression_and_sample_share_production_loader(self):
        raw = (SAMPLES / 'S01_仿真_正常背景.csv').read_bytes()
        response = self.client.post('/api/data/upload', headers=self.headers,
            files={'file': ('S01_仿真_正常背景.csv', raw, 'text/csv')})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['rows'], 1801)
        self.assertEqual(self.session.data_identity['kind'], 'synthetic')


    def test_sample_meta_and_identity_survive_export_import(self):
        loaded = self.load('S01').json()
        project_id = loaded['project_state']['current']['id']
        exported = self.client.post(f'/api/projects/{project_id}/export', headers=self.headers, json={})
        self.assertEqual(exported.status_code, 200, exported.text)
        other = self.client.get('/api/session').json()['session_id']
        other_headers = {'X-Session-Id': other}
        try:
            imported = self.client.post('/api/projects/import', headers=other_headers,
                files={'file': ('sample.wqproj', exported.content, 'application/zip')},
                data={'river_form': 'null'})
            self.assertEqual(imported.status_code, 200, imported.text)
            status = self.client.get('/api/data/status', headers=other_headers).json()
            self.assertEqual(status['rows'], 1801)
            self.assertEqual(status['data_identity']['kind'], 'synthetic')
            self.assertEqual(status['sample_meta']['id'], 'S01')
            self.assertEqual(status['sample_meta']['version'], loaded['sample']['version'])
        finally:
            STORE.drop(other)



    def test_load_and_detection_do_not_require_key_or_external_http(self):
        with patch('requests.sessions.Session.request', side_effect=AssertionError('unexpected external request')):
            response = self.load('S02', detect=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['rows'], 1801)
        self.assertTrue(response.json()['detection']['ok'])
        self.assertEqual(response.json()['video_frames'], 0)

    def test_save_or_new_project_failure_keeps_current_state(self):
        first = self.load('S01').json()
        before_id = self.session.project_id
        before_file = self.session.data_filename
        with patch('backend.routers.data.shelve', side_effect=OSError('controlled disk full')):
            response = self.load('S02')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.project_id, before_id)
        self.assertEqual(self.session.data_filename, before_file)
        self.assertIn('S01_仿真', self.session.data_filename)

        other = self.client.get('/api/session').json()['session_id']
        other_headers = {'X-Session-Id': other}
        other_session = STORE.get(other)
        try:
            with patch('backend.routers.data.repository') as repo:
                repo.return_value.write.side_effect = OSError('controlled disk full')
                response = self.client.post('/api/data/samples/S01/load', headers=other_headers,
                    json={'project_id': None, 'analysis_revision': 0, 'results_revision': 0,
                          'detect': False, 'river_form': None})
            self.assertEqual(response.status_code, 503)
            self.assertIsNone(other_session.df)
            self.assertEqual(other_session.project_id, '')
        finally:
            STORE.drop(other)

    def assert_restores_sample(self, sample_id, project_id):
        restored = SessionStore().get(self.sid)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.project_id, project_id)
        self.assertEqual(restored.df.attrs['sample_meta']['id'], sample_id)

    def test_snapshot_read_failure_keeps_memory_binding_and_restore_target(self):
        self.assertEqual(self.load('S01').status_code, 200)
        first_id = self.session.project_id
        with patch('backend.projects.ProjectRepository.read',
                   side_effect=OSError('controlled snapshot read failure')):
            response = self.load('S02')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.project_id, first_id)
        self.assertEqual(self.session.df.attrs['sample_meta']['id'], 'S01')
        self.assertEqual(repository()._binding(self.sid), first_id)
        self.assertEqual(len(repository().list()), 1)
        self.assert_restores_sample('S01', first_id)

    def test_materialization_failure_keeps_memory_binding_and_restore_target(self):
        self.assertEqual(self.load('S01').status_code, 200)
        first_id = self.session.project_id
        with patch('backend.projects.prepare_snapshot',
                   side_effect=OSError('controlled materialize failure')):
            response = self.load('S02')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.project_id, first_id)
        self.assertEqual(self.session.df.attrs['sample_meta']['id'], 'S01')
        self.assertEqual(repository()._binding(self.sid), first_id)
        self.assertEqual(len(repository().list()), 1)
        self.assert_restores_sample('S01', first_id)

    def test_publish_failure_restores_previous_binding(self):
        self.assertEqual(self.load('S01').status_code, 200)
        first_id = self.session.project_id
        with patch('backend.projects.publish_snapshot',
                   side_effect=OSError('controlled publish failure')):
            response = self.load('S02')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.project_id, first_id)
        self.assertEqual(self.session.df.attrs['sample_meta']['id'], 'S01')
        self.assertEqual(repository()._binding(self.sid), first_id)
        self.assertEqual(len(repository().list()), 1)
        self.assert_restores_sample('S01', first_id)

    def test_binding_failure_keeps_previous_binding_and_memory(self):
        self.assertEqual(self.load('S01').status_code, 200)
        first_id = self.session.project_id
        with patch('backend.projects.ProjectRepository._set_binding',
                   side_effect=sqlite3.OperationalError('controlled bind failure')):
            response = self.load('S02')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.session.project_id, first_id)
        self.assertEqual(self.session.df.attrs['sample_meta']['id'], 'S01')
        self.assertEqual(repository()._binding(self.sid), first_id)
        self.assertEqual(len(repository().list()), 1)
        self.assert_restores_sample('S01', first_id)

    def test_new_manifest_id_is_loadable_without_frontend_branch(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = SAMPLES / 'S01_仿真_正常背景.csv'
            (directory / 'X01_仿真_新配置.csv').write_bytes(source.read_bytes())
            payload = manifest_payload()
            item = deepcopy(payload['samples'][0])
            item.update({'id': 'X01', 'title': 'X01 新配置', 'file': 'X01_仿真_新配置.csv'})
            item['sha256'] = sample_catalog.sha256((directory / 'X01_仿真_新配置.csv').read_bytes()).hexdigest()
            item['size_bytes'] = (directory / 'X01_仿真_新配置.csv').stat().st_size
            payload['samples'] = [item]
            (directory / 'manifest.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with patch.object(sample_catalog, 'SAMPLE_DIR', directory), \
                 patch.object(sample_catalog, 'MANIFEST_PATH', directory / 'manifest.json'):
                response = self.load('X01')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['rows'], 1801)
        self.assertEqual(response.json()['sample']['id'], 'X01')
