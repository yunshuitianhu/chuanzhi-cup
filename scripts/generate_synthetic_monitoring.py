"""生成可复现的受控走航算例中间数据；Excel 由 artifact-tool 独立导出。

地图线仅是目视描绘的近似路线。水力、排放、辅助指标全部是假设，不能作为当地实测。
无需联网、密钥、原始样本或用户会话；--output 指向新目录，不覆盖已有真值。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from river_model.config import RiverConfig
from river_model.state import HydroResult
from river_model.wq_engine import solve_water_quality
from river_model.observations import sample_water_quality
from water_quality_mcp.src.water_quality_mcp.geo_tools import gcj02_to_wgs84

HEADERS = ['时间', '氨氮(mg/L)', '溶解氧(mg/L)', '水温(℃)', '经度', '纬度', '电导率',
           '化学需氧量(mg/L)', '总有机碳(mg/L)', 'pH', '电量(%)', '浑浊度(NTU)']
ROUTES = {
    'nanjing': dict(name='南京秦淮河地图选段', center=[32.027, 118.785], width=35., depth=2., u=.20,
        pixels=[[40,120],[80,135],[135,153],[190,178],[250,199],[310,220],[365,244],
                [410,271],[470,285],[530,299],[585,314],[630,337],[675,373],[718,414],
                [759,456],[803,497],[850,529]]),
    'chengdu': dict(name='成都南河地图选段', center=[30.65,104.075], width=28., depth=1.6, u=.18,
        pixels=[[55,566],[116,582],[186,594],[257,603],[325,610],[390,616],[446,614],
                [504,611],[552,617],[596,635],[645,664],[695,699],[745,730],[780,750]]),
    'hangzhou': dict(name='杭州瓶窑镇河道地图选段（河名未核实）', center=[30.37,119.98], width=18., depth=1.5, u=.08,
        pixels=[[350,40],[324,70],[304,110],[290,133],[248,147],[220,169],[198,194],
                [184,228],[184,262],[183,303],[180,345],[179,389],[177,429],
                [178,468],[177,506],[184,550],[185,591],[181,635]])}

# v2：从实际卫星瓦片可见水面重新定线。原v1静态街道图描线没有完成水面配准，
# 不再用于地理演示。下列坐标为zoom16、256px瓦片拼图中的像素，不是实测断面。
ROUTES['nanjing'].update(name='南京外秦淮河卫星选段',
    trace_frame=dict(zoom=16,world_origin=[13922304,6810880],coordinate_system='WGS84'),
    pixels=[[1010,40],[1030,120],[1050,200],[1070,280],[1085,360],[1080,440],
            [1060,520],[1060,600],[1075,680],[1075,760],[1070,840],[1055,920],
            [1035,1000],[1015,1080],[995,1160],[970,1240]])
ROUTES['chengdu'].update(name='成都南河卫星选段',
    trace_frame=dict(zoom=16,world_origin=[13237504,6886656],coordinate_system='WGS84'),
    pixels=[[750,153],[850,177],[950,191],[1050,210],[1150,224],[1250,221],
            [1350,221],[1450,269],[1550,338],[1650,386],[1750,445],[1850,489]])
ROUTES['hangzhou'].update(name='杭州瓶窑镇河道卫星选段（河名未核实）',
    trace_frame=dict(zoom=16,world_origin=[13978880,6900736],coordinate_system='WGS84'),
    pixels=[[470,40],[505,100],[525,160],[525,220],[500,280],[465,340],[425,400],[405,430]])


def route_coordinates(spec):
    """从明确的瓦片坐标框架生成WGS84；兼容旧的静态地图输入定义。"""
    if 'trace_frame' in spec:
        frame=spec['trace_frame']
        if frame['coordinate_system'] != 'WGS84':
            raise ValueError('卫星定线须显式声明WGS84框架')
        world=256*2**frame['zoom']
        ox,oy=frame['world_origin']
        points=np.array([[(ox+px)/world*360-180,
            math.degrees(math.atan(math.sinh(math.pi*(1-2*(oy+py)/world))))]
            for px,py in spec['pixels']])
        return points, _route_distances(points)
    lat, lon = spec['center']
    world = 256 * 2 ** 15
    cx = (lon + 180) / 360 * world
    cy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * world
    points = []
    for px, py in spec['pixels']:
        glon = (cx + px - 450) / world * 360 - 180
        glat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (cy + py - 380) / world))))
        wlat, wlon = gcj02_to_wgs84(glat, glon)
        points.append([wlon, wlat])
    points = np.asarray(points)
    return points, _route_distances(points)


def _route_distances(points):
    a, b = np.radians(points[:-1]), np.radians(points[1:])
    delta = b - a
    dist = 6371008.8 * 2 * np.arcsin(np.sqrt(np.sin(delta[:,1]/2)**2 +
        np.cos(a[:,1])*np.cos(b[:,1])*np.sin(delta[:,0]/2)**2))
    return np.r_[0., np.cumsum(dist)]


def scenarios():
    def source(sid, kind, fraction, mass, start=.2, minutes=150.):
        return dict(source_id=sid, pollutant_type=kind, fraction=fraction,
                    load_kg=mass, source_start_h=start, duration_min=minutes)
    return [
        dict(id='S01', route='nanjing', label='正常背景', events=[]),
        dict(id='S02', route='nanjing', label='单源持续排放',
             events=[source('A','ammonia',.22,1900.)]),
        dict(id='S03', route='chengdu', label='双源持续排放',
             events=[source('A','ammonia',.15,1100.),source('B','ammonia',.58,1600.,.6)]),
        dict(id='S04', route='chengdu', label='单源两次间歇排放',
             events=[source('A','ammonia',.35,450.,.5,20.),source('A','ammonia',.35,650.,1.7,25.)]),
        dict(id='S05', route='hangzhou', label='双源氨氮与有机物耗氧',
             events=[source('A','ammonia',.17,160.,.2,120.),
                     source('A','cbod',.17,2000.,.2,120.),source('B','cbod',.62,800.,.5,100.)]),
        dict(id='S06', route='hangzhou', label='正常水体加仪器异常与缺测', events=[], faults=True),
    ]


def generate(output):
    if output.exists():
        raise FileExistsError(f'为保护已有数据，请使用新的输出目录：{output}')
    output.mkdir(parents=True)
    (output/'真值与配置').mkdir()
    # 用于第三方复现，不把答案写进走航表。
    manifest = dict(schema_version=2, status='SYNTHETIC_NOT_FIELD_DATA', seed_base=20261018,
        coordinate_system='WGS84', geometry_source='星图卫星可见水面人工近似定线；非测绘中心线',
        geometry_source_url='https://open.geovisearth.com/support/document?docId=59&sid=183',
        supersedes='v1地图描线存在水面配准错误，不再用于地理验证；旧测试计数不适用于本版',
        flow_direction='按折线顶点顺序假定；不是当地真实流向', cases=[])
    for index, case in enumerate(scenarios(), 1):
        spec = ROUTES[case['route']]
        polyline, arc = route_coordinates(spec)
        length = float(arc[-1])
        grid = np.linspace(0, length, 81)
        config = RiverConfig(river_name=spec['name'], location='地图选点，所有水力参数为假设',
            length_km=length/1000, length_m=length, width_avg_m=spec['width'], depth_avg_m=spec['depth'],
            width_range_m=(spec['width'],spec['width']),depth_range_m=(spec['depth'],spec['depth']),
            velocity_avg_ms=spec['u'], avg_discharge_m3s=spec['width']*spec['depth']*spec['u'],
            ammonia_nh3_mgL=.35, dissolved_oxygen_mgL=6.8, bod_cbod_mgL=3., water_temp_C=25.,
            k_sediment_oxygen_demand=.5, n_cross_sections=81)
        if case['id'] == 'S05':
            config.k_cbod_decay=.8
        hydro = HydroResult(t=np.array([0.]),chainage=grid,
            water_level=np.full((1,81),spec['depth']),
            discharge=np.full((1,81),config.avg_discharge_m3s),velocity=np.full((1,81),spec['u']),
            area=np.full((1,81),spec['width']*spec['depth']),
            params=dict(engine='analytic_uniform_steady_fixture',tidal_amplitude_m=0.,
                        parameter_status='假定的恒定矩形河槽；非现场率定'))
        events=[]
        for item in case['events']:
            event = {k:v for k,v in item.items() if k != 'fraction'}
            event['chainage_m'] = item['fraction'] * length
            events.append(event)
        # 1小时预热，随后5秒一次、共2.5小时走航；固定时步+守恒输移+耦合耗氧。
        result = solve_water_quality(config,hydro,simulation_hours=3.5,dt_s=30.,
                                     output_interval_s=30.,point_sources=events)
        rng = np.random.default_rng(20261018 + index)
        elapsed = np.arange(1801) * 5.
        times = 1. + elapsed / 3600
        distance = elapsed * 1.10
        phase = np.mod(distance,2*length)
        chainage = np.where(phase <= length,phase,2*length-phase)
        lon = np.interp(chainage,arc,polyline[:,0])
        lat = np.interp(chainage,arc,polyline[:,1])
        # 约0.7m独立位置噪声，不能掩盖地图线本身的30m近似误差。
        lat += rng.normal(0,.7,len(times))/111195
        lon += rng.normal(0,.7,len(times))/(111195*np.cos(np.radians(lat)))
        clean_n = sample_water_quality(result,times,chainage,'ammonia')
        clean_o = sample_water_quality(result,times,chainage,'do')
        clean_b = sample_water_quality(result,times,chainage,'cbod')
        measured_n = np.maximum(0,clean_n+rng.normal(0,.025+.008*clean_n,len(times)))
        measured_o = np.clip(clean_o+rng.normal(0,.04,len(times)),0,20)
        tags = ['normal_noise'] * len(times)
        if case.get('faults'):
            for row in [120,370,910,1480]:
                measured_n[row], measured_o[row] = 30., .1
                tags[row] = 'isolated_sensor_spike'
            measured_n[600:620], measured_o[600:620] = 18., .15
            for row in range(600,620): tags[row] = 'correlated_sensor_bias_100s'
            measured_n[1020:1044] = np.nan
            measured_o[1032:1056] = np.nan
            for row in range(1020,1056): tags[row] = 'missing_measurement'
        # 水温/pH/浊度/电量仅为经验辅助场，不冒充水质反应模型输出。
        temperature = 25 + .3*np.sin(elapsed/9000*math.pi)+rng.normal(0,.03,len(times))
        ph = 7.3+.05*np.sin(elapsed/1200)+rng.normal(0,.015,len(times))
        turbidity = np.maximum(0,15+3*np.sin(elapsed/2000)+rng.normal(0,.6,len(times)))
        battery = 98-18*elapsed/9000
        start = datetime(2026,10,20+index,9,0,0)
        absolute = [(start+timedelta(seconds=float(t))).strftime('%Y-%m-%d %H:%M:%S') for t in elapsed]
        def number(value,digits=3):
            return round(float(value),digits) if np.isfinite(value) else None
        rows = [[absolute[i],number(measured_n[i]),number(measured_o[i]),number(temperature[i],2),
                 number(lon[i],8),number(lat[i],8),None,None,None,number(ph[i],2),
                 number(battery[i],1),number(turbidity[i],2)] for i in range(len(times))]
        expected = (clean_n>8.) | (clean_o<2.)
        expected_target = (clean_n>2.) | (clean_o<2.)
        name = f"{case['id']}_仿真V2_{case['label']}"
        truth_path = output/'真值与配置'/f'{case["id"]}_逐点真值.csv'
        with truth_path.open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['raw_row_id','时间','model_time_h','chainage_m','longitude_wgs84','latitude_wgs84',
                'true_nh3_mgL','true_do_mgL','true_cbod_mgL','expected_nh3_do_alert',
                'exceed_target_V_nh3_do','instrument_tag'])
            for i in range(len(times)):
                writer.writerow([i+1,absolute[i],times[i],chainage[i],lon[i],lat[i],clean_n[i],clean_o[i],
                                 clean_b[i],int(expected[i]),int(expected_target[i]),tags[i]])
        source_features=[]
        for event in result.params['point_sources']:
            sx=event['source_effective_chainage_m']
            event['longitude_wgs84']=float(np.interp(sx,arc,polyline[:,0]))
            event['latitude_wgs84']=float(np.interp(sx,arc,polyline[:,1]))
            source_features.append(dict(type='Feature',geometry=dict(type='Point',
                coordinates=[event['longitude_wgs84'],event['latitude_wgs84']]),properties=event))
        features=[dict(type='Feature',geometry=dict(type='LineString',coordinates=polyline.tolist()),
            properties=dict(name=spec['name'],status='synthetic_approximate_map_trace',
                            direction='assumed_along_vertex_order',length_m=length))]+source_features
        (output/'真值与配置'/f'{case["id"]}_河道与假定污染源.geojson').write_text(
            json.dumps(dict(type='FeatureCollection',features=features),ensure_ascii=False,indent=2),encoding='utf-8')
        detail=dict(case_id=case['id'],label=case['label'],river=spec['name'],seed=20261018+index,
            coordinate_system='WGS84',row_count=len(rows),data_start=absolute[0],data_end=absolute[-1],
            simulation_start=(start-timedelta(hours=1)).isoformat(),warmup_h=1.,sample_interval_s=5.,boat_speed_ms=1.1,
            boat_motion='沿折线往返，逐点在对应时间和位置采样',noise='NH3 sigma=0.025+0.008*C mg/L；DO sigma=0.04 mg/L；位置sigma=0.7m；独立高斯',
            missing_columns=['电导率','化学需氧量(mg/L)','总有机碳(mg/L)'],
            missing_reason='未建立这些组分的独立模型；COD不能用CBOD替代。pH、水温、浊度、电量为经验辅助场。',
            config=asdict(config),model_params=result.params,
            physical_source_count=len(set(e['source_id'] for e in events)),emission_event_count=len(events),
            source_location_uncertainty='最近断面离散误差见各事件；卫星水面人工定线未经测绘验证，未承诺精度',
            trace_frame=spec['trace_frame'],trace_pixels=spec['pixels'],dataset_version=2,
            alert_definition='仅受控阈值 NH3>8 或 DO<2；不是人工黑臭标签。目标V类超标另以 NH3>2 或 DO<2 记录。',
            intended_test='数据导入、时空筛查、多河段迁移和误报压力；不以热点数等同污染源数；多源反演尚未上线',
            stats=dict(true_nh3_min=float(clean_n.min()),true_nh3_max=float(clean_n.max()),
                       true_do_min=float(clean_o.min()),true_do_max=float(clean_o.max()),
                       true_alert_rows=int(expected.sum()),instrument_fault_rows=sum(t!='normal_noise' for t in tags)))
        (output/'真值与配置'/f'{case["id"]}_场景参数与源真值.json').write_text(
            json.dumps(detail,ensure_ascii=False,indent=2),encoding='utf-8')
        # 原始输入中不含源坐标、负荷或逐点真值，仅明确它是仿真数据。
        payload=dict(id=case['id'],name=name,headers=HEADERS,rows=rows,notes=[
            ['数据性质','纯仿真走航数据，非当地监测结果'],['地点',spec['name']],
            ['坐标系','WGS84；卫星水面人工近似定线，未经测绘验证'],['记录数',len(rows)],['采样间隔','5秒'],
            ['采样时间',absolute[0]+' 至 '+absolute[-1]],['路线','假定方向的沿河往返走航'],
            ['物理输出','氨氮、溶解氧：恒定流守恒对流—扩散—反应，逐点时空采样'],
            ['辅助字段','水温、pH、浊度、电量：经验设定，未经过现场率定'],
            ['未模拟字段','电导率、COD、总有机碳留空；COD未用CBOD冒充'],
            ['真值用途','独立保存供评估，检测输入不包含污染源答案'],
            ['使用限制','可验证受控场景行为，不能据此证明真实河流准确率'],
            ['版本','V2：替代未正确贴合水面的V1路线；所有污染与水力仍为仿真设定'],
            ['地理参考','星图卫星影像 https://www.geovisearth.com；人工近似定线']])
        (output/f'{case["id"]}_workbook_payload.json').write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
        manifest['cases'].append({k:detail[k] for k in ('case_id','label','river','row_count','physical_source_count',
            'emission_event_count','stats') } | dict(workbook=name+'.xlsx'))
        print(json.dumps(manifest['cases'][-1],ensure_ascii=False),flush=True)
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    generate(parser.parse_args().output.resolve())
