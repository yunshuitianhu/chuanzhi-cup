"""走航逐点配准：显式参考线和时间原点，不将船的累计航程当作河长。"""
from collections import Counter
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from .observations import sample_water_quality
from water_quality_mcp.src.water_quality_mcp.data_quality import coordinate_mask

RADIUS_M = 6371000.
NOTICE = ('配准按用户提供的WGS84参考线、上游起点、方向、时区和时间原点计算，未经独立现场核验；'
          '误差仅为情景对照，不能当作模型准确率、仪器校准或污染源确认。')


def _longitude_delta(value):
    return (np.asarray(value)+180.) % 360.-180.


def distance(a, b):
    """成对球面距离，输入[..., 纬度/经度]，避免经度跨180度时绕地球。"""
    a, b = np.asarray(a), np.asarray(b)
    lat1, lat2 = np.radians(a[..., 0]), np.radians(b[..., 0])
    h = np.sin((lat2-lat1)/2)**2 + np.cos(lat1)*np.cos(lat2)*np.sin(np.radians(_longitude_delta(b[..., 1]-a[..., 1]))/2)**2
    return 2*RADIUS_M*np.arcsin(np.sqrt(np.clip(h, 0., 1.)))


def utc_times(values, timezone):
    try:
        zone = ZoneInfo(timezone)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError('监测时区无效，请使用IANA时区，例如Asia/Shanghai') from exc
    parsed = []
    for value in values:
        try:
            # 不给仅有时刻的字符串自动补今天，也不把数字猜作纳秒时间戳。
            if not isinstance(value, (pd.Timestamp, datetime, date)) and not (
                    isinstance(value, str) and re.match(r'^\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}', value)):
                raise ValueError('缺少日期')
            stamp = pd.Timestamp(value)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize(zone, ambiguous='NaT', nonexistent='NaT')
            parsed.append(stamp.tz_convert('UTC'))
        except (ValueError, TypeError, OverflowError):
            parsed.append(pd.NaT)
    return pd.DatetimeIndex(parsed, tz='UTC')


def _coordinates(df):
    coordinates = np.column_stack([pd.to_numeric(df.get(key, pd.Series(np.nan, index=df.index)),
        errors='coerce').to_numpy(dtype=float, na_value=np.nan) for key in ('latitude', 'longitude')])
    valid = coordinate_mask(df).to_numpy() & (abs(coordinates[:, 0]) <= 85)
    return coordinates, valid


def audit_track(df, timezone='Asia/Shanghai'):
    coordinates, valid = _coordinates(df)
    times = utc_times(df.get('datetime', pd.Series(pd.NaT, index=df.index)), timezone)
    segments = valid[1:] & valid[:-1]
    distances = distance(coordinates[:-1], coordinates[1:])
    travelled = float(distances[segments].sum())
    points = coordinates[valid]
    chord = float(distance(points[0], points[-1])) if len(points) > 1 else 0.
    span = float(distance(points[0], points).max()) if len(points) else 0.
    warnings = []
    possible_revisit = travelled > max(100., 1.5*span) and chord < .4*travelled
    if possible_revisit:
        warnings.append('可能存在返航、重复巡测或GPS漂移；累计航程不能直接作为河道长度或桩号。')
    if not valid.all():
        warnings.append('存在缺失、越界或非原始GPS坐标，不用插值坐标参与配准。')
    if times.isna().any():
        warnings.append('存在无完整日期或无法解析的时间；不会自动补日期。')
    good_times = times[~times.isna()]
    return dict(records=len(df), valid_gps=int(valid.sum()), invalid_time=int(times.isna().sum()),
        possible_revisit=possible_revisit,
        travelled_distance_m=travelled, endpoint_distance_m=chord,
        first_time=good_times.min().isoformat() if len(good_times) else None,
        warnings=warnings, status='reference_required',
        notice='船的航行方向不能证明水流方向。参考线须由用户核实，不自动压缩返航轨迹或缩放到模型河长。')


def reference_geometry(path):
    points = np.asarray(path, dtype=float)
    if (points.ndim != 2 or points.shape[1] != 2 or not 2 <= len(points) <= 500
            or not np.isfinite(points).all() or np.any(abs(points[:, 0]) > 85)
            or np.any(abs(points[:, 1]) > 180)):
        raise ValueError('参考线须有2~500个有限WGS84坐标[纬度,经度]，纬度限±85度')
    lengths = distance(points[:-1], points[1:])
    if np.any(lengths < .01) or lengths.sum() > 100000 or np.any(lengths > 20000):
        raise ValueError('参考线含重复相邻点、单段超过20km或总长超过100km，请核实并适当加密')
    return points, np.r_[0., np.cumsum(lengths)]


def project_points(coordinates, path, ambiguity_m):
    points, chainage = reference_geometry(path)
    if len(coordinates)*(len(points)-1) > 5_000_000:
        raise ValueError('配准计算量过大，请减少参考线节点或分段核对')
    origin, end = points[:-1], points[1:]
    scale = np.cos(np.radians((origin[:, 0]+end[:, 0])/2))
    vector = np.column_stack([np.radians(_longitude_delta(end[:, 1]-origin[:, 1]))*scale,
                              np.radians(end[:, 0]-origin[:, 0])])*RADIUS_M
    squared = np.sum(vector**2, axis=1)
    output_x, output_distance, output_ambiguous = [], [], []
    # 单段局部切平面投影，再用球面距离比较候选；分批限制内存。
    for start in range(0, len(coordinates), 512):
        batch = coordinates[start:start+512]
        offsets = np.stack([np.radians(_longitude_delta(batch[:, None, 1]-origin[None, :, 1]))*scale,
                            np.radians(batch[:, None, 0]-origin[None, :, 0])], axis=-1)*RADIUS_M
        fraction = np.clip(np.sum(offsets*vector, axis=-1)/squared, 0., 1.)
        projected = origin[None, :, :]+fraction[:, :, None]*np.column_stack([
            end[:, 0]-origin[:, 0], _longitude_delta(end[:, 1]-origin[:, 1])])[None, :, :]
        projected[:, :, 1] = _longitude_delta(projected[:, :, 1])
        distances = distance(batch[:, None, :], projected)
        positions = chainage[:-1]+fraction*np.diff(chainage)
        best = distances.argmin(axis=1)
        ids = np.arange(len(batch))
        x, error = positions[ids, best], distances[ids, best]
        ambiguous = np.any((distances <= error[:, None]+ambiguity_m)
                          & (abs(positions-x[:, None]) > max(10., 2*ambiguity_m)), axis=1)
        output_x.extend(x); output_distance.extend(error); output_ambiguous.extend(ambiguous)
    return np.array(output_x), np.array(output_distance), np.array(output_ambiguous, dtype=bool)


def align_cruise(df, wq, *, reference_path, model_origin, monitoring_timezone='Asia/Shanghai',
                 time_offset_s=0., max_distance_m=50., ambiguity_m=5., max_speed_mps=10.,
                 confirmed=False, reference_note='', constituent='ammonia'):
    if confirmed is not True or not isinstance(reference_note, str) or not reference_note.strip():
        raise ValueError('须明确确认参考线按模型上游至下游排列、时间原点已核对，并记录参考线来源')
    if not 1 <= len(df) <= 20000:
        raise ValueError('每次核对需要1~20000条监测记录')
    limits = [(time_offset_s, -86400., 86400.), (max_distance_m, .5, 500.),
              (ambiguity_m, 0., 50.), (max_speed_mps, .1, 50.)]
    if any(type(v) not in (int, float) or not np.isfinite(v) or not low <= v <= high for v, low, high in limits):
        raise ValueError('时间偏移、距离、歧义或航速排查阈值无效')
    if constituent not in ('ammonia', 'do'):
        raise ValueError('仅支持氨氮或溶解氧逐点对照，不用COD代替CBOD')
    origin = pd.Timestamp(model_origin)
    if pd.isna(origin) or origin.tzinfo is None:
        raise ValueError('模拟时间原点须含完整日期和明确时区偏移')
    times = utc_times(df.get('datetime', pd.Series(pd.NaT, index=df.index)), monitoring_timezone)
    relative = np.asarray((times-origin.tz_convert('UTC')).total_seconds(), dtype=float)/3600+time_offset_s/3600
    coordinates, valid = _coordinates(df)
    positions, errors, ambiguous = np.full(len(df), np.nan), np.full(len(df), np.nan), np.zeros(len(df), dtype=bool)
    positions[valid], errors[valid], ambiguous[valid] = project_points(coordinates[valid], reference_path, ambiguity_m)
    seconds = np.asarray((times[1:]-times[:-1]).total_seconds(), dtype=float)
    pair = valid[:-1] & valid[1:] & ~times.isna()[:-1] & ~times.isna()[1:] & (seconds > 0)
    speed = np.divide(distance(coordinates[:-1], coordinates[1:]), seconds,
                      out=np.zeros(len(seconds)), where=pair)
    jumps = np.zeros(len(df), dtype=bool)
    jumps[:-1] |= pair & (speed > max_speed_mps)
    jumps[1:] |= pair & (speed > max_speed_mps)
    column = 'ammonia_nitrogen' if constituent == 'ammonia' else 'dissolved_oxygen'
    measured = pd.to_numeric(df.get(column, pd.Series(np.nan, index=df.index)), errors='coerce').to_numpy(dtype=float, na_value=np.nan)
    observed = np.isfinite(measured) & (measured >= 0) & (measured <= (1000 if constituent == 'ammonia' else 20))
    if f'__observed_{column}' in df:
        observed &= df[f'__observed_{column}'].eq(True).to_numpy()
    rows, accepted, seen = [], [], set()
    for i in range(len(df)):
        status = ('invalid_time' if not np.isfinite(relative[i]) else 'invalid_gps' if not valid[i] else
            'gps_jump' if jumps[i] else 'off_reference' if errors[i] > max_distance_m else
            'ambiguous_position' if ambiguous[i] else
            'outside_time' if not wq.t[0] <= relative[i] <= wq.t[-1] else
            'outside_chainage' if not wq.chainage[0] <= positions[i] <= wq.chainage[-1] else
            'unobserved_measurement' if not observed[i] else 'matched')
        key = (round(relative[i], 12), round(positions[i], 6))
        if status == 'matched':
            if key in seen:
                status = 'duplicate_observation'
            else:
                seen.add(key); accepted.append(i)
        rows.append(dict(record_id=i, status=status, time_h=float(relative[i]) if np.isfinite(relative[i]) else None,
            chainage_m=float(positions[i]) if valid[i] else None,
            distance_m=float(errors[i]) if valid[i] else None,
            measured_mgL=float(measured[i]) if observed[i] else None, simulated_mgL=None))
    metrics = None
    if accepted:
        prediction = sample_water_quality(wq, relative[accepted], positions[accepted], constituent)
        residual = prediction-measured[accepted]
        metrics = dict(rmse_mgL=float(np.sqrt(np.mean(residual**2))), mae_mgL=float(np.mean(abs(residual))),
                       bias_mgL=float(np.mean(residual)))
        for i, value in zip(accepted, prediction):
            rows[i]['simulated_mgL'] = float(value)
    return dict(summary=dict(records=len(df), matched=len(accepted),
        excluded=len(df)-len(accepted), statuses=dict(Counter(row['status'] for row in rows)),
        constituent=constituent, comparison=metrics, reference_length_m=float(reference_geometry(reference_path)[1][-1]),
        reference_note=reference_note, confirmation='user_supplied_not_independently_verified', notice=NOTICE), rows=rows)
