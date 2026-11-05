"""从当前数据建议单向航段，支持未确认的地图情景预览。"""
import numpy as np
from river_model.cruise_alignment import _coordinates, utc_times, audit_track, distance, reference_geometry


def reference_segment(df, start, end, reverse=False):
    if df is None or not 0 <= start < end < len(df):
        raise ValueError('请选择当前数据中的有效航段')
    segment = df.iloc[start:end+1]
    if audit_track(segment)['warnings']:
        raise ValueError('航段仍有返航、缺失GPS或时间问题，请另选单向完整航段')
    points = segment[['latitude','longitude']].to_numpy(dtype=float)
    points = points[np.r_[True, distance(points[:-1],points[1:]) >= .01]]
    points = points[np.unique(np.linspace(0,len(points)-1,min(500,len(points)),dtype=int))]
    if reverse:
        points = points[::-1]
    _, axis = reference_geometry(points)
    return dict(start=start,end=end,reverse=reverse,reference_path=points.tolist(),
        length_m=float(axis[-1]),reference_note=f'当前数据记录{start}至{end}的单向候选航段；仅地图情景展示，非测绘配准',
        notice='请确认本次情景按所选方向从上游向下游播放。航行方向不等于实际水流方向。')


def suggest_reference(df):
    if df is None or not 2 <= len(df) <= 20000:
        raise ValueError('需加载2至20000条带GPS的走航记录，或在配准面板提供独立参考线')
    if 'datetime' not in df:
        raise ValueError('走航记录缺少完整时间列，请提供独立参考线')
    coordinates, valid = _coordinates(df)
    valid &= ~utc_times(df.get('datetime', []),'Asia/Shanghai').isna()
    indices = np.flatnonzero(valid)
    runs = np.split(indices,np.flatnonzero(np.diff(indices)>1)+1)
    pending = [(int(r[0]),int(r[-1])) for r in runs if len(r)>1]
    candidates = []
    while pending:
        start,end = pending.pop()
        if end-start < 1:
            continue
        if not audit_track(df.iloc[start:end+1])['warnings']:
            try:
                candidates.append(reference_segment(df,start,end))
            except ValueError:
                pass
            continue
        # Split revisits at the farthest excursion, never accumulate the return leg.
        split = start + int(np.argmax(distance(coordinates[start],coordinates[start:end+1])))
        if split <= start or split >= end:
            split = (start+end)//2
        if split > start:
            pending.append((start,split))
        if split < end:
            pending.append((split+1,end))
    if not candidates:
        raise ValueError('未找到有效单向航段，请提供独立参考线；不会补造GPS')
    return max(candidates,key=lambda c:c['length_m'])


def restore_reference(df, config):
    if (not isinstance(config,dict) or config.get('confirmed') is not True
            or type(config.get('start')) is not int or type(config.get('end')) is not int
            or type(config.get('reverse',False)) is not bool):
        raise ValueError('地图航段配置无效，请重新选择并确认方向')
    return reference_segment(df,config['start'],config['end'],config.get('reverse',False))


def automatic_preview(chainage, df):
    """读取候选直接预览；不写用户确认，不改变配准配置或模型桩号。"""
    selected = suggest_reference(df)
    points, reference_axis = reference_geometry(selected['reference_path'])
    axis = np.asarray(chainage,dtype=float)
    if (axis.ndim != 1 or len(axis)<2 or not np.isfinite(axis).all()
            or axis[0]<0 or np.any(np.diff(axis)<=0)):
        raise ValueError('模拟桩号轴无效')
    covered = (axis >= 0) & (axis <= reference_axis[-1])
    if covered.sum() < 2:
        raise ValueError('当前候选航段未覆盖两个模型节点，请调整河段范围或提供参考线')
    lat = np.interp(axis[covered],reference_axis,points[:,0])
    lon = (np.interp(axis[covered],reference_axis,
        np.degrees(np.unwrap(np.radians(points[:,1]))))+180)%360-180
    positions = [None]*len(axis)
    for i,a,b in zip(np.flatnonzero(covered),lat,lon):
        positions[int(i)]=[float(a),float(b)]
    reason = (f"自动情景预览：按当前记录{selected['start']}–{selected['end']}的单向航段顺序展示，"
              '未确认真实水流方向；可在航段设置中反转或更换，不作为已核验配准。')
    return dict(available=True,complete=bool(covered.all()),positions=positions,
        mapped_nodes=int(covered.sum()),total_nodes=len(axis),
        mapped_range_m=[float(axis[covered][0]),float(axis[covered][-1])],
        model_range_m=[float(axis[0]),float(axis[-1])],track_length_m=float(reference_axis[-1]),
        confirmation='automatic_scenario_preview_unconfirmed',reason=reason,
        preview_segment={'start':selected['start'],'end':selected['end']})
