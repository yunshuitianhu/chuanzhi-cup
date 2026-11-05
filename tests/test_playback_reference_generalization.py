"""不同河段的真实解析、HTTP正演及地图配置流程；全部为受控情景。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from backend.data_service import parse_monitoring
from backend.playback_reference import suggest_reference, reference_segment, restore_reference
from backend.project_codec import dump_project, load_project


class PlaybackReferenceTests(unittest.TestCase):
    def test_six_samples_use_current_data_and_actual_solver(self):
        reports=[]
        with tempfile.TemporaryDirectory() as work, patch.dict('os.environ',{'WQ_PROJECT_HOME':work}):
            with TestClient(app) as client:
                for i,path in enumerate(sorted(Path('data/samples').glob('*.csv'))):
                    with self.subTest(sample=path.name):
                        sid=client.get('/api/session').json()['session_id'];h={'X-Session-Id':sid}
                        session=STORE.get(sid)
                        session.df,_=parse_monitoring(path.read_bytes(),path.name)
                        original=session.df.copy(deep=True)
                        length=600.+i*100
                        def post(url,data):
                            response=client.post('/api/river/'+url,headers=h,json=data)
                            self.assertEqual(response.status_code,200,response.text[:400]);return response.json()
                        post('init',{'n_cross_sections':12,'parameters':{'length_m':length,'width_avg_m':20.,'depth_avg_m':2.,'ammonia_nh3_mgL':.2}})
                        post('hydro',{'duration_h':.5,'upstream_flow_m3s':4.,'downstream_stage_m':2.})
                        post('pollution',{'simulation_hours':.5,'load_kg':1.,'duration_min':5.,'chainage_m':length*.3,'wq_refinement':2})
                        before=client.get('/api/river/playback',headers=h).json()
                        self.assertTrue(before['mapping']['available'])
                        self.assertEqual(before['mapping']['confirmation'],'automatic_scenario_preview_unconfirmed')
                        self.assertNotIn('playback_reference',session.river_ui)
                        candidate=client.get('/api/river/playback/reference',headers=h).json()
                        self.assertIn('reference_path',candidate)
                        request={'start':candidate['start'],'end':candidate['end'],'reverse':False,'confirmed':True,'analysis_revision':session.analysis_revision,'project_id':None}
                        post('playback/reference',request)
                        after=client.get('/api/river/playback',headers=h).json()
                        self.assertTrue(after['mapping']['available'],after['mapping']['reason'])
                        self.assertTrue(after['mapping']['complete'])
                        np.testing.assert_allclose(after['mapping']['positions'][0],candidate['reference_path'][0],rtol=0,atol=1e-10)
                        for key in ('concentrations','times','chainage'):
                            self.assertEqual(before[key],after[key])
                        pd.testing.assert_frame_equal(original,session.df)
                        package=dump_project(session,'通用地图流程验收')
                        restored=load_project(package)
                        self.assertEqual(restore_reference(restored.state['df'],restored.state['river_ui']['playback_reference'])['reference_path'],candidate['reference_path'])
                        post('playback/reference',{**request,'reverse':True})
                        reversed_map=client.get('/api/river/playback',headers=h).json()['mapping']
                        np.testing.assert_allclose(reversed_map['positions'][0],candidate['reference_path'][-1],rtol=0,atol=1e-10)
                        response=client.post('/api/river/playback/reference',headers=h,json={**request,'analysis_revision':session.analysis_revision+1})
                        self.assertEqual(response.status_code,409)
                        reports.append(dict(sample=path.name,start=candidate['start'],end=candidate['end'],length_m=length,mapped=after['mapping']['mapped_nodes']))
                        output=Path('output/map-generalization-data');output.mkdir(parents=True,exist_ok=True)
                        (output/(path.stem[:3]+'.wqproj')).write_bytes(package)
                        STORE.drop(sid)
        Path('output').mkdir(exist_ok=True)
        Path('output/map-generalization-six.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2),encoding='utf-8')

    def test_new_coordinates_and_gaps_do_not_use_sample_names_or_join_missing_gps(self):
        for lat,lon in [(42.,-71.),(-33.,151.),(0.,30.)]:
            outward=np.linspace(0,.02,50)
            df=pd.DataFrame({'latitude':lat+np.r_[outward,outward[::-1]],'longitude':lon,
                'datetime':pd.date_range('2025-01-01',periods=100,freq='5s')})
            df.loc[30,'latitude']=np.nan
            c=suggest_reference(df)
            self.assertFalse(c['start']<=30<=c['end'])
            self.assertLess(abs(c['reference_path'][0][0]-lat),.03)
            with self.assertRaises(ValueError):reference_segment(df,0,99)

    def test_invalid_reference_and_no_gps_do_not_fabricate_map(self):
        df=pd.DataFrame({'datetime':pd.date_range('2025-01-01',periods=10,freq='5s')})
        with self.assertRaises(ValueError):suggest_reference(df)
        for config in (None,{},'bad',{'confirmed':True,'start':True,'end':9}):
            with self.assertRaises(ValueError):restore_reference(df,config)


if __name__=='__main__':unittest.main()
