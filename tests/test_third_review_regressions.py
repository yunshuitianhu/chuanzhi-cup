"""技术查询互斥、站点输入边界及报告状态一致性；网络均受控。"""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
import json,unittest
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from backend.agent.tools import execute_tool

class TechQueryReviewTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid};self.session=STORE.get(self.sid)
    def tearDown(self):
        self.client.close()
    def test_multi_source_query_holds_session_lock_during_external_query(self):
        started,release=Event(),Event()
        def controlled(*args):
            started.set();assert release.wait(5)
            return json.dumps({'综合污染源列表':[]})
        with patch('backend.routers.trace.multi_source_trace',side_effect=controlled),ThreadPoolExecutor(1) as executor:
            pending=executor.submit(self.client.post,'/api/trace/multi-source',headers=self.headers,json={'lat':30.,'lon':120.})
            try:
                self.assertTrue(started.wait(3))
                mutation=self.client.post('/api/data/upload',headers=self.headers,files={'file':('sample.csv',b'time,x\n2026-01-01,1','text/csv')})
                self.assertEqual(mutation.status_code,409,mutation.text)
            finally:release.set()
            self.assertEqual(pending.result().status_code,200)
    def test_station_query_invalidates_completed_report_but_keeps_history(self):
        s=self.session;s.analysis_done=True;s.analysis_status='succeeded';s.comprehensive_summary='old summary'
        s.analysis_steps={'generate_final_report':'succeeded','get_comprehensive_summary':'succeeded'}
        s.messages=[{'role':'assistant','content':'历史正文'}];revision=s.results_revision
        with patch('backend.routers.stations.get_national_station_data',return_value={'站点列表':[],'站点数量':0}):
            result=self.client.get('/api/stations?province=广东省',headers=self.headers)
        self.assertEqual(result.status_code,200);self.assertFalse(s.analysis_done);self.assertEqual(s.analysis_status,'stale')
        self.assertIsNone(s.comprehensive_summary);self.assertGreater(s.results_revision,revision)
        self.assertEqual(s.messages[0]['content'],'历史正文');self.assertNotIn('generate_final_report',s.analysis_steps)
        self.assertEqual(json.loads(s.tool_evidence['get_national_station_data']),s.national_station_result)
    def test_invalid_station_coordinates_are_rejected_before_query(self):
        cases=['lat=30','lon=120','lat=nan&lon=120','lat=91&lon=120','lat=30&lon=181','lat=30&lon=inf','radius_km=0','radius_km=-1']
        with patch('backend.routers.stations.get_national_station_data',return_value={'站点列表':[]}) as query:
            for value in cases:
                with self.subTest(value=value):self.assertEqual(self.client.get('/api/stations?live=false&'+value,headers=self.headers).status_code,422)
            query.assert_not_called()
    def test_failed_station_query_keeps_previous_report_and_evidence(self):
        s=self.session;s.analysis_done=True;s.analysis_status='succeeded';s.national_station_result={'marker':'previous'}
        with patch('backend.routers.stations.get_national_station_data',side_effect=ValueError('受控异常')),TestClient(app,raise_server_exceptions=False) as client:
            result=client.get('/api/stations?live=false',headers=self.headers)
        self.assertEqual(result.status_code,500);self.assertTrue(s.analysis_done)
        self.assertEqual(s.national_station_result,{'marker':'previous'})
    def test_agent_station_query_also_invalidates_previous_completed_report(self):
        s=self.session;s.analysis_done=True;s.analysis_status='succeeded'
        with patch('backend.agent.tools.get_national_station_data',return_value={'站点列表':[],'站点数量':0}):
            raw,failed=execute_tool('get_national_station_data',{'live':False},s)
        self.assertFalse(failed,raw);self.assertFalse(s.analysis_done);self.assertEqual(s.analysis_status,'stale')
