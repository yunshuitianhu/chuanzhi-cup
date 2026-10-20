"""正演独立参照：复氧解析解、关闭复氧和实际数值分辨率。"""
import sys
import unittest
from pathlib import Path
import json

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.config import RiverConfig
from river_model.state import HydroResult
from river_model.wq_engine import solve_water_quality


def uniform_hydro(nodes=11, length=200., speed=0., width=20., depth=2.):
    area = np.full((1,nodes),width*depth)
    return HydroResult(t=np.array([0.]), chainage=np.linspace(0,length,nodes),
        water_level=np.full_like(area,depth), area=area,
        velocity=np.full_like(area,speed), discharge=area*speed)


class ForwardConfigurationTests(unittest.TestCase):
    def config(self, **updates):
        return RiverConfig(width_avg_m=20.,depth_avg_m=2.,depth_range_m=(2.,2.),
            ammonia_nh3_mgL=0.,bod_cbod_mgL=0.,k_sediment_oxygen_demand=0.,
            dissolved_oxygen_mgL=2.,do_saturation_mgL=8.,water_temp_C=20.,**updates)

    def test_zero_reaeration_preserves_closed_water_oxygen_without_any_mock(self):
        cfg=self.config(k_reaeration_base=0.)
        result=solve_water_quality(cfg,uniform_hydro(),point_sources=[],simulation_hours=6.,dt_s=60.)
        np.testing.assert_array_equal(result.dissolved_oxygen,2.)

    def test_configured_reaeration_matches_the_exponential_reference(self):
        # 无对流的均匀闭域，只存在解析的纯复氧；原实现对系数更改完全无响应。
        for base in (2.66,10.64):
            with self.subTest(base=base):
                result=solve_water_quality(self.config(k_reaeration_base=base),uniform_hydro(),
                    point_sources=[],simulation_hours=6.,dt_s=60.)
                rate=base*.001**.67/2.**1.85
                expected=8.-6.*np.exp(-rate*6./24.)
                np.testing.assert_allclose(result.dissolved_oxygen[-1],expected,rtol=2e-12,atol=1e-12)

    def test_negative_reaeration_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'反应参数'):
            solve_water_quality(self.config(k_reaeration_base=-1.),uniform_hydro(),
                point_sources=[],simulation_hours=.01)

    def test_adaptive_substeps_are_reported_without_changing_input_hydro(self):
        hydro=uniform_hydro(nodes=21,speed=.4)
        before=hydro.discharge.copy()
        result=solve_water_quality(self.config(),hydro,point_sources=[],simulation_hours=1/60,dt_s=60.)
        info=result.params['numerical_diagnostics']
        self.assertEqual(info['requested_dt_s'],60.)
        self.assertGreater(info['transport_substeps'],1)
        self.assertLess(info['actual_max_dt_s'],60.)
        self.assertLessEqual(result.params['max_transport_stability'],.9+1e-12)
        self.assertGreaterEqual(info['dispersion_min_m2s'],0.)
        np.testing.assert_array_equal(before,hydro.discharge)

    def test_uniform_reaction_convergence_really_changes_actual_steps(self):
        from scripts.validate_steady_forward import reaction_benchmark
        report=reaction_benchmark()
        self.assertTrue(all(report['checks'].values()))
        self.assertEqual([r['numerics']['actual_max_dt_s'] for r in report['rows']],[3600.,1800.,900.])
        self.assertTrue(all(3.5<p<4.5 for p in report['observed_orders']))

    def test_comprehensive_summary_preserves_numerics_and_legacy_unknowns(self):
        from river_model.state import RiverModelState, WQResult
        from water_quality_mcp.src.water_quality_mcp.analysis import build_comprehensive_summary
        from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState
        h=uniform_hydro()
        w=WQResult(t=np.array([0.,1.]),chainage=h.chainage,
            ammonia=np.ones((2,len(h.chainage))),params={'pollutant_type':'ammonia',
                'numerical_diagnostics':{'requested_dt_s':60.,'actual_max_dt_s':2.}})
        state=RiverModelState(hydro_result=h,wq_result=w)
        summary=json.loads(build_comprehensive_summary(WaterQualityState(),state))
        self.assertEqual(summary['河道模型']['水质模拟']['数值诊断']['actual_max_dt_s'],2.)
        w.params.pop('numerical_diagnostics')
        summary=json.loads(build_comprehensive_summary(WaterQualityState(),state))
        self.assertEqual(summary['河道模型']['水质模拟']['数值诊断'],{})


if __name__=='__main__': unittest.main()
