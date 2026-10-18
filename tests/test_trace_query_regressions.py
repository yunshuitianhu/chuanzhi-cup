"""正常背景可查周边，手动坐标优先，参数不改写会话配置。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from fastapi.testclient import TestClient
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.main import app
from backend.session import STORE
from backend.routers import trace


class TraceQueryTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid}
        self.session=STORE.get(self.sid)
        self.mock=patch.object(trace,'multi_source_trace',return_value={'综合污染源列表':[]})
        self.engine=self.mock.start()

    def tearDown(self):
        self.mock.stop();STORE.drop(self.sid);self.client.close()

    def post(self,payload):
        return self.client.post('/api/trace/multi-source',headers=self.headers,json=payload)

    def test_clean_route_uses_actual_record_not_mean_of_curve(self):
        self.session.df=pd.DataFrame({'latitude':[30.,31.,30.],'longitude':[120.,121.,122.]})
        self.session.black_spots=pd.DataFrame()
        r=self.post({})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['搜索中心'],[31.,121.])
        self.assertIn('代表采样点',r.json()['搜索中心来源'])
        self.assertIn('不表示本次已检出污染',r.json()['查询用途'])

    def test_manual_coordinates_work_without_data_or_hotspots(self):
        r=self.post({'lat':0.,'lon':0.,'radius_m':1000})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['搜索中心'],[0.,0.])
        self.assertEqual(r.json()['搜索中心来源'],'手动输入坐标')
        self.assertEqual(self.engine.call_args.args[3].search_radius_m,1000)
        self.assertEqual(self.session.analysis_config.search_radius_m,500)

    def test_valid_hotspot_keeps_priority_and_noise_is_excluded(self):
        self.session.df=pd.DataFrame({'latitude':[32.],'longitude':[118.]})
        self.session.black_spots=pd.DataFrame({'latitude':[33.,30.,30.2],
            'longitude':[119.,120.,120.2],'cluster_id':[-1,0,0]})
        r=self.post({})
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()['搜索中心'],[30.1,120.1])
        self.assertIn('聚类',r.json()['搜索中心来源'])

    def test_invalid_gps_does_not_become_search_center(self):
        self.session.df=pd.DataFrame({'latitude':[float('nan'),91.,30.],
                                     'longitude':[120.,120.,float('inf')]})
        self.assertEqual(self.post({}).status_code,400)
        self.engine.assert_not_called()

    def test_partial_or_invalid_manual_request_is_rejected(self):
        for payload in [{'lat':30.},{'lon':120.},{'lat':91.,'lon':120.},
            {'lat':'30','lon':120.},{'lat':True,'lon':120.},{'radius_m':0},
            {'radius_m':500.5},{'radius_m':50001},{'radius_m':'500'}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.post(payload).status_code,422)
        self.engine.assert_not_called()

    def test_missing_every_coordinate_explains_how_to_proceed(self):
        r=self.post({})
        self.assertEqual(r.status_code,400)
        self.assertIn('手动填写',r.json()['detail'])


if __name__=='__main__':unittest.main()
