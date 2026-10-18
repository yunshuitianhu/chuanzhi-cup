"""手工河段参数的公共校验与派生；不依赖行政区、样例坐标或本机软件。"""
from dataclasses import replace
import math

PARAMETER_FIELDS = {'river_name', 'location', 'length_m', 'width_avg_m', 'depth_avg_m',
    'mannings_n', 'longitudinal_slope', 'avg_discharge_m3s', 'bed_elevation_avg_m',
    'high_water_mean_m', 'ammonia_nh3_mgL', 'dissolved_oxygen_mgL', 'bod_cbod_mgL',
    'water_temp_C', 'k_nitrification', 'k_cbod_decay', 'k_sediment_oxygen_demand', 'do_saturation_mgL'}


def configure_river(config, parameters=None):
    values = dict(parameters or {})
    if set(values) - PARAMETER_FIELDS:
        raise ValueError('包含未支持的河段参数')
    for key, value in values.items():
        if key in ('river_name', 'location'):
            if not isinstance(value, str) or not value.strip() or len(value) > 100:
                raise ValueError(f'{key} 须为 1~100 字符文本')
        elif type(value) not in (float, int) or not math.isfinite(value):
            raise ValueError(f'{key} 须为有限数')
    cfg = replace(config, **values)
    if (not 10 <= cfg.length_m <= 100000 or not .5 <= cfg.width_avg_m <= 5000
            or not .1 <= cfg.depth_avg_m <= 100 or not 0 < cfg.mannings_n <= .2
            or not 0 <= cfg.longitudinal_slope <= .05 or cfg.avg_discharge_m3s < 0
            or min(cfg.ammonia_nh3_mgL, cfg.dissolved_oxygen_mgL, cfg.bod_cbod_mgL) < 0):
        raise ValueError('河段长度/宽度/水深/糙率/坡度或初始水质超出支持范围')
    if (not 0 <= cfg.water_temp_C <= 45 or not 0 <= cfg.k_nitrification <= 10
            or not 0 <= cfg.k_cbod_decay <= 10 or not 0 <= cfg.k_sediment_oxygen_demand <= 100
            or not 0 < cfg.do_saturation_mgL <= 20):
        raise ValueError('温度、反应速率或饱和氧参数超出支持范围')
    # 梯形在参考水深处的水面宽等于输入宽度；窄深河道自动用较陡的岸坡。
    cfg.side_slope = min(2., cfg.width_avg_m/(8*cfg.depth_avg_m))
    cfg.bottom_width_m = cfg.width_avg_m - 2*cfg.side_slope*cfg.depth_avg_m
    cfg.bank_elevation_m = cfg.bed_elevation_avg_m + cfg.longitudinal_slope*cfg.length_m/2 + 2*cfg.depth_avg_m
    cfg.length_km = cfg.length_m / 1000
    if cfg.high_water_mean_m <= cfg.bed_elevation_avg_m - cfg.longitudinal_slope*cfg.length_m/2:
        raise ValueError('下游水位须高于下游河底，请核对高程基准')
    cfg.depth_range_m = (.5*cfg.depth_avg_m, 1.5*cfg.depth_avg_m)
    cfg.width_range_m = (.5*cfg.width_avg_m, 1.5*cfg.width_avg_m)
    cfg.param_sources = dict(cfg.param_sources)
    cfg.param_confidence = dict(cfg.param_confidence)
    for key in values:
        cfg.param_sources[key] = 'user_scenario'
        cfg.param_confidence.pop(key, None)  # 用户填值不等于 100% 现场准确率。
    return cfg
