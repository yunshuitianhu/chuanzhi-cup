"""将当前模型结果交给原生网页播放器，不运行模型或重用固定样例。"""
import numpy as np

from water_quality_mcp.src.water_quality_mcp.analysis_config import GB3838_STANDARDS


def playback_mapping(chainage, monitoring_data, alignment_config=None):
    """只绘已有坐标支持的模型节点；尾段不足不使整个底图失效。"""
    from river_model.geo_mapping import checked_model_mapping, chainage_array_to_gps
    axis = np.asarray(chainage, dtype=float)
    if (axis.ndim != 1 or len(axis) < 2 or not np.isfinite(axis).all()
            or np.any(np.diff(axis) <= 0) or axis[0] < 0):
        raise ValueError('模拟桩号轴无效')
    covered = np.ones(len(axis), dtype=bool)
    try:
        gps = checked_model_mapping(axis, monitoring_data, alignment_config)
    except ValueError as original:
        # Probe with the first two actual model nodes, retaining all existing GPS,
        # direction/reference and return-trip checks. Never stretch or extrapolate.
        try:
            probe = checked_model_mapping(axis[:2], monitoring_data, alignment_config)
            covered = axis <= probe['total_length_m'] + 1e-6
            if covered.all() or covered.sum() < 2:
                raise original
            gps = checked_model_mapping(axis[covered], monitoring_data, alignment_config)
        except (ValueError, KeyError):
            raise original
    latitude, longitude = chainage_array_to_gps(gps, axis[covered])
    positions = [None] * len(axis)
    for i, lat, lon in zip(np.flatnonzero(covered), latitude, longitude):
        positions[int(i)] = [float(lat), float(lon)]
    complete = bool(covered.all())
    reason = gps['notice']
    if not complete:
        reason += (f" 仅显示桩号{axis[covered][0]:.1f}–{axis[covered][-1]:.1f} m的已覆盖节点；"
                   f"模型至{axis[-1]:.1f} m，未覆盖末段不在地图绘制，不缩放或外推坐标。")
    return {'available': True, 'complete': complete, 'positions': positions,
        'mapped_nodes': int(covered.sum()), 'total_nodes': len(axis),
        'mapped_range_m': [float(axis[covered][0]), float(axis[covered][-1])],
        'model_range_m': [float(axis[0]), float(axis[-1])],
        'track_length_m': gps['total_length_m'], 'confirmation': gps['confirmation'], 'reason': reason}


def playback_data(state, monitoring_data, constituent, target_class, max_frames, alignment_config=None, map_reference=None):
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
    try:
        if alignment_config is not None and map_reference is None:
            from .cruise_alignment import AlignmentRequest
            import json
            config = AlignmentRequest.model_validate_json(json.dumps(alignment_config))
            alignment_config = config.model_dump(mode='json')
        if map_reference is not None:
            from .playback_reference import restore_reference
            selected = restore_reference(monitoring_data,map_reference)
            alignment_config = {**selected,'confirmed':True}
        mapping = playback_mapping(chainage, monitoring_data, alignment_config)
        if map_reference is not None:
            mapping['confirmation'] = 'user_confirmed_scenario_route_only'
            mapping['reason'] += ' 此航段仅用于地图情景展示，不改变逐点时空配准。'
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
