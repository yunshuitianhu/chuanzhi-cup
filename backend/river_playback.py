"""将当前模型结果交给原生网页播放器，不运行模型或重用固定样例。"""
import numpy as np

from water_quality_mcp.src.water_quality_mcp.analysis_config import GB3838_STANDARDS


def playback_data(state, monitoring_data, constituent, target_class, max_frames, alignment_config=None):
    wq = state.wq_result
    names = {'ammonia': ('氨氮', 'ammonia'), 'cbod': ('CBOD', 'cbod'), 'do': ('溶解氧', 'dissolved_oxygen')}
    name, field = names[constituent]
    values = getattr(wq, field)
    if values is None:
        raise ValueError(f'当前模拟没有{name}结果')
    values, times, chainage = np.asarray(values), np.asarray(wq.t), np.asarray(wq.chainage)
    if (len(times) == 0 or len(chainage) < 2 or values.shape != (len(times), len(chainage))
        or not all(np.isfinite(a).all() for a in (values, times, chainage))
        or np.any(np.diff(times) <= 0) or np.any(np.diff(chainage) <= 0)):
        raise ValueError('模型结果包含无效数值或时间/桩号顺序，不能播放')
    indices = set(np.linspace(0, len(times)-1, min(len(times), max_frames), dtype=int).tolist())
    indices.add(int(np.argmin(values.min(axis=1)) if constituent == 'do' else np.argmax(values.max(axis=1))))
    # 保留排放开始/结束两侧，避免均匀抽帧跳过短排放事件。最多额外四帧。
    start = float(wq.params.get('source_start_h', 0))
    end = start + float(wq.params.get('duration_min', 0))/60
    for event in (start, end):
        if times[0] <= event <= times[-1]:
            i = int(np.searchsorted(times, event))
            indices.update([max(0, i-1), min(len(times)-1, i)])
    indices = sorted(indices)
    mapping = {'available': False, 'reason': '当前项目没有有效走航轨迹，可查看沿程浓度动画。', 'positions': []}
    if alignment_config is not None:
        from .cruise_alignment import AlignmentRequest
        from river_model.cruise_alignment import reference_geometry
        import json
        try:
            config = AlignmentRequest.model_validate_json(json.dumps(alignment_config))
            reference, axis = reference_geometry(config.reference_path)
            if chainage[0] < 0 or chainage[-1] > axis[-1]:
                raise ValueError('已配置参考线未覆盖全部模拟桩号，地图不缩放或外推；仍可查看沿程动画。')
            lat = np.interp(chainage, axis, reference[:, 0])
            unwrapped = np.degrees(np.unwrap(np.radians(reference[:, 1])))
            lon = (np.interp(chainage, axis, unwrapped)+180) % 360-180
            mapping = {'available':True, 'positions':np.column_stack([lat,lon]).tolist(),
                'track_length_m':float(axis[-1]), 'confirmation':'user_supplied_not_independently_verified',
                'reason':'按用户确认的参考线映射，不累计船的返航航程；参考线和流向仍需独立核实。'}
        except ValueError as exc:
            mapping['reason'] = str(exc)
    elif monitoring_data is not None and {'latitude', 'longitude'} <= set(monitoring_data.columns):
        from river_model.geo_mapping import build_chainage_gps_mapping, chainage_array_to_gps
        valid = monitoring_data.loc[monitoring_data.latitude.between(-85, 85) & monitoring_data.longitude.between(-180, 180)]
        try:
            from river_model.cruise_alignment import audit_track
            if audit_track(monitoring_data)['warnings']:
                raise ValueError('走航轨迹存在返航、缺失坐标或时间等待核对项，请先在河道页配置参考线；累计航程不能直接作为河道桩号。')
            gps = build_chainage_gps_mapping(valid, smoothing_sigma=3.0)
            if chainage[0] < 0 or chainage[-1] > gps['total_length_m'] + 1:
                raise ValueError('当前走航轨迹未覆盖模拟桩号范围；请核对河段长度、起点与方向。')
            if gps['total_length_m'] <= 0:
                raise ValueError('当前GPS轨迹没有可用的沿程长度。')
            lat, lon = chainage_array_to_gps(gps, chainage)
            positions = np.column_stack([lat, lon])
            if not np.isfinite(positions).all():
                raise ValueError('轨迹映射包含无效坐标，请核实GPS。')
            mapping = {'available': True, 'reason': '桩号从当前走航起点累计映射；地图为示意，起点、方向及GPS需现场核实。',
                       'positions': positions.tolist(), 'track_length_m': float(gps['total_length_m']),
                       'confirmation':'illustrative_boat_route_not_registered'}
        except (ValueError, KeyError) as exc:
            mapping['reason'] = str(exc)
    limits = GB3838_STANDARDS[target_class]
    limit = limits['nh3'] if constituent == 'ammonia' else limits['do'] if constituent == 'do' else None
    source = {key: wq.params.get(key) for key in ('chainage_m', 'source_effective_chainage_m', 'source_start_h', 'duration_min', 'load_kg', 'pollutant_type')}
    return {'river_name': state.config.river_name, 'constituent': constituent, 'label': name, 'unit': 'mg/L',
        'chainage': chainage.tolist(), 'times': times[indices].tolist(), 'concentrations': values[indices].tolist(),
        'total_frames': len(times), 'returned_frames': len(indices), 'source': source,
        'color_scale': {'min': float(values.min()), 'max': float(values.max()), 'inverse': constituent == 'do'},
        'limit': limit, 'limit_direction': 'minimum' if constituent == 'do' else 'maximum',
        'target_water_class': target_class, 'mapping': mapping,
        'notice': '一维恒定流情景模拟；色标在本次模拟全时段固定。横向线宽仅用于显示，不表示二维污染范围。'}
