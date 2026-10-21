"""显式参考线/时间配准的独立解析场与错误场景，不代表现场配准已核验。"""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from fastapi.testclient import TestClient
from backend.main import app
from backend.session import STORE
from river_model.state import WQResult
from river_model.cruise_alignment import align_cruise, audit_track, project_points
from scripts.validate_refined_inverse import uniform_hydro


def fixture():
    times = np.array([0., 1., 2.])
    chainage = np.array([0., 500., 1200.])
    field = 1+2*times[:,None]+.005*chainage[None,:]+.001*times[:,None]*chainage[None,:]
    wq = WQResult(t=times, chainage=chainage, ammonia=field, dissolved_oxygen=field)
    longitude = np.array([0., .003, .006, .003, 0.])
    x = 6371000*np.radians(longitude)  # 与被测投影独立的赤道精确球面弧长。
    t = np.arange(5)/12
    df = pd.DataFrame(dict(datetime=pd.date_range('2025-01-01',periods=5,freq='5min'),
        latitude=np.zeros(5),longitude=longitude,ammonia_nitrogen=1+2*t+.005*x+.001*t*x))
    config = dict(reference_path=[[0.,0.],[0.,.009]], model_origin='2025-01-01T00:00:00+08:00',
        monitoring_timezone='Asia/Shanghai', confirmed=True, reference_note='受控赤道参考线，不是实际测绘')
    return df, wq, config, x


class AlignmentMathTests(unittest.TestCase):
    def setUp(self):
        self.df, self.wq, self.config, self.x = fixture()

    def run_alignment(self, **kwargs):
        return align_cruise(self.df, self.wq, **(self.config | kwargs))

    def test_return_voyage_keeps_spatial_chainage_and_paired_time(self):
        result = self.run_alignment()
        self.assertEqual(result['summary']['matched'], 5)
        np.testing.assert_allclose([r['chainage_m'] for r in result['rows']], self.x, atol=1e-7)
        self.assertLess(result['summary']['comparison']['rmse_mgL'], 1e-10)
        self.assertLess(result['rows'][-1]['chainage_m'], 1e-8)
        self.assertGreater(audit_track(self.df)['travelled_distance_m'], self.wq.chainage[-1])
        self.assertTrue(audit_track(self.df)['warnings'])

    def test_absolute_timezones_and_clock_offset_are_explicit(self):
        equivalent = self.run_alignment(model_origin='2024-12-31T16:00:00+00:00')
        shifted = self.run_alignment(time_offset_s=300.)
        self.assertEqual(equivalent['rows'][0]['time_h'], 0.)
        self.assertAlmostEqual(shifted['rows'][0]['time_h'], 1/12)
        with self.assertRaisesRegex(ValueError, '明确时区'):
            self.run_alignment(model_origin='2025-01-01T00:00')
        with self.assertRaisesRegex(ValueError, '时区无效'):
            self.run_alignment(monitoring_timezone='No/SuchTimezone')

    def test_missing_date_is_not_filled_with_today(self):
        self.df['datetime'] = self.df.datetime.astype(object)
        self.df.loc[1,'datetime'] = '00:05:00'
        self.assertEqual(self.run_alignment()['rows'][1]['status'], 'invalid_time')

    def test_off_reference_and_unobserved_coordinates_are_excluded(self):
        self.df.loc[1,'latitude'] = .001
        result = self.run_alignment(max_distance_m=10.)
        self.assertEqual(result['rows'][1]['status'], 'off_reference')
        self.df['__observed_coordinates'] = True
        self.df.loc[2,'__observed_coordinates'] = False
        self.assertEqual(self.run_alignment()['rows'][2]['status'], 'invalid_gps')

    def test_intersection_is_ambiguous_instead_of_arbitrary_branch(self):
        self.df = self.df.iloc[:1].copy()
        result = self.run_alignment(reference_path=[[-.001,-.001],[.001,.001],[-.001,.001],[.001,-.001]])
        self.assertEqual(result['rows'][0]['status'], 'ambiguous_position')
        self.assertIsNone(result['summary']['comparison'])

    def test_duplicate_observations_do_not_increase_comparison_count(self):
        self.df = pd.concat([self.df, self.df.iloc[[0]]],ignore_index=True)
        result = self.run_alignment()
        self.assertEqual(result['summary']['matched'], 5)
        self.assertEqual(result['rows'][-1]['status'], 'duplicate_observation')

    def test_interpolated_measurement_is_not_an_observation_and_zero_is_valid(self):
        self.df['__observed_ammonia_nitrogen'] = True
        self.df.loc[1,'__observed_ammonia_nitrogen'] = False
        self.df.loc[2,'ammonia_nitrogen'] = 0.
        result = self.run_alignment()
        self.assertEqual(result['rows'][1]['status'], 'unobserved_measurement')
        self.assertEqual(result['rows'][2]['status'], 'matched')
        self.assertEqual(result['rows'][2]['measured_mgL'], 0.)

    def test_outside_model_time_or_space_is_not_extrapolated(self):
        self.assertEqual(self.run_alignment(model_origin='2025-01-02T00:00:00+08:00')['summary']['matched'], 0)
        self.wq.chainage = np.array([0., 50., 100.])
        self.assertEqual(self.run_alignment()['rows'][2]['status'], 'outside_chainage')

    def test_fast_gps_jump_is_a_review_flag_not_corrected_coordinates(self):
        self.df.datetime = pd.date_range('2025-01-01',periods=5,freq='1s')
        self.assertIn('gps_jump', [r['status'] for r in self.run_alignment()['rows']])

    def test_unconfirmed_or_degenerate_reference_is_rejected(self):
        for changes in (dict(confirmed=False), dict(reference_note=''),
                        dict(reference_path=[[0.,0.],[0.,0.]]), dict(time_offset_s=True)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_alignment(**changes)

    def test_dateline_projection_does_not_wrap_around_the_earth(self):
        x, error, ambiguous = project_points(np.array([[84.,180.]]), [[84.,179.99],[84.,-179.99]], 5.)
        self.assertTrue(100 < x[0] < 130)
        self.assertLess(error[0], 1e-7)
        self.assertFalse(ambiguous[0])

    def test_original_table_and_model_arrays_remain_unchanged(self):
        before, field = self.df.copy(deep=True), self.wq.ammonia.copy()
        self.run_alignment()
        pd.testing.assert_frame_equal(self.df,before)
        np.testing.assert_array_equal(self.wq.ammonia,field)

    def test_automatic_river_length_does_not_use_a_return_voyage(self):
        from unittest.mock import patch
        from river_model.parameter_estimator import RiverParameterEstimator
        with patch.object(RiverParameterEstimator,'_identify_river') as external:
            with self.assertRaisesRegex(ValueError,'不能用累计航程推断河长'):
                RiverParameterEstimator().estimate(self.df)
            external.assert_not_called()


class AlignmentApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.s = STORE.create()
        self.headers = {'X-Session-Id':self.s.id}
        df,wq,self.config,_ = fixture()
        self.s.df = df
        state = self.s.get_river_state()
        state.hydro_result = uniform_hydro(1200.,8.,1.,.25,3)
        state.wq_result = wq

    def tearDown(self):
        STORE.drop(self.s.id)
        self.client.close()

    def test_api_updates_config_only_and_summary_receives_current_evidence(self):
        before = self.s.river_state.wq_result.ammonia.copy()
        self.s.analysis_status = 'succeeded'; self.s.analysis_done = True
        response = self.client.post('/api/river/alignment',headers=self.headers,json=self.config)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['summary']['matched'],5)
        self.assertEqual(self.s.analysis_status,'stale')
        np.testing.assert_array_equal(self.s.river_state.wq_result.ammonia,before)
        summary = self.client.get('/api/analysis/comprehensive',headers=self.headers).json()
        self.assertEqual(summary['走航时空配准']['matched'],5)
        # 后续模型变化时GET与报告重算，不能沿用缓存误差。
        self.s.river_state.wq_result.ammonia += 2
        latest = self.client.get('/api/river/alignment?limit=1',headers=self.headers).json()
        self.assertAlmostEqual(latest['summary']['comparison']['bias_mgL'],2.)
        self.assertEqual(len(latest['rows']),1)

    def test_invalid_request_preserves_previous_config(self):
        self.client.post('/api/river/alignment',headers=self.headers,json=self.config)
        before = deepcopy(self.s.river_ui)
        for changes in (dict(confirmed=False),dict(model_origin='2025-01-01T00:00:00'),
                        dict(reference_path=[[0.,0.],[0.,0.]]),dict(unexpected=1)):
            response = self.client.post('/api/river/alignment',headers=self.headers,json=self.config | changes)
            self.assertIn(response.status_code,(400,422),response.text)
            self.assertEqual(self.s.river_ui,before)

    def test_missing_configuration_does_not_fabricate_mapping(self):
        response = self.client.get('/api/river/alignment',headers=self.headers)
        self.assertEqual(response.json()['status'],'not_configured')
        self.assertNotIn('comparison',response.json()['summary'])

    def test_summary_reports_endpoint_distance_without_assuming_close_endpoints(self):
        self.s.df.loc[self.s.df.index[-1],'longitude'] = .02
        summary = self.client.get('/api/analysis/comprehensive',headers=self.headers).json()
        self.assertAlmostEqual(summary['空间分析']['首末直线距离_m'],6371000*np.radians(.02),places=2)
        self.assertNotIn('往返起终点接近',summary['空间分析']['说明'])

    def test_return_voyage_map_uses_reference_line_after_explicit_configuration(self):
        self.s.river_state.wq_result.chainage = np.array([0.,500.,900.])
        before = self.client.get('/api/river/playback',headers=self.headers).json()
        self.assertFalse(before['mapping']['available'])
        saved = self.client.post('/api/river/alignment',headers=self.headers,json=self.config)
        self.assertEqual(saved.status_code,200,saved.text)
        after = self.client.get('/api/river/playback',headers=self.headers).json()
        self.assertTrue(after['mapping']['available'])
        self.assertAlmostEqual(after['mapping']['positions'][2][1],900/6371000*180/np.pi,places=8)

    def test_project_roundtrip_preserves_config_and_recomputes_comparison(self):
        from backend.project_codec import dump_project, load_project
        self.client.post('/api/river/alignment',headers=self.headers,json=self.config)
        raw = dump_project(self.s,'配准受控项目')
        restored = load_project(raw)
        self.assertEqual(restored.state['river_ui']['alignment'],self.s.river_ui['alignment'])
        from backend.session import Session
        from backend.cruise_alignment import alignment_result
        clone = Session(id='restored-alignment')
        for key,value in restored.state.items():
            setattr(clone,key,value)
        clone.river_state.wq_result.ammonia += 1
        self.assertAlmostEqual(alignment_result(clone)['summary']['comparison']['bias_mgL'],1.)


if __name__ == '__main__':
    unittest.main()
