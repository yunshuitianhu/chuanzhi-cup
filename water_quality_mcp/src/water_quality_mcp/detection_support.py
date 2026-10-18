"""点级筛查的时空支持度与热点聚类；支持度不是污染责任概率。"""
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from sklearn.cluster import DBSCAN
from sklearn.neighbors import BallTree, sort_graph_by_row_values

EARTH_RADIUS_M = 6371000.
DETECTION_VERSION = 'screening-support-v2'
MAX_NEIGHBOR_EDGES = 2_000_000


def time_seconds(df):
    if 'datetime' not in df:
        return np.full(len(df), np.nan)
    parsed = pd.to_datetime(df['datetime'], errors='coerce', utc=True)
    ns = parsed.array.as_unit('ns').asi8
    valid = parsed.notna().to_numpy()
    result = np.full(len(df), np.nan)
    if valid.any():
        result[valid] = (ns[valid].astype(float) - float(ns[valid].min())) / 1e9
    return result


def temporal_support(df, pi, window_min=30, min_points=3, max_gap_min=5):
    """窗口内观测支持的超目标时长/完整窗口时长；重复时间不增加独立证据。"""
    pi = np.asarray(pi, dtype=float)
    factors = np.zeros(len(pi))
    if window_min <= 0 or max_gap_min <= 0 or len(pi) < min_points:
        return factors
    times = time_seconds(df)
    valid = np.isfinite(times) & np.isfinite(pi)
    if valid.sum() < min_points:
        return factors
    records = pd.DataFrame({'t': times[valid], 'exceed': (pi[valid] > 1).astype(float)})
    # 同时刻正反观测冲突计0.5；复制其中任一状态也不能提高支持度。
    grouped = records.drop_duplicates(['t', 'exceed']).groupby('t', sort=True)['exceed'].mean()
    ts, status = grouped.index.to_numpy(), grouped.to_numpy()
    if len(ts) < max(min_points, 2):
        return factors
    dt = np.diff(ts)
    supported = dt <= max_gap_min * 60
    if '__sample_sequence' in df:
        seq = pd.DataFrame({'t': times[valid], 'seq': df['__sample_sequence'].to_numpy()[valid]})
        bounds = seq.groupby('t', sort=True)['seq'].agg(['min', 'max'])
        supported &= bounds['min'].to_numpy()[1:] - bounds['max'].to_numpy()[:-1] == 1
    areas = .5 * (status[:-1] + status[1:]) * dt * supported
    prefix = np.r_[0., np.cumsum(areas)]

    def integral(query):
        i = np.clip(np.searchsorted(ts, query, side='right')-1, 0, len(ts)-2)
        length = np.clip(query-ts[i], 0, dt[i])
        partial = (status[i]*length + (status[i+1]-status[i])*length**2/(2*dt[i])) * supported[i]
        return np.where(query <= ts[0], 0., np.where(query >= ts[-1], prefix[-1], prefix[i]+partial))

    half = window_min * 30.
    lo, hi = times[valid]-half, times[valid]+half
    left, right = np.searchsorted(ts, lo), np.searchsorted(ts, hi, side='right')
    count_prefix = np.r_[0, np.cumsum(status > 0)]
    enough = count_prefix[right]-count_prefix[left] >= min_points
    factor = (integral(hi)-integral(lo))/(window_min*60.)
    factors[valid] = np.where(enough & (pi[valid] > 1), np.clip(factor, 0, 1), 0)
    return factors


def haversine_m(lat1, lon1, lat2, lon2):
    dlat, dlon = np.radians(lat2-lat1), np.radians(lon2-lon1)
    a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1))*np.cos(np.radians(lat2))*np.sin(dlon/2)**2
    return 2*EARTH_RADIUS_M*np.arcsin(np.sqrt(np.clip(a,0,1)))


def spatial_support(df, pi, window_m=50, min_points=3, max_gap_min=5):
    """按有效相邻观测成段，用离段首的最大跨度计权；不把返航抵消为零。"""
    pi = np.asarray(pi, dtype=float)
    factors = np.zeros(len(pi))
    if len(pi) < min_points or window_m <= 0 or not {'latitude','longitude'} <= set(df):
        return factors
    lat = pd.to_numeric(df.latitude, errors='coerce').to_numpy(dtype=float)
    lon = pd.to_numeric(df.longitude, errors='coerce').to_numpy(dtype=float)
    valid = (pi > 1) & np.isfinite(pi) & np.isfinite(lat) & np.isfinite(lon) & (abs(lat)<=90) & (abs(lon)<=180)
    adjacent = valid[:-1] & valid[1:] & (haversine_m(lat[:-1],lon[:-1],lat[1:],lon[1:]) <= 2*window_m)
    if '__sample_sequence' in df:
        adjacent &= np.diff(df['__sample_sequence'].to_numpy()) == 1
    if 'datetime' in df:
        times = time_seconds(df)
        adjacent &= np.isfinite(times[:-1]) & np.isfinite(times[1:]) & (np.diff(times)>=0) & (np.diff(times)<=max_gap_min*60)
    # 每个连续段只扫描一次；旧实现会对同一段重复向两端搜索。
    starts = np.r_[0, np.flatnonzero(~adjacent)+1]
    ends = np.r_[starts[1:],len(pi)]
    for start, end in zip(starts,ends):
        if end-start >= min_points and valid[start]:
            # 最少支持点也按独立观测计数；复制一行不能使两点变成三点。
            keys = {'lat':lat[start:end], 'lon':lon[start:end]}
            if 'datetime' in df:
                keys['time'] = times[start:end]
            if len(pd.DataFrame(keys).drop_duplicates()) < min_points:
                continue
            span = float(np.max(haversine_m(lat[start],lon[start],lat[start:end],lon[start:end])))
            factors[start:end] = min(span/window_m,1.)
    return factors


def cluster_black_samples(black, config):
    """球面米制距离与时间门限同时满足才相邻；重复坐标/时刻只计一次。"""
    labels = np.full(len(black), -1, dtype=int)
    radius = config.dbscan_eps_m
    if radius is None:
        radius = np.radians(config.dbscan_eps) * EARTH_RADIUS_M
    minimum = config.dbscan_min_samples
    gap = config.dbscan_time_gap_min
    if not np.isfinite(radius) or radius <= 0 or isinstance(minimum,bool) or not isinstance(minimum,(int,np.integer)) or minimum < 1 or (gap is not None and (not np.isfinite(gap) or gap <= 0)):
        raise ValueError('聚类半径、最少独立观测数和时间门限必须为正数')
    meta = {'空间半径_m': float(radius), '最少独立观测': int(minimum), '时间邻域_min': gap,
            '方法':'球面距离+时间门限DBSCAN', '重复观测数':0}
    if not {'latitude','longitude'} <= set(black) or not len(black):
        return labels, meta
    coords = black[['latitude','longitude']].apply(pd.to_numeric,errors='coerce')
    valid = coords.latitude.between(-90,90) & coords.longitude.between(-180,180)
    positions = np.flatnonzero(valid)
    keys = coords.iloc[positions].reset_index(drop=True)
    times = time_seconds(black)[positions]
    has_time = 'datetime' in black
    timed = has_time and gap is not None
    if has_time:
        keys['time'] = times
        meta['时间缺失点数'] = int(np.isnan(times).sum())
    unique = ~keys.duplicated()
    inverse, _ = pd.factorize(pd.MultiIndex.from_frame(keys),sort=False)
    rep = np.flatnonzero(unique)
    meta['重复观测数'] = int(len(keys)-len(rep))
    if len(rep) < minimum:
        return labels, meta
    radians = np.radians(keys.loc[unique,['latitude','longitude']].to_numpy())
    times = times[rep]
    tree = BallTree(radians,metric='haversine')
    rows, cols, distances = [], [], []
    edges = 0
    for i in range(len(rep)):
        neighbors, dist = tree.query_radius(radians[i:i+1],r=radius/EARTH_RADIUS_M,return_distance=True,sort_results=True)
        j, distance = neighbors[0], dist[0]*EARTH_RADIUS_M
        if timed:
            keep = (np.isfinite(times[j]) & np.isfinite(times[i]) & (abs(times[j]-times[i]) <= gap*60)) | (j==i)
            j, distance = j[keep],distance[keep]
        edges += len(j)
        if edges > MAX_NEIGHBOR_EDGES:
            raise ValueError('聚类邻接数据过密，请分时段分析；点级筛查结果仍保留')
        rows.append(np.full(len(j),i,dtype=np.int32));cols.append(j)
        # 正的近零值保留同坐标、不同时刻的邻接关系。
        distances.append(np.maximum(distance, min(radius/2,1e-12)))
    graph = csr_matrix((np.concatenate(distances),(np.concatenate(rows),np.concatenate(cols))),shape=(len(rep),len(rep)))
    graph = sort_graph_by_row_values(graph,warn_when_not_sorted=False)
    clustered = DBSCAN(eps=float(radius),min_samples=int(minimum),metric='precomputed',n_jobs=1).fit_predict(graph)
    labels[positions] = clustered[inverse]
    return labels,meta
