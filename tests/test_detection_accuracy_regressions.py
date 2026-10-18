"""已知规则与可控时空场景校验；不代表现场分类准确率。"""
import json
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
from water_quality_mcp.src.water_quality_mcp import analysis as a
from water_quality_mcp.src.water_quality_mcp import detection_support as support
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState


def observations(positions=(0,1,2,24,25,26), lat=23, times=None):
    n=len(positions)
    return pd.DataFrame({'latitude':np.full(n,lat),
        'longitude':113+np.asarray(positions)/(111195*np.cos(np.radians(lat))),
        'datetime':pd.date_range('2025-01-01',periods=n,freq='5s') if times is None else times,
        'ammonia_nitrogen':np.full(n,20.),'dissolved_oxygen':np.full(n,1.)})


class SeveritySupportTests(unittest.TestCase):
    def test_higher_threshold_weights_are_not_overwritten(self):
        np.testing.assert_array_equal(a._compute_exceedance_severity([2,3,4,5,6,10,11]),[1,1,1.5,1.5,2,2,3])

    def test_custom_threshold_mapping_order_does_not_change_results(self):
        config=AnalysisConfig(exceedance_severity_multipliers={10:4,3:1.5,5:2.5})
        np.testing.assert_array_equal(a._compute_exceedance_severity([2,4,6,11],config),[1,1.5,2.5,4])

    def test_ten_second_burst_is_not_thirty_minutes_of_persistence(self):
        factor=a._compute_temporal_persistence(observations([0,1,2]),[2,2,2])
        np.testing.assert_allclose(factor,10/1800)

    def test_nonexceeding_point_does_not_receive_neighbor_risk_boost(self):
        df=observations([0,1,2,3])
        self.assertEqual(a._compute_temporal_persistence(df,[2,.5,2,2])[1],0)

    def test_duplicate_timestamps_do_not_invent_persistence(self):
        df=observations([0]*10,times=pd.to_datetime(['2025-01-01']*10))
        np.testing.assert_array_equal(a._compute_temporal_persistence(df,np.full(10,2)),0)

    def test_duplicate_rows_do_not_increase_support(self):
        df=observations()
        base=a._compute_temporal_persistence(df,np.full(len(df),2))
        doubled=pd.concat([df,df.iloc[[2]]]*2,ignore_index=True)
        factor=a._compute_temporal_persistence(doubled,np.full(len(doubled),2))
        np.testing.assert_allclose(factor[:len(df)],base)

    def test_denser_sampling_inside_constant_interval_preserves_duration(self):
        sparse_seconds=np.arange(0,601,60)
        dense_seconds=np.unique(np.r_[sparse_seconds,np.arange(70,111)])
        values=[]
        for seconds in (sparse_seconds,dense_seconds):
            df=observations(np.zeros(len(seconds)),times=pd.to_datetime('2025-01-01')+pd.to_timedelta(seconds,unit='s'))
            scores=np.where((seconds>=60)&(seconds<=300),2.,.5)
            result=a._compute_temporal_persistence(df,scores,window_min=10,max_gap_min=2)
            values.append(result[np.flatnonzero(seconds==300)[0]])
        self.assertAlmostEqual(values[0],values[1])

    def test_long_observation_gaps_are_not_filled_as_continuous_pollution(self):
        df=observations([0,1,2],times=pd.date_range('2025-01-01',periods=3,freq='10min'))
        np.testing.assert_array_equal(a._compute_temporal_persistence(df,[2,2,2]),0)

    def test_invalid_timestamps_and_one_point_are_safe(self):
        df=observations([0,1,2],times=pd.to_datetime([None]*3))
        np.testing.assert_array_equal(a._compute_temporal_persistence(df,[2,2,2]),0)
        np.testing.assert_array_equal(a._compute_temporal_persistence(observations([0]),[2],min_points=1),0)

    def test_nanosecond_and_microsecond_time_units_agree(self):
        df=observations()
        expected=a._compute_temporal_persistence(df,np.full(len(df),2))
        df['datetime']=df['datetime'].dt.as_unit('us')
        np.testing.assert_allclose(a._compute_temporal_persistence(df,np.full(len(df),2)),expected)

    def test_return_to_start_does_not_erase_spatial_extent(self):
        factor=a._compute_spatial_continuity(observations([0,20,40,20,0]),np.full(5,2))
        self.assertTrue(np.all(factor>.79))

    def test_copying_one_of_two_points_does_not_create_spatial_support(self):
        df=observations([0,20])
        duplicated=pd.concat([df,df.iloc[[1]]],ignore_index=True)
        np.testing.assert_array_equal(a._compute_spatial_continuity(duplicated,[2]*3),0)

    def test_missing_observation_sequence_breaks_continuity(self):
        df=observations([0,10,20,30]);df['__sample_sequence']=[0,1,3,4]
        np.testing.assert_array_equal(a._compute_spatial_continuity(df,[2]*4),0)

    def test_invalid_gps_and_time_gaps_break_continuity(self):
        df=observations([0,1,2,3,4]);df.loc[2,'latitude']=np.nan
        np.testing.assert_array_equal(a._compute_spatial_continuity(df,[2]*5),0)
        df=observations([0,1,2],times=pd.date_range('2025-01-01',periods=3,freq='10min'))
        np.testing.assert_array_equal(a._compute_spatial_continuity(df,[2]*3),0)


class MetricClusteringTests(unittest.TestCase):
    def detect(self,df,**kwargs):
        raw,black=a.find_black_spots(df,AnalysisConfig(**kwargs))
        return json.loads(raw),black

    def test_same_meter_geometry_clusters_consistently_across_latitudes(self):
        for lat in (0,23,60):
            result,_=self.detect(observations(lat=lat),dbscan_min_samples=3,dbscan_eps_m=30)
            self.assertEqual(result['聚类热点数'],1)

    def test_meter_radius_prevents_merging_physically_separate_groups(self):
        result,_=self.detect(observations([0,1,2,80,81,82]),dbscan_min_samples=3,dbscan_eps_m=30)
        self.assertEqual(result['聚类热点数'],2)

    def test_separate_visits_are_not_one_persistent_event(self):
        times=pd.to_datetime(['2025-01-01 00:00:00','2025-01-01 00:00:05','2025-01-01 00:00:10',
            '2025-01-01 01:00:00','2025-01-01 01:00:05','2025-01-01 01:00:10'])
        df=observations([0,1,2,0,1,2],times=times)
        self.assertEqual(self.detect(df,dbscan_min_samples=3)[0]['聚类热点数'],2)
        self.assertEqual(self.detect(df,dbscan_min_samples=3,dbscan_time_gap_min=None)[0]['聚类热点数'],1)

    def test_duplicate_only_records_do_not_create_a_hotspot(self):
        df=observations([0]*8,times=pd.to_datetime(['2025-01-01']*8))
        result,black=self.detect(df)
        self.assertEqual(result['黑点总数'],8)
        self.assertEqual(result['聚类热点数'],0)
        self.assertTrue((black.cluster_id==-1).all())
        self.assertEqual(result['聚类参数']['重复观测数'],7)

    def test_min_samples_two_is_respected(self):
        self.assertEqual(self.detect(observations([0,1]),dbscan_min_samples=2)[0]['聚类热点数'],1)

    def test_unknown_times_do_not_connect_two_known_events(self):
        df=observations(times=pd.to_datetime([None]*6))
        self.assertEqual(self.detect(df,dbscan_min_samples=3)[0]['聚类热点数'],0)

    def test_missing_gps_still_preserves_point_level_alerts(self):
        result,black=self.detect(observations().drop(columns=['latitude','longitude']))
        self.assertEqual(result['黑点总数'],6);self.assertTrue((black.cluster_id==-1).all())

    def test_input_order_does_not_change_chronological_scores(self):
        df=observations([0,1,2,100,101,102])
        _,a1=self.detect(df);_,b1=self.detect(df.sample(frac=1,random_state=5))
        np.testing.assert_allclose(a1.sort_index().black_score,b1.sort_index().black_score)
        np.testing.assert_array_equal(a1.sort_index().cluster_id,b1.sort_index().cluster_id)

    def test_resource_limit_is_explicit_without_losing_point_alerts(self):
        with patch.object(support,'MAX_NEIGHBOR_EDGES',1):
            result,black=self.detect(observations())
        self.assertEqual(result['黑点总数'],6)
        self.assertIn('过密',result['聚类提示']);self.assertTrue((black.cluster_id==-1).all())

    def test_timezone_is_not_removed_from_original_records(self):
        df=observations(times=pd.date_range('2025-01-01',periods=6,freq='5s',tz='Asia/Shanghai'))
        _,black=self.detect(df)
        self.assertEqual(str(black.datetime.dt.tz),'Asia/Shanghai')


class DetectionReportingTests(unittest.TestCase):
    def test_chart_colors_do_not_promote_raw_index_due_to_priority_weight(self):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id'];h={'X-Session-Id':sid}
            session=STORE.get(sid);session.df=observations()
            _,black=a.find_black_spots(session.df)
            black['nemerow_raw']=1.5;black['black_score']=100.
            session.black_spots=black
            try:
                chart=client.get('/api/charts/time-series',headers=h).json()
                trace=client.get('/api/charts/trace-map',headers=h).json()
                expected=a.get_severity_info(1.5)[2]
                self.assertTrue(all(s['color']==expected for s in chart['black_spots']))
                self.assertTrue(all(s['color']==expected for s in trace['top5']))
                self.assertTrue(all(c['color']==expected for c in trace['clusters']))
            finally:STORE.drop(sid)

    def test_raw_index_not_enhanced_priority_is_used_in_summary(self):
        df=observations();raw,black=a.find_black_spots(df)
        state=WaterQualityState();state.df=df;state.black_spots=black
        summary=json.loads(a.build_comprehensive_summary(state))['黑臭检测']
        top=black.nlargest(5,'black_score').iloc[0]
        self.assertEqual(summary['Top5最严重点位'][0]['内梅罗指数'],round(float(top.nemerow_raw),2))
        self.assertEqual(summary['Top5最严重点位'][0]['时空增强评分'],round(float(top.black_score),2))
        cluster=json.loads(raw)['聚类详情'][0]
        self.assertEqual(cluster['内梅罗指数_均值'],round(float(black.nemerow_raw.mean()),2))
        self.assertGreater(cluster['时空增强评分_均值'],cluster['内梅罗指数_均值'])

    def test_index_only_alert_is_not_called_additional_black_water(self):
        df=observations();df['ammonia_nitrogen']=3.;df['dissolved_oxygen']=4.
        result=json.loads(a.find_black_spots(df)[0])
        self.assertEqual(result['黑点总数'],0)
        self.assertEqual(result['非黑臭指数异常点数'],6)
        self.assertEqual(result.get('时空增强识别的额外黑点',0),0)

    def test_reanalysis_status_does_not_invalidate_the_river_model(self):
        with TestClient(app) as client:
            sid=client.get('/api/session').json()['session_id'];h={'X-Session-Id':sid}
            session=STORE.get(sid);session.df=observations()
            session.black_spots=session.df.copy();session.black_spots_json='{}'
            session.comprehensive_summary='old';session.captions={'map':'old'};revision=session.analysis_revision
            try:
                self.assertTrue(client.get('/api/data/status',headers=h).json()['detection_outdated'])
                response=client.post('/api/analysis/black-spots',headers=h)
                self.assertEqual(response.status_code,200)
                status=client.get('/api/data/status',headers=h).json()
                self.assertFalse(status['detection_outdated'])
                self.assertEqual(status['detection_algorithm_version'],support.DETECTION_VERSION)
                self.assertEqual(session.analysis_revision,revision)
                self.assertIsNone(session.comprehensive_summary)
                self.assertEqual(session.captions,{})
            finally:STORE.drop(sid)

    def test_agent_detection_clears_old_summary_and_captions(self):
        from backend.agent.tools import execute_tool
        from backend.session import Session
        session=Session(id='controlled-detection-check',df=observations(),
            comprehensive_summary='old',captions={'map':'old'})
        raw,failed=execute_tool('find_black_spots',{},session)
        self.assertFalse(failed,raw)
        self.assertIsNone(session.comprehensive_summary)
        self.assertEqual(session.captions,{})


if __name__=='__main__':unittest.main()
