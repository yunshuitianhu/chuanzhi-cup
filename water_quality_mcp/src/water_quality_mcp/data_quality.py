"""关键监测值的有效性与原始观测掩码，供加载、检测和 Agent 共用。"""
import numpy as np
import pandas as pd

MEASUREMENTS = {'ammonia_nitrogen': ('氨氮', 0, 1000),
                'dissolved_oxygen': ('溶解氧', 0, 20)}


def coordinate_text(row):
    try:
        lat, lon = float(row['latitude']), float(row['longitude'])
        if np.isfinite(lat) and np.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180:
            return f'{lat:.6f}, {lon:.6f}'
    except (KeyError, TypeError, ValueError):
        pass
    return None


def observed_mask(df, column):
    if column not in df:
        return pd.Series(False, index=df.index)
    _, lo, hi = MEASUREMENTS[column]
    values = pd.to_numeric(df[column], errors='coerce')
    valid = values.between(lo, hi) & np.isfinite(values)
    flag = f'__observed_{column}'
    if flag in df:
        valid &= df[flag].eq(True)
    return valid


def assessment_mask(df):
    valid = pd.Series(True, index=df.index)
    for column in MEASUREMENTS:
        valid &= observed_mask(df, column)
    return valid


def measurement_error(df):
    if df is None or df.empty:
        return '监测数据为空'
    for column, (label, _, _) in MEASUREMENTS.items():
        if column not in df:
            return f'缺少必需监测列：{label}（{column}）'
        if not observed_mask(df, column).any():
            return f'{label}没有有效实测值，无法判定水质'
    if not assessment_mask(df).any():
        return '没有同时包含有效氨氮和溶解氧观测的记录，无法判定水质'
    return None
