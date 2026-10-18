"""评审可移植性、通用河段、断面几何及站点证据契约。"""
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from river_model.config import RiverConfig, YayaoConfig
from river_model.cross_sections import generate_yayao_cross_sections
from river_model.parameters import configure_river
from river_model.parameter_estimator import RiverParameterEstimator
from river_model import tools, hydro_engine
from water_quality_mcp.src.water_quality_mcp import national_station as stations
from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig


class PortableRiverTests(unittest.TestCase):
    def test_default_has_no_real_river_identity_or_coordinates(self):
        cfg = RiverConfig()
        self.assertIs(YayaoConfig, RiverConfig)
        self.assertNotIn('雅瑶', cfg.river_name)
        self.assertIsNone(cfg.center_lat)
        self.assertIsNone(cfg.catchment_area_km2)
        self.assertEqual(hydro_engine.detect_engine(), 'pure_python')
        self.assertNotIn('hecras_bridge', hydro_engine.run_hydrodynamic.__code__.co_names)

    def test_no_gps_does_not_fabricate_river_location(self):
        with self.assertRaisesRegex(ValueError, 'GPS'):
            RiverParameterEstimator().estimate(pd.DataFrame({'ammonia_nitrogen':[1.]}))

    def test_three_river_geometry_scalars_remain_independent(self):
        for length, width, depth in ((400.,8.,1.), (1800.,40.,3.), (6000.,150.,5.)):
            cfg = configure_river(RiverConfig(), {'length_m':length,'width_avg_m':width,'depth_avg_m':depth})
            section = generate_yayao_cross_sections(3,length,0.,0.,cfg.bottom_width_m,
                cfg.bank_elevation_m,cfg.side_slope)[1]
            self.assertAlmostEqual(section.top_width(depth), width)
            self.assertAlmostEqual(cfg.length_km, length/1000)

    def test_partial_wetted_trapezoid_matches_independent_analytic_geometry(self):
        section = generate_yayao_cross_sections(3,100,0,0,10,5,2)[1]
        for h in (.1, 1., 2., 4.):
            self.assertAlmostEqual(section.flow_area(h), 10*h + 2*h*h)
            self.assertAlmostEqual(section.wetted_perimeter(h), 10 + 2*h*np.sqrt(5))
            self.assertAlmostEqual(section.top_width(h), 10+4*h)
        self.assertEqual(section.wetted_perimeter(0), 0)

    def test_invalid_manual_configuration_is_rejected(self):
        for override in ({'length_m':float('nan')}, {'width_avg_m':0}, {'unknown':1}, {'mannings_n':-1},
                         {'k_nitrification':-1}, {'water_temp_C':60}):
            with self.assertRaises(ValueError):
                configure_river(RiverConfig(), override)

    def test_steady_water_levels_are_stable_and_independent_of_vertical_datum(self):
        solutions=[]
        for offset in (0.,1000.):
            cfg=RiverConfig(bed_elevation_avg_m=offset,depth_avg_m=2.)
            sections=generate_yayao_cross_sections(5,400.,offset,.0001,10.,offset+5.)
            result=hydro_engine.run_hydrodynamic(cfg,sections,upstream_flow_m3s=2.,
                downstream_stage_m=offset+2.,duration_h=.5,dt_s=60.,output_interval_min=5.)
            self.assertLess(np.max(abs(result.water_level-result.water_level[0])),1e-6)
            solutions.append(result)
        np.testing.assert_allclose(solutions[1].water_level-1000,solutions[0].water_level,atol=1e-7)
        np.testing.assert_allclose(solutions[1].area,solutions[0].area,atol=1e-7)

    def test_hydrodynamics_covers_nonintegral_requested_duration(self):
        cfg=RiverConfig()
        sections=generate_yayao_cross_sections(5)
        result=hydro_engine.run_hydrodynamic(cfg,sections,upstream_flow_m3s=2.,
            downstream_stage_m=2.,duration_h=.57,dt_s=77.,output_interval_min=5.)
        self.assertAlmostEqual(result.t[-1],.57,places=12)
        self.assertTrue(np.all(np.diff(result.t)>0))

    def test_gps_mapping_uses_only_current_dataframe(self):
        df=pd.DataFrame({'latitude':[31.,31.001,31.002,31.003],
                         'longitude':[118.,118.001,118.002,118.003]})
        with patch('river_model.parameter_estimator.build_chainage_gps_mapping',return_value={}) as mapping:
            RiverParameterEstimator()._extract_gps_info(df)
        self.assertIs(mapping.call_args.args[0],df)

    def test_manual_model_api_retains_supplied_river(self):
        with TestClient(app) as client, patch.object(tools, 'get_engine_info', return_value='portable'):
            sid = client.get('/api/session').json()['session_id']
            try:
                response = client.post('/api/river/init', headers={'X-Session-Id':sid}, json={
                    'n_cross_sections':5, 'parameters':{'river_name':'另一条河流','length_m':1800.,
                        'width_avg_m':80.,'depth_avg_m':4.,'avg_discharge_m3s':20.,
                        'water_temp_C':15.,'k_nitrification':0.,'bod_cbod_mgL':0.}})
                self.assertEqual(response.status_code, 200, response.text)
                cfg = STORE.get(sid).get_river_state().config
                self.assertEqual(cfg.river_name,'另一条河流')
                self.assertEqual(cfg.length_m,1800.)
                self.assertEqual(cfg.water_temp_C,15.)
                self.assertEqual(cfg.k_nitrification,0.)
                self.assertEqual(cfg.param_sources['width_avg_m'],'user_scenario')
                self.assertNotIn('width_avg_m',cfg.param_confidence)
                self.assertEqual(client.post('/api/river/hydro', headers={'X-Session-Id':sid},
                    json={'tidal_amplitude_m':.8}).status_code, 422)
            finally:
                STORE.drop(sid)


class StationEvidenceTests(unittest.TestCase):
    def test_offline_radius_and_keyword_filters_never_expand_scope(self):
        self.assertEqual(stations._search_static_db('江苏省',lat=0.,lon=0.,radius_km=1), [])
        self.assertEqual(stations._search_static_db('江苏省',keyword='不存在的断面'), [])
    def test_manual_live_result_reaches_session_and_summary(self):
        payload={'站点数量':1,'站点列表':[{'站名':'测试断面','氨氮':.3,'时间':'2026-10-18 08:00'}],
                 '含实时监测值':True,'数据来源':'HTTP API 实时','数据时效':'本次抓取'}
        with TestClient(app) as client, patch('backend.routers.stations.get_national_station_data',return_value=json.dumps(payload)):
            sid=client.get('/api/session').json()['session_id']
            try:
                session=STORE.get(sid)
                session.df=pd.DataFrame({'ammonia_nitrogen':[1.], 'dissolved_oxygen':[4.],
                    'datetime':pd.to_datetime(['2025-08-01'])})
                r=client.get('/api/stations?province=江苏省&live=true',headers={'X-Session-Id':sid})
                self.assertEqual(r.status_code,200)
                self.assertTrue(session.national_station_result['含实时监测值'])
                summary=client.get('/api/analysis/comprehensive',headers={'X-Session-Id':sid}).json()
                self.assertTrue(summary['国控站点查询']['含实时监测值'])
                self.assertEqual(summary['国控站点查询']['查询条件']['省份'],'江苏省')
                session.reset_analysis()
                self.assertIsNone(session.national_station_result)
            finally:
                STORE.drop(sid)

    def test_unmatched_keyword_stays_empty_and_cache_uses_whole_province(self):
        with patch.object(stations,'_fetch_live_via_api',return_value=[{'站名':'甲','省份':'江苏省','氨氮':1.}]) as fetch:
            data=json.loads(stations.get_national_station_data(province='江苏省',keyword='不存在的断面',live=True))
        self.assertEqual(data['站点列表'],[])
        self.assertEqual(fetch.call_args.kwargs['station_name'],'')

    def test_coordinate_matching_cannot_cross_provinces(self):
        self.assertIsNone(stations._match_coordinates('南京下关','广东省'))
        self.assertIsNotNone(stations._match_coordinates('南京下关','江苏省'))

    def test_trace_requests_live_but_monitors_are_not_ranked_polluters(self):
        cfg=AnalysisConfig()
        cfg.enabled_sources=['national_station']
        payload={'站点列表':[{'站名':'国控断面','氨氮':.1}],'含实时监测值':True,'数据来源':'本次 HTTP'}
        with patch('water_quality_mcp.src.water_quality_mcp.trace_engine._infer_province',return_value='江苏省'), \
             patch.object(stations,'get_national_station_data',return_value=json.dumps(payload)) as fetch:
            result=json.loads(multi_source_trace(32.,118.,config=cfg))
        self.assertTrue(fetch.call_args.kwargs['live'])
        self.assertEqual(fetch.call_args.kwargs['province'],'江苏省')
        self.assertTrue(result['国控监测背景']['含实时监测值'])
        self.assertFalse(result.get('综合污染源列表'))


class HeatMapTests(unittest.TestCase):
    def test_severity_map_includes_clean_observations_without_black_spots(self):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id']
            try:
                session=STORE.get(sid)
                session.df=pd.DataFrame({'latitude':[31.,31.001], 'longitude':[118.,118.001],
                    'ammonia_nitrogen':[.1,8.], 'dissolved_oxygen':[8.,0.],
                    'datetime':pd.to_datetime(['2025-08-01','2025-08-02'])})
                result=client.get('/api/charts/trace-map',headers={'X-Session-Id':sid})
                self.assertEqual(result.status_code,200)
                points=result.json()['heat']
                self.assertEqual(len(points),2)
                self.assertGreater(points[1][2],points[0][2])
                self.assertEqual(result.json()['clusters'],[])
            finally:
                STORE.drop(sid)


class InverseGuardsTests(unittest.TestCase):
    def test_unobserved_or_no_signal_case_does_not_claim_a_source(self):
        from river_model.inverse import fit_single_source
        from test_transport_regressions import still_hydro
        cfg=RiverConfig(ammonia_nh3_mgL=0.,bod_cbod_mgL=0.)
        h=still_hydro([0.,100.,200.])
        for times,start in (([0.,.001,.002,.003],.1), ([.01,.02,.03,.04],0.)):
            result=fit_single_source(cfg,h,times,[50.]*4,[0.]*4,.01,[0.,100.],(0.,1.),source_start_h=start)
            self.assertEqual(result['status'],'unidentifiable')
            self.assertIsNone(result['estimate'])

    def test_tide_and_nonpositive_error_are_rejected(self):
        from river_model.inverse import fit_single_source
        from test_transport_regressions import still_hydro
        h=still_hydro([0.,100.,200.])
        for amp,sigma in ((.8,.01),(0.,0.)):
            h.params['tidal_amplitude_m']=amp
            with self.assertRaises(ValueError):
                fit_single_source(RiverConfig(),h,[.01,.02,.03,.04],[50.]*4,[1.]*4,sigma,[100.])


if __name__ == '__main__':
    unittest.main()
