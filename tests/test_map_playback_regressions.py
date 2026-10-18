"""普通点查与扩散播放器使用当前会话数据，无外部API和LLM。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from river_model.config import RiverConfig
from river_model.state import RiverModelState, WQResult, HydroResult


class MapPlaybackTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid}
        self.session=STORE.get(self.sid)
        self.session.df=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=40,freq='5s'),
            'latitude':np.linspace(23,23.002,40),'longitude':np.full(40,113.),
            'ammonia_nitrogen':np.full(40,3.),'dissolved_oxygen':np.full(40,4.),
            'cod':np.full(40,25.),'turbidity':np.full(40,20.),'ph':np.full(40,7.)})
        t=np.linspace(0,1,101);x=np.array([0.,50.,100.]);a=np.ones((101,3))
        nh3=a.copy();nh3[13,1]=20
        do=a*8;do[17,1]=.05
        hydro=HydroResult(t,x,a,a,a,a,{'run_id':'h1'})
        wq=WQResult(t,x,ammonia=nh3,cbod=a*2,dissolved_oxygen=do,params={
            'hydro_run_id':'h1','source_start_h':.235,'duration_min':.7,
            'chainage_m':52.,'source_effective_chainage_m':50.,'load_kg':10.,'pollutant_type':'ammonia'})
        self.session.river_state=RiverModelState(config=RiverConfig(river_name='通用河段A',length_m=100.),
            hydro_result=hydro,wq_result=wq,model_initialized=True)

    def tearDown(self):
        STORE.drop(self.sid);self.client.close()

    def playback(self,**params):
        return self.client.get('/api/river/playback',headers=self.headers,params=params)

    def test_every_valid_sample_is_available_not_every_thirtieth(self):
        before=self.session.df.copy(deep=True)
        response=self.client.get('/api/charts/trace-map',headers=self.headers)
        self.assertEqual(response.status_code,200)
        samples=response.json()['samples']
        self.assertEqual(len(samples),40)
        self.assertEqual(samples[29]['id'],29)
        self.assertEqual(samples[29]['values']['cod'],25)
        self.assertEqual(samples[29]['sources']['cod'],'record')
        pd.testing.assert_frame_equal(self.session.df,before)

    def test_missing_and_interpolated_values_are_not_original_observations(self):
        df=self.session.df
        df['__observed_ammonia_nitrogen']=True;df.loc[3,'__observed_ammonia_nitrogen']=False
        df.loc[0,'dissolved_oxygen']=0
        df.loc[1,'cod']=np.nan
        response=self.client.get('/api/charts/trace-map',headers=self.headers)
        self.assertEqual(response.status_code,200)
        rows=response.json()['samples']
        self.assertEqual(rows[0]['values']['dissolved_oxygen'],0)
        self.assertIsNone(rows[1]['values']['cod'])
        self.assertIsNone(rows[3]['raw_score'])
        self.assertIsNone(rows[3]['values']['ammonia_nitrogen'])
        self.assertEqual(rows[3]['interpolated']['ammonia_nitrogen'],3)

    def test_invalid_coordinates_skip_rows_but_duplicate_indices_keep_unique_ids(self):
        self.session.df.loc[2,'latitude']=np.inf
        self.session.df.index=np.zeros(40,dtype=int)
        rows=self.client.get('/api/charts/trace-map',headers=self.headers).json()['samples']
        self.assertEqual(len(rows),39)
        self.assertEqual(len({r['id'] for r in rows}),39)

    def test_actual_frame_values_event_boundaries_peak_and_end_are_preserved(self):
        with patch('requests.get') as external:
            response=self.playback(max_frames=6)
        external.assert_not_called()
        self.assertEqual(response.status_code,200)
        d=response.json()
        self.assertEqual(d['times'][0],0);self.assertEqual(d['times'][-1],1)
        self.assertIn(.13,d['times'])
        self.assertTrue({.23,.24,.25} <= set(d['times']))
        self.assertLessEqual(len(d['times']),11)
        for time,values in zip(d['times'],d['concentrations']):
            np.testing.assert_array_equal(values,self.session.river_state.wq_result.ammonia[round(time*100)])
        self.assertEqual(d['color_scale'],{'min':1.,'max':20.,'inverse':False})
        self.assertEqual(d['river_name'],'通用河段A')

    def test_do_low_oxygen_frame_and_class_limit_are_preserved(self):
        self.session.target_water_class='III'
        d=self.playback(constituent='do',max_frames=6).json()
        self.assertIn(.17,d['times'])
        self.assertEqual(d['color_scale']['min'],.05)
        self.assertTrue(d['color_scale']['inverse'])
        self.assertEqual(d['limit'],5.)
        self.assertEqual(d['limit_direction'],'minimum')

    def test_cbod_does_not_borrow_cod_standard(self):
        d=self.playback(constituent='cbod').json()
        self.assertEqual(d['label'],'CBOD');self.assertIsNone(d['limit'])

    def test_no_model_and_other_session_do_not_reuse_results(self):
        response=self.client.get('/api/river/playback')
        self.assertEqual(response.status_code,400)
        STORE.drop(response.headers['X-Session-Id'])
        self.session.river_state=None
        self.assertEqual(self.playback().status_code,400)

    def test_stale_hydrodynamics_result_is_rejected(self):
        self.session.river_state.hydro_result.params['run_id']='new-run'
        self.assertEqual(self.playback().status_code,400)

    def test_tidal_result_is_not_exposed_as_valid_animation(self):
        self.session.river_state.hydro_result.params['tidal_amplitude_m']=.5
        self.assertEqual(self.playback().status_code,400)

    def test_invalid_constituent_and_frame_limits(self):
        for params in [{'constituent':'cod'},{'max_frames':0},{'max_frames':242},{'max_frames':'nan'}]:
            self.assertEqual(self.playback(**params).status_code,422)

    def test_nonfinite_and_out_of_order_results_are_rejected(self):
        wq=self.session.river_state.wq_result
        wq.ammonia[0,0]=np.nan
        self.assertEqual(self.playback().status_code,400)
        wq.ammonia[0,0]=1;wq.t[1]=wq.t[0]
        self.assertEqual(self.playback().status_code,400)

    def test_missing_constituent_is_rejected(self):
        self.session.river_state.wq_result.cbod=None
        self.assertEqual(self.playback(constituent='cbod').status_code,400)

    def test_without_gps_profile_still_plays(self):
        self.session.df=None
        d=self.playback().json()
        self.assertFalse(d['mapping']['available']);self.assertTrue(d['concentrations'])

    def test_gps_mapping_does_not_stretch_model_to_full_track(self):
        d=self.playback().json()
        mapping=d['mapping']
        self.assertTrue(mapping['available'])
        self.assertEqual(len(mapping['positions']),3)
        self.assertGreater(mapping['track_length_m'],180)
        self.assertLess(mapping['positions'][-1][0],23.0015)

    def test_insufficient_track_coverage_disables_map_only(self):
        self.session.df['latitude']=np.linspace(23,23.0002,40)
        d=self.playback().json()
        self.assertFalse(d['mapping']['available']);self.assertTrue(d['concentrations'])
        self.assertIn('未覆盖',d['mapping']['reason'])

    def test_busy_session_does_not_read_partial_model_state(self):
        self.session.operation_lock.acquire()
        try:self.assertEqual(self.playback().status_code,409)
        finally:self.session.operation_lock.release()


if __name__=='__main__':unittest.main()
