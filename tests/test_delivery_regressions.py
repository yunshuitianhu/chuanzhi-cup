"""交付入口契约：网页/API同源，资源缺失不伪装成功，不暴露项目文件。"""

from io import BytesIO
import json
import socket
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from pathlib import Path

from fastapi.testclient import TestClient

from backend.main import app as api_app
from backend.session import STORE
from backend.web import create_app
from scripts.serve_web import check_frontend, check_port
from scripts.package_delivery import write_bundle, worktree_problems
from scripts.verify_delivery import fingerprint


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name) / 'dist'
        (self.dist / 'assets').mkdir(parents=True)
        (self.dist / 'assets' / 'app-abcd.js').write_text('export const ok=true;', encoding='utf-8')
        (self.dist / 'index.html').write_text('<html><script src="/assets/app-abcd.js"></script></html>', encoding='utf-8')
        (self.dist / '.vite').mkdir()
        (self.dist / '.vite' / 'manifest.json').write_text(json.dumps({'index.html': {'file': 'assets/app-abcd.js'}}), encoding='utf-8')
        self.client = TestClient(create_app(self.dist))

    def test_routes_refresh_without_creating_sessions(self):
        before = STORE.count
        for path in ('/', '/chat', '/charts', '/river', '/tech'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('text/html', response.headers['content-type'])
            self.assertEqual(response.headers['cache-control'], 'no-cache')
            self.assertNotIn('x-session-id', response.headers)
            self.assertEqual(self.client.head(path).status_code, 200)
        self.assertEqual(STORE.count, before)

    def test_unknown_api_and_page_paths_are_404_not_the_home_page(self):
        for path in ('/api/does-not-exist', '/nope', '/river/extra'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn('<html', response.text.lower(), path)

    def test_running_service_keeps_lazy_assets_when_build_directory_is_replaced(self):
        old = self.client.get('/assets/app-abcd.js').content
        (self.dist/'assets/app-abcd.js').unlink()
        (self.dist/'assets/app-new.js').write_text('export const changed=true;',encoding='utf-8')
        (self.dist/'index.html').write_text('<html><script src="/assets/app-new.js"></script></html>',encoding='utf-8')
        self.assertIn('app-abcd.js',self.client.get('/river').text)
        self.assertEqual(self.client.get('/assets/app-abcd.js').content,old)
        self.assertEqual(self.client.get('/assets/app-new.js').status_code,404)
        updated=TestClient(create_app(self.dist))
        self.assertIn('app-new.js',updated.get('/tech').text)
        self.assertEqual(updated.get('/assets/app-new.js').status_code,200)

    def test_asset_cache_and_head_describe_the_same_immutable_bytes(self):
        reply=self.client.get('/assets/app-abcd.js')
        self.assertEqual(reply.headers['cache-control'],'public, max-age=31536000, immutable')
        self.assertEqual(self.client.get('/assets/app-abcd.js',headers={'If-None-Match':reply.headers['etag']}).status_code,304)
        head=self.client.head('/assets/app-abcd.js')
        self.assertEqual(head.content,b'')
        self.assertEqual(int(head.headers['content-length']),len(reply.content))
        self.assertEqual(head.headers['etag'],reply.headers['etag'])
        self.assertNotIn('x-session-id',reply.headers)

    def test_api_same_origin_and_separate_api_entry_still_work(self):
        self.assertEqual(self.client.get('/api/health').json()['service'], 'water-quality-api')
        self.assertEqual(TestClient(api_app).get('/').json()['service'], '水质监测与污染溯源 API')
        self.assertEqual(self.client.get('/openapi.json').status_code, 200)

    def test_static_missing_and_sensitive_paths_never_return_spa(self):
        self.assertEqual(self.client.get('/assets/app-abcd.js').status_code, 200)
        for path in ('/assets/missing.js', '/api/missing', '/.env', '/README.md', '/data/projects/projects.sqlite3', '/assets/%2e%2e/%2e%2e/.env'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, path)
            self.assertNotIn('<html>', response.text)

    def test_incomplete_frontend_fails_preflight(self):
        check_frontend(self.dist)
        (self.dist / 'assets' / 'app-abcd.js').unlink()
        with self.assertRaisesRegex(RuntimeError, '资源不完整'):
            check_frontend(self.dist)
        (self.dist / 'index.html').unlink()
        with self.assertRaisesRegex(RuntimeError, '缺少前端构建'):
            check_frontend(self.dist)
        with self.assertRaisesRegex(RuntimeError, '缺少前端构建'):
            create_app(self.dist)

    def test_occupied_port_is_reported_and_not_stopped(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            with self.assertRaisesRegex(RuntimeError, '不可绑定或已占用'):
                check_port(listener.getsockname()[1])
            self.assertGreater(listener.fileno(), -1)

    def test_missing_lazy_chunk_and_traversal_in_manifest_are_rejected(self):
        manifest = self.dist / '.vite' / 'manifest.json'
        for entry in ({'file': 'assets/missing-river.js'}, {'file': '../private.txt'},
                      {'file': 'assets/app-abcd.js', 'dynamicImports': ['missing']}):
            manifest.write_text(json.dumps({'index.html': entry}), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, '构建清单缺失或资源不完整'):
                check_frontend(self.dist)

    def test_bundle_contains_checked_frontend_not_runtime_data(self):
        source=BytesIO()
        with zipfile.ZipFile(source,'w') as archive:
            archive.writestr('README.md','source')
            archive.writestr('.env.example','placeholder')
            archive.writestr('.rag_index/chroma.sqlite3','runtime')
            archive.writestr('data/projects/private.wqproj','user project')
            for path in sorted((Path(__file__).resolve().parents[1]/'data'/'samples').iterdir()):
                if path.is_file():
                    archive.writestr('data/samples/'+path.name,path.read_bytes())
        output=Path(self.temp.name)/'delivery.zip'
        write_bundle(source.getvalue(),self.dist,output,'test-revision')
        with zipfile.ZipFile(output) as archive:
            names=set(archive.namelist())
            self.assertIn('frontend/dist/assets/app-abcd.js',names)
            self.assertIn('frontend/dist/.vite/manifest.json',names)
            self.assertIn('.env.example',names)
            self.assertNotIn('.rag_index/chroma.sqlite3',names)
            self.assertNotIn('data/projects/private.wqproj',names)
            self.assertIn('data/samples/manifest.json',names)
            self.assertIn('data/samples/README.md',names)
            self.assertTrue(any(name.startswith('data/samples/S01_') and name.endswith('.csv') for name in names))
            self.assertEqual(json.loads(archive.read('delivery-manifest.json'))['revision'],'test-revision')
        with self.assertRaises(FileExistsError):
            write_bundle(source.getvalue(),self.dist,output,'test-revision')

    def test_packaging_blocks_uncommitted_and_untracked_source_but_ignores_runtime_paths(self):
        self.assertEqual(worktree_problems([], []), [])
        dirty = worktree_problems(['backend/new.py'], [])
        self.assertEqual(len(dirty), 1)
        self.assertIn('未提交源码', dirty[0])
        pending = worktree_problems([], ['scripts/new_feature.py'])
        self.assertEqual(len(pending), 1)
        self.assertIn('未跟踪文件', pending[0])
        self.assertIn('scripts/new_feature.py', pending[0])
        for name in ('work/tmp.zip', 'data/projects/x/projects.sqlite3',
                     '.rag_index/chroma.sqlite3', '仿真数据/a.xlsx'):
            self.assertEqual(worktree_problems([name], [name]), [], name)
        many = worktree_problems([], [f'src/{index}.py' for index in range(12)])
        self.assertIn('等 12 个', many[0])

    def test_bundle_requires_sample_manifest_and_hashes(self):
        source=BytesIO()
        with zipfile.ZipFile(source,'w') as archive:
            archive.writestr('README.md','source')
            archive.writestr('data/samples/README.md','samples')
        output=Path(self.temp.name)/'missing-samples.zip'
        with self.assertRaisesRegex(ValueError,'缺少 data/samples'):
            write_bundle(source.getvalue(),self.dist,output,'test-revision')
        self.assertFalse(output.exists())

    def test_bundle_rejects_tracked_secret_file(self):
        source=BytesIO()
        with zipfile.ZipFile(source,'w') as archive:
            archive.writestr('.env','placeholder')
        output=Path(self.temp.name)/'unsafe.zip'
        with self.assertRaisesRegex(ValueError,'包含本机配置文件'):
            write_bundle(source.getvalue(),self.dist,output,'test-revision')
        self.assertFalse(output.exists())

    def test_migration_fingerprint_accepts_new_directory_but_checks_file_bytes(self):
        import pandas as pd
        def sample(folder):
            folder.mkdir()
            image=folder/'river_hydro_profile.png'
            image.write_bytes(b'same artifact')
            return SimpleNamespace(df=pd.DataFrame({'value':[0.,1.]}),data_identity={'kind':'synthetic'},
                river_state=SimpleNamespace(output_dir=str(folder)),black_spots_json='{}',analysis_done=False,
                river_ui={'hydro':{'输出文件':[str(image)]}})
        first=sample(Path(self.temp.name)/'original')
        second=sample(Path(self.temp.name)/'restored')
        self.assertEqual(fingerprint(first),fingerprint(second))
        (Path(second.river_state.output_dir)/'river_hydro_profile.png').write_bytes(b'corrupt artifact')
        self.assertNotEqual(fingerprint(first),fingerprint(second))


if __name__ == '__main__':
    unittest.main()
