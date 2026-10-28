"""R02 回归：示例资源读取失败必须进入 503 资源错误边界。

覆盖读取权限错误、检查后文件消失、实际文件超过上限三种情况；全部使用
临时项目目录和受控故障注入，不读取或修改用户项目。
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE
from backend import sample_catalog

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / 'data' / 'samples'
MANIFEST = SAMPLES / 'manifest.json'


def manifest_payload():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def monitoring(n=5):
    return pd.DataFrame({'datetime': pd.date_range('2026-01-01', periods=n, freq='5s'),
                         'ammonia_nitrogen': [.2] * n, 'dissolved_oxygen': [7.] * n})


class SampleResourceTests(unittest.TestCase):
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

    def load(self, sample_id='S01'):
        return self.client.post(f'/api/data/samples/{sample_id}/load', headers=self.headers,
                                json={'project_id': self.session.project_id or None,
                                      'analysis_revision': self.session.analysis_revision,
                                      'results_revision': self.session.results_revision,
                                      'detect': False, 'river_form': None})

    def test_unreadable_csv_is_annotated_503_not_generic_500(self):
        self.session.df = monitoring()
        self.session.data_filename = '保留_仿真.csv'
        before = (len(self.session.df), self.session.data_filename, self.session.analysis_revision)
        real_open = Path.open

        def deny_csv(path, *args, **kwargs):
            if path.name.startswith('S01_') and path.suffix == '.csv':
                raise PermissionError('controlled permission failure')
            return real_open(path, *args, **kwargs)

        with patch.object(Path, 'open', deny_csv):
            listed = self.client.get('/api/data/samples', headers=self.headers)
            loaded = self.load('S01')
        for response in (listed, loaded):
            self.assertEqual(response.status_code, 503)
            self.assertIn('无法读取', response.json()['detail'])
            self.assertIn('data/samples', response.json()['detail'])
            self.assertNotIn(str(SAMPLES), response.text)
        self.assertEqual((len(self.session.df), self.session.data_filename,
                          self.session.analysis_revision), before)

    def test_csv_disappearing_after_checks_is_annotated_503(self):
        real_open = Path.open

        def vanish(path, *args, **kwargs):
            if path.name.startswith('S01_') and path.suffix == '.csv':
                raise FileNotFoundError('controlled missing file')
            return real_open(path, *args, **kwargs)

        with patch.object(Path, 'open', vanish):
            response = self.client.get('/api/data/samples', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn('无法读取', response.json()['detail'])

    def test_actual_file_over_limit_is_rejected_before_full_read(self):
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            csv = directory / 'X02_仿真_超大.csv'
            csv.write_bytes(b'x' * 65)
            payload = manifest_payload()
            item = deepcopy(payload['samples'][0])
            item.update({'id': 'X02', 'title': 'X02 超大', 'file': csv.name})
            item['size_bytes'] = 10
            item['sha256'] = sample_catalog.sha256(csv.read_bytes()).hexdigest()
            payload['samples'] = [item]
            manifest = directory / 'manifest.json'
            manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            with patch.object(sample_catalog, 'SAMPLE_DIR', directory), \
                 patch.object(sample_catalog, 'MANIFEST_PATH', manifest), \
                 patch.object(sample_catalog, 'MAX_SAMPLE_BYTES', 64):
                response = self.client.get('/api/data/samples', headers=self.headers)
        self.assertEqual(response.status_code, 503)
        self.assertIn('超过大小上限', response.json()['detail'])
        self.assertNotIn(str(directory), response.text)

    def test_get_sample_returns_the_bytes_that_were_verified(self):
        item, raw = sample_catalog.get_sample('S01')
        self.assertEqual(len(raw), item['size_bytes'])
        self.assertEqual(sample_catalog.sha256(raw).hexdigest(), item['sha256'])


if __name__ == '__main__':
    unittest.main()
