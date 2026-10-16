"""坐标请求契约和反向流水质子步长的离线回归。"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from water_quality_mcp.src.water_quality_mcp import geo_tools as geo
from river_model.config import YayaoConfig
from river_model.state import HydroResult
from river_model import wq_engine


class GeoRegressionTests(unittest.TestCase):
    def test_poi_query_is_gcj_and_response_is_wgs(self):
        lat, lon = 23.12, 113.14
        glat, glon = geo.wgs84_to_gcj02(lat, lon)
        self.assertGreater(max(abs(glat - lat), abs(glon - lon)), 0.0001)
        response = Mock()
        response.json.return_value = {'status': '1', 'pois': [
            {'name': '测试企业', 'location': f'{glon},{glat}', 'distance': '10'},
            {'name': '缺少坐标', 'location': ''},
            {'name': '非法坐标', 'location': 'nan,inf'},
        ]}
        with patch.object(geo.requests, 'get', return_value=response) as get:
            pois = geo.search_nearby_pollution_sources(lat, lon, 'test-key', keywords=['工厂'])
        location = get.call_args.kwargs['params']['location']
        qlon, qlat = map(float, location.split(','))
        self.assertAlmostEqual(qlat, glat, places=5)
        self.assertAlmostEqual(qlon, glon, places=5)
        self.assertEqual(len(pois), 1)
        self.assertAlmostEqual(pois[0]['lat'], lat, places=6)
        self.assertAlmostEqual(pois[0]['lon'], lon, places=6)
        self.assertEqual(pois[0]['coordinate_system'], 'WGS-84')

    def test_reverse_geocode_converts_request(self):
        response = Mock()
        response.json.return_value = {'status': '1', 'regeocode': {
            'formatted_address': '测试地址', 'addressComponent': {'province': '广东省'}}}
        with patch.object(geo.requests, 'get', return_value=response) as get:
            result = json.loads(geo.reverse_geocode(23.12, 113.14, 'test-key'))
        glat, glon = geo.wgs84_to_gcj02(23.12, 113.14)
        qlon, qlat = map(float, get.call_args.kwargs['params']['location'].split(','))
        self.assertAlmostEqual(qlat, glat, places=5)
        self.assertAlmostEqual(qlon, glon, places=5)
        self.assertEqual(result['省份'], '广东省')

    def test_outside_china_coordinates_are_unchanged(self):
        self.assertEqual(geo.wgs84_to_gcj02(51.5, -0.1), (51.5, -0.1))
        self.assertEqual(geo.gcj02_to_wgs84(51.5, -0.1), (51.5, -0.1))


class CFLRegressionTests(unittest.TestCase):
    def test_positive_negative_and_mixed_flow_obey_cfl(self):
        for velocities in ([3, 3, 3], [-3, -3, -3], [-4, 1, 2]):
            with self.subTest(velocities=velocities):
                hydro = HydroResult(
                    t=np.array([0.0]), chainage=np.array([0., 10., 20.]),
                    water_level=np.ones((1, 3)),
                    discharge=np.array([velocities]) * 100,
                    velocity=np.array([velocities]), area=np.ones((1, 3)) * 100)
                original = wq_engine._advection_step
                courant = []

                def record(c, u, dx, dt):
                    courant.append(float(np.max(np.abs(u))) * dt / dx)
                    return original(c, u, dx, dt)

                with patch.object(wq_engine, '_advection_step', side_effect=record):
                    result = wq_engine.solve_water_quality(
                        YayaoConfig(), hydro, simulation_hours=1/60,
                        duration_min=1, chainage_m=10, dt_s=60)
                self.assertTrue(courant)
                self.assertLessEqual(max(courant), 1.0)
                self.assertTrue(np.all(np.isfinite(result.ammonia)))


if __name__ == '__main__':
    unittest.main()
