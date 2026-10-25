"""动画导出与网页使用同一参考线门禁，受控数据，不运行外部服务。"""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from river_model.geo_mapping import checked_model_mapping
from river_model.state import RiverModelState,HydroResult,WQResult
from river_model.tools import tool_river_pollution_event
from backend.river_playback import playback_data


class CheckedExportMappingTests(unittest.TestCase):
    def setUp(self):
        self.df=pd.DataFrame({'datetime':pd.date_range('2026-10-24',periods=100,freq='10s'),
            'latitude':np.full(100,30.),'longitude':np.r_[np.linspace(120,120.012,50),np.linspace(120.012,120,50)]})
        self.config={'confirmed':True,'reference_note':'受控测试参考线，非实测',
            'reference_path':[[30.,120.],[30.,120.012]],'model_origin':'2026-10-24T00:00:00+08:00'}

    def test_return_trip_without_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'返航'):
            checked_model_mapping([0.,500.,1000.],self.df)

    def test_reference_must_cover_entire_model(self):
        with self.assertRaisesRegex(ValueError,'未覆盖'):
            checked_model_mapping([0.,1000.,1500.],self.df,self.config)
        with self.assertRaisesRegex(ValueError,'确认'):
            checked_model_mapping([0.,500.],self.df,{**self.config,'confirmed':False})

    def test_export_and_web_share_coordinates_and_skip_unregistered_return_trip(self):
        x=np.array([0.,500.,1000.]);t=np.array([0.,1.]);a=np.ones((2,3))
        hydro=HydroResult(t,x,a,a,a,a,{'run_id':'h1'})
        def result():
            return WQResult(t,x,ammonia=a.copy(),cbod=a.copy(),dissolved_oxygen=a*7,
                params={'chainage_m':500.,'source_start_h':0.,'duration_min':30.})
        captured=[]
        def animation(gps,*args,output_path,**kwargs):
            captured.append(np.column_stack([gps['lat_grid'],gps['lon_grid']]))
            Path(output_path).write_text('controlled-animation',encoding='utf-8')
        with tempfile.TemporaryDirectory() as temporary:
            state=RiverModelState(hydro_result=hydro,model_initialized=True,output_dir=temporary)
            with patch('river_model.tools.solve_water_quality',side_effect=lambda *a,**k:result()), \
                 patch('river_model.tools._PLOT_LOCK'), \
                 patch('river_model.tools.plot_concentration_contour',side_effect=lambda *a:None), \
                 patch('river_model.tools.plot_concentration_profile',side_effect=lambda *a:None), \
                 patch('river_model.tools.plot_concentration_timeseries',side_effect=lambda *a:None), \
                 patch('river_model.tools.plot_hydro_wq_dashboard',side_effect=lambda *a:None), \
                 patch('river_model.animation.build_pollution_map_animation',side_effect=animation):
                raw=json.loads(tool_river_pollution_event(state,monitoring_data=self.df))
                self.assertEqual(captured,[])
                self.assertTrue(any('返航' in w['原因'] for w in raw['文件生成说明']))
                raw=json.loads(tool_river_pollution_event(state,monitoring_data=self.df,alignment_config=self.config))
            self.assertEqual(len(captured),1)
            self.assertTrue(any(p.endswith('river_pollution_animation.html') for p in raw['输出文件']))
            web=playback_data(state,self.df,'ammonia','V',10,alignment_config=self.config)
            np.testing.assert_allclose(captured[0],web['mapping']['positions'])


if __name__=='__main__':unittest.main()
