"""地图普通采样点的数据；插值参考与原始观测分开返回。"""
import math

import numpy as np
import pandas as pd

from water_quality_mcp.src.water_quality_mcp.analysis import calc_black_score_vectorized, get_severity_info, quality_report
from water_quality_mcp.src.water_quality_mcp.data_quality import observed_mask

METRICS = ('ammonia_nitrogen', 'dissolved_oxygen', 'cod', 'turbidity', 'ph')


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def map_samples(df, config, quality=None):
    quality = quality or quality_report(df, config)
    masks = {}
    for key in METRICS:
        if key in ('ammonia_nitrogen', 'dissolved_oxygen'):
            masks[key] = observed_mask(df, key).to_numpy(dtype=bool)
        else:
            values = pd.to_numeric(df.get(key, pd.Series(np.nan, index=df.index)), errors='coerce')
            flag = df.get(f'__observed_{key}', pd.Series(True, index=df.index))
            masks[key] = (np.isfinite(values) & flag.eq(True)).to_numpy(dtype=bool)
    measured = masks['ammonia_nitrogen'] & masks['dissolved_oxygen']
    scores = np.full(len(df), np.nan)
    if measured.any():
        scores[measured] = calc_black_score_vectorized(df.iloc[np.flatnonzero(measured)], config)
    result = []
    for pos, row in enumerate(df.itertuples(index=False)):
        lat, lon = finite(getattr(row, 'latitude', None)), finite(getattr(row, 'longitude', None))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        values, interpolated, sources = {}, {}, {}
        for key in METRICS:
            value = finite(getattr(row, key, None))
            values[key] = value if masks[key][pos] else None
            sources[key] = ('observed' if key in ('ammonia_nitrogen', 'dissolved_oxygen') or f'__observed_{key}' in df else 'record') if masks[key][pos] else 'missing'
            if not masks[key][pos] and value is not None:
                interpolated[key] = value
        score = finite(scores[pos])
        time = getattr(row, 'datetime', None)
        result.append({'id': pos, 'coord': [lat, lon],
            'quality': {k: quality.rows[pos][k] for k in ('flag', 'label', 'reasons', 'raw_values', 'raw_text', 'raw_preserved')},
            'time': None if time is None or pd.isna(time) else str(time),
            'values': values, 'interpolated': interpolated, 'sources': sources, 'raw_score': score,
            'grade': get_severity_info(score, config)[1] if score is not None else '关键指标缺测，未判定'})
    return result
