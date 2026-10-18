"""底图代理离线契约：不调用真实外部服务，不读写分析项目。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.main import app
from backend.routers import maps
from backend.session import STORE

PNG = b'\x89PNG\r\n\x1a\nimage-contract'
QUERY = {'lat':23.166774, 'lon':113.127267, 'zoom':14, 'width':640, 'height':460}
SECRET = 'test-key-must-stay-server-side'


def upstream(body=PNG, mime='image/png;charset=UTF-8', status=200):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status_code = status
    response.headers = {'Content-Type':mime}
    response.iter_content.return_value = [body]
    return response


class MapRegressionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        maps._CACHE.clear()
        maps._TILE_CACHE.clear()
        self.key_patch = patch.object(maps, 'resolve_api_key', return_value=SECRET)
        self.key_patch.start()

    def tearDown(self):
        self.key_patch.stop()
        self.client.close()
        maps._CACHE.clear()
        maps._TILE_CACHE.clear()

    def get(self, **overrides):
        return self.client.get('/api/maps/amap-static', params={**QUERY, **overrides})

    def test_image_contract_and_no_new_session_or_secret(self):
        count = STORE.count
        with patch.object(maps.requests, 'get', return_value=upstream()) as get:
            result = self.get()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, PNG)
        self.assertEqual(result.headers['content-type'], 'image/png')
        self.assertIn('private', result.headers['cache-control'])
        self.assertNotIn('X-Session-Id', result.headers)
        self.assertEqual(STORE.count, count)
        self.assertNotIn(SECRET, str(result.headers) + result.text)
        self.assertEqual(get.call_args.args, ('https://restapi.amap.com/v3/staticmap',))
        self.assertEqual(get.call_args.kwargs['params'], {
            'key':SECRET, 'location':'113.127267,23.166774', 'zoom':14, 'size':'640*460', 'scale':1})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_validation_rejects_nonfinite_out_of_range_and_arbitrary_urls(self):
        with patch.object(maps.requests, 'get') as get:
            for query in ({'lat':'nan'}, {'lon':'inf'}, {'zoom':18}, {'zoom':1.5},
                          {'width':1025}, {'height':0}, {'lat':90}, {'lon':181},
                          {'lat':'https://attacker.invalid/'}):
                with self.subTest(query=query):
                    self.assertEqual(self.get(**query).status_code, 422)
            get.assert_not_called()

    def test_missing_key_is_actionable_and_stateless(self):
        count = STORE.count
        with patch.object(maps, 'resolve_api_key', return_value=''), patch.object(maps.requests, 'get') as get:
            result = self.get()
        self.assertEqual(result.status_code, 503)
        self.assertIn('Web服务', result.json()['detail'])
        self.assertEqual(STORE.count, count)
        get.assert_not_called()

    def test_json_error_only_discloses_numeric_code(self):
        body = ('{"infocode":"10005","info":"'+SECRET+'"}').encode()
        with patch.object(maps.requests, 'get', return_value=upstream(body, 'application/json')):
            result = self.get()
        self.assertEqual(result.status_code, 502)
        self.assertIn('10005', result.text)
        self.assertNotIn(SECRET, result.text)

    def test_exception_does_not_echo_upstream_url(self):
        with patch.object(maps.requests, 'get', side_effect=maps.requests.Timeout('https://host/?key='+SECRET)):
            result = self.get()
        self.assertEqual(result.status_code, 502)
        self.assertNotIn(SECRET, result.text)
        self.assertIn('超时', result.json()['detail'])

    def test_redirect_rejection_and_nonimage_rejection(self):
        for response in (upstream(status=302), upstream(b'<html>blocked</html>', 'text/html'),
                         upstream(b'fake', 'image/png'), upstream(PNG, 'image/svg+xml')):
            with patch.object(maps.requests, 'get', return_value=response):
                self.assertEqual(self.get().status_code, 502)
        self.assertFalse(maps._CACHE)

    def test_image_size_is_bounded(self):
        with patch.object(maps, '_MAX_IMAGE_BYTES', 10), patch.object(maps.requests, 'get', return_value=upstream()):
            self.assertEqual(self.get().status_code, 502)
        self.assertFalse(maps._CACHE)

    def test_cache_hit_expiry_and_key_rotation(self):
        with patch.object(maps.requests, 'get', side_effect=lambda *a, **k:upstream()) as get:
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(get.call_count, 1)
            with patch.object(maps, 'resolve_api_key', return_value='rotated'):
                self.assertEqual(self.get().status_code, 200)
            self.assertEqual(get.call_count, 2)
            for k, v in list(maps._CACHE.items()):
                maps._CACHE[k] = (0, v[1], v[2])
            self.assertEqual(self.get().status_code, 200)
            self.assertEqual(get.call_count, 3)

    def test_cache_count_is_bounded_and_failed_calls_are_not_cached(self):
        with patch.object(maps.requests, 'get', side_effect=lambda *a, **k:upstream()):
            for i in range(18):
                self.assertEqual(self.get(lon=113+i/100).status_code, 200)
        self.assertEqual(len(maps._CACHE), 16)
        maps._CACHE.clear()
        with patch.object(maps.requests, 'get', return_value=upstream(status=403)) as get:
            self.get(); self.get()
            self.assertEqual(get.call_count, 2)
        self.assertFalse(maps._CACHE)

    def test_jpeg_supported(self):
        with patch.object(maps.requests, 'get', return_value=upstream(b'\xff\xd8\xffjpeg', 'image/jpeg')):
            self.assertEqual(self.get().headers['content-type'], 'image/jpeg')

    def test_satellite_tile_uses_fixed_upstream_and_hides_token(self):
        body = b'RIFF\x10\x00\x00\x00WEBPsample'
        count = STORE.count
        with patch.object(maps.requests, 'get', return_value=upstream(body,'image/webp')) as get:
            response = self.client.get('/api/maps/starcloud/15/26680/14215')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,body)
        self.assertEqual(response.headers['content-type'],'image/webp')
        self.assertNotIn('X-Session-Id',response.headers)
        self.assertEqual(STORE.count,count)
        self.assertNotIn(SECRET,response.text+str(response.headers))
        self.assertEqual(get.call_args.args,('https://tiles1.geovisearth.com/base/v1/img/15/26680/14215',))
        self.assertEqual(get.call_args.kwargs['params'],{'format':'webp','tmsIds':'w','token':SECRET})
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    def test_satellite_tile_validation(self):
        with patch.object(maps.requests,'get') as get:
            for path in ['19/0/0','0/1/0','1/0/2','15/-1/0','nan/0/0','15/0/99999999']:
                self.assertEqual(self.client.get('/api/maps/starcloud/'+path).status_code,422)
            get.assert_not_called()

    def test_satellite_missing_token(self):
        with patch.object(maps,'resolve_api_key',return_value=''), patch.object(maps.requests,'get') as get:
            self.assertEqual(self.client.get('/api/maps/starcloud/0/0/0').status_code,503)
            get.assert_not_called()

    def test_satellite_errors_are_sanitized_and_not_cached(self):
        for response in [upstream(status=403),upstream(status=302),
                         upstream(SECRET.encode(),'application/json'),upstream(PNG,'image/webp')]:
            with patch.object(maps.requests,'get',return_value=response):
                result=self.client.get('/api/maps/starcloud/0/0/0')
            self.assertEqual(result.status_code,502)
            self.assertNotIn(SECRET,result.text)
        self.assertFalse(maps._TILE_CACHE)

    def test_satellite_timeout_never_echoes_token(self):
        with patch.object(maps.requests,'get',side_effect=maps.requests.Timeout('?token='+SECRET)):
            result=self.client.get('/api/maps/starcloud/0/0/0')
        self.assertEqual(result.status_code,502)
        self.assertNotIn(SECRET,result.text)

    def test_satellite_cache_expiry_and_rotation(self):
        with patch.object(maps.requests,'get',side_effect=lambda *a,**k:upstream(b'RIFF0000WEBPimage','image/webp')) as get:
            self.client.get('/api/maps/starcloud/0/0/0')
            self.client.get('/api/maps/starcloud/0/0/0')
            self.assertEqual(get.call_count,1)
            with patch.object(maps,'resolve_api_key',return_value='rotated'):
                self.client.get('/api/maps/starcloud/0/0/0')
            self.assertEqual(get.call_count,2)
            for k,v in list(maps._TILE_CACHE.items()):maps._TILE_CACHE[k]=(0,v[1])
            self.client.get('/api/maps/starcloud/0/0/0')
            self.assertEqual(get.call_count,3)


if __name__ == '__main__':
    unittest.main()
