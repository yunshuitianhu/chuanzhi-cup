"""HEC-RAS 文件契约：离线验证，不启动 COM，不冒充实机引擎验收。"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from river_model.config import YayaoConfig
from river_model.cross_sections import generate_yayao_cross_sections
from research.legacy_hecras.hecras_bridge import _build_plan_file, _build_unsteady_flow_file, _extract_hecras_results


class HecIoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.cfg = YayaoConfig(length_m=200., bed_elevation_avg_m=0.)

    def fixture(self, area=True, time=True, steady=False):
        path=self.path/'fixture.hdf'
        with h5py.File(path,'w') as h:
            if steady:
                base=h.create_group('Results/Steady/Output/Output Blocks/Base Output/Steady Profiles')
                shape=(1,3)
            else:
                base=h.create_group('Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series')
                shape=(3,3)
                if time: base['Time']=np.array([0., .5/24, 1./24])
            xs=base.create_group('Cross Sections')
            xs['Water Surface']=np.ones(shape)
            xs['Flow']=np.tile([1.,-2.,3.],(shape[0],1))
            if area: xs['Flow Area']=np.full(shape,10.)
            attrs=np.array([(b'0',),(b'100',),(b'200',)],dtype=[('RS','S12')])
            h.create_dataset('Geometry/Cross Sections/Attributes',data=attrs)
        return path

    def extract(self, path, **kwargs):
        return _extract_hecras_results(str(path),self.cfg,6.9,.5,.8,12.42,1.,30.,str(self.path),**kwargs)

    def test_explicit_unsteady_plan_uses_flow_u01_and_correct_clock(self):
        p=_build_plan_file(str(self.path),25.,30.,5.,unsteady=True)
        text=Path(p).read_text()
        self.assertIn('Flow File=u01\n',text)
        self.assertNotIn('Flow File=f01',text)
        self.assertIn('Simulation Date=01JAN2024,0000,02JAN2024,0100',text)
        self.assertIn('Run UNet=1',text)
        self.assertIn('Computation Interval=30SEC',text)
        self.assertIn('Mapping Interval=5MIN',text)

    def test_default_steady_mode_is_retained(self):
        p=_build_plan_file(str(self.path),.5,60.,30.)
        self.assertIn('Flow File=f01',Path(p).read_text())

    def test_invalid_clock_and_interval_are_rejected(self):
        for args in ((.501,30.,5.),(1.,17.,5.),(1.,60.,.5)):
            with self.subTest(args=args),self.assertRaises(ValueError):
                _build_plan_file(str(self.path),*args,unsteady=True)

    def test_boundary_does_not_invent_upstream_flow_variation(self):
        p=_build_unsteady_flow_file(str(self.path),self.cfg,6.9,.5,.8,12.42,.5,300.)
        lines=Path(p).read_text().splitlines()
        index=next(i for i,line in enumerate(lines) if line.startswith('Flow Hydrograph='))
        values=[float(lines[index+1][i:i+8]) for i in range(0,len(lines[index+1]),8)]
        self.assertEqual(values,[6.9]*7)

    def test_actual_days_become_hours_and_signed_q_over_area(self):
        result=self.extract(self.fixture(),require_unsteady=True)
        np.testing.assert_allclose(result.t,[0.,.5,1.])
        np.testing.assert_allclose(result.chainage,[0.,100.,200.])
        np.testing.assert_allclose(result.velocity[0],[.3,-.2,.1])
        self.assertTrue(np.all(result.area>0))

    def test_missing_area_uses_supplied_geometry_instead_of_zero(self):
        sections=generate_yayao_cross_sections(n_total=3,channel_length_m=200.,
                                              bed_elevation_avg_m=0.,bed_slope=0.)
        result=self.extract(self.fixture(area=False),cross_sections=sections)
        expected=sections[0].flow_area(1.)
        np.testing.assert_allclose(result.area,expected)
        self.assertEqual(result.params['area_source'],'derived_from_supplied_cross_sections')

    def test_missing_area_without_geometry_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'过水面积'):
            self.extract(self.fixture(area=False))

    def test_missing_actual_time_is_not_filled(self):
        with self.assertRaisesRegex(ValueError,'时间轴'):
            self.extract(self.fixture(time=False))

    def test_steady_output_cannot_satisfy_unsteady_request(self):
        with self.assertRaisesRegex(ValueError,'恒定流替代'):
            self.extract(self.fixture(steady=True),require_unsteady=True)


if __name__=='__main__':
    unittest.main()
