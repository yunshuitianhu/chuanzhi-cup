"""水质专用网格：细分水动力区间，不增加或伪造实测断面。"""
import numpy as np

from .transport import geometry, cell_widths


def refined_chainage(chainage, refinement):
    x = np.asarray(chainage, dtype=float)
    cell_widths(x)  # 同时校验严格递增的有限桩号。
    if type(refinement) is not int or refinement not in (1, 2, 4, 8):
        raise ValueError('水质网格细分数须为整数 1、2、4 或 8')
    if refinement == 1:
        return x
    if (len(x)-1)*refinement+1 > 1201:
        raise ValueError('细分后水质节点超过 1201，请降低细分数')
    fractions = np.arange(refinement)/refinement
    return np.r_[(x[:-1, None]+np.diff(x)[:, None]*fractions).ravel(), x[-1]]


def check_work_budget(chainage, area_min, area_max, q_min, q_max, dispersion,
                      seconds, outer_intervals, output_count):
    """用水动力包络估计稳定性子步上界；超限拒绝，不静默降低分辨率。

    内部A/Q对时间及空间均线性插值，故包络覆盖实际中间时刻。
    返回的是计算量上界，不是已经完成的子步数或误差估计。
    """
    _, q_high, diffusion, _ = geometry(chainage, area_max, q_max, dispersion)
    _, q_low, _, _ = geometry(chainage, area_min, q_min, dispersion)
    volume_min = area_min*cell_widths(chainage)
    rate = float(np.max((np.maximum(q_high[1:], 0)+np.maximum(-q_low[:-1], 0)
        + diffusion[:-1]+diffusion[1:])/volume_min))
    steps_bound = int(np.ceil(seconds*rate/.9)) + int(outer_intervals)
    updates_bound = steps_bound*len(chainage)
    if steps_bound > 200000 or updates_bound > 50_000_000:
        raise ValueError('当前水质网格和时长的显式计算量过大，请降低细分数或缩短模拟时长；未自动降低精度')
    if output_count*len(chainage)*4 > 8_000_000:
        raise ValueError('水质输出数组过大，请增大输出间隔或降低细分数')
    return steps_bound
