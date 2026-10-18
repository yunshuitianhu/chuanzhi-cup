"""从时空场读取逐点观测；时间为相对模拟起点的小时，位置为上游起算桩号。

这里只执行观测算子，不推断 GPS 桩号、绝对时间原点或观测误差。
走航记录必须逐条提供 (time_h, chainage_m)，不能当作同一时刻的沿程剖面。
"""

import numpy as np

from .state import WQResult


def sample_water_quality(result: WQResult, times_h, chainages_m,
                         constituent: str = 'ammonia') -> np.ndarray:
    """在每对时间、桩号上作双线性插值，返回原顺序的一维浓度 (mg/L)。

    不允许越界外推、缺测值或组分替代。插值不提高模型原有的时空分辨率。
    调用方负责确认时间原点、河道方向及 GPS 到桩号的映射。
    """
    fields = {'ammonia': 'ammonia', 'cbod': 'cbod',
              'do': 'dissolved_oxygen', 'nitrate': 'nitrate'}
    if constituent not in fields:
        raise ValueError(f'未实现的观测组分: {constituent}')
    field = getattr(result, fields[constituent])
    if field is None:
        raise ValueError(f'组分 {constituent} 数据不可用')
    t, x = np.asarray(result.t, dtype=float), np.asarray(result.chainage, dtype=float)
    values = np.asarray(field, dtype=float)
    for name, axis in (('时间', t), ('桩号', x)):
        if (axis.ndim != 1 or not len(axis) or not np.all(np.isfinite(axis))
                or np.any(np.diff(axis) <= 0)):
            raise ValueError(f'模型{name}轴须为非空、有限、严格递增的一维数组')
    if values.shape != (len(t), len(x)) or not np.all(np.isfinite(values)):
        raise ValueError('浓度场形状须与时间/桩号轴一致，且须为有限数')
    qt, qx = np.asarray(times_h, dtype=float), np.asarray(chainages_m, dtype=float)
    if qt.ndim != 1 or qx.ndim != 1 or qt.shape != qx.shape:
        raise ValueError('观测时间和桩号须为等长一维数组，逐对对应')
    if not np.all(np.isfinite(qt)) or not np.all(np.isfinite(qx)):
        raise ValueError('观测时间和桩号须为有限数')
    if np.any((qt < t[0]) | (qt > t[-1]) | (qx < x[0]) | (qx > x[-1])):
        raise ValueError('观测时间或桩号超出模拟范围，禁止外推')

    def brackets(axis, query):
        lo = np.maximum(0, np.searchsorted(axis, query, side='right') - 1)
        hi = np.minimum(lo + 1, len(axis) - 1)
        weight = np.divide(query - axis[lo], axis[hi] - axis[lo],
                           out=np.zeros_like(query), where=hi != lo)
        return lo, hi, weight

    t0, t1, wt = brackets(t, qt)
    x0, x1, wx = brackets(x, qx)
    v0 = values[t0, x0] * (1 - wx) + values[t0, x1] * wx
    v1 = values[t1, x0] * (1 - wx) + values[t1, x1] * wx
    return v0 * (1 - wt) + v1 * wt
