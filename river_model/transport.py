"""节点控制体积上的守恒输移，质量单位 g，浓度单位 g/m³（等于 mg/L）。"""
import numpy as np


def cell_widths(chainage):
    spacing = np.diff(chainage)
    if len(spacing) == 0 or np.any(~np.isfinite(spacing)) or np.any(spacing <= 0):
        raise ValueError('至少需要两个严格递增的断面桩号')
    return np.r_[spacing[0] / 2, (spacing[:-1] + spacing[1:]) / 2, spacing[-1] / 2]


def geometry(chainage, area, discharge, dispersion):
    if np.any(~np.isfinite(area)) or np.any(area <= 0) or np.any(~np.isfinite(discharge)):
        raise ValueError('水动力结果包含非法过水面积或流量')
    volume = area * cell_widths(chainage)
    q_face = np.r_[discharge[0], (discharge[:-1] + discharge[1:]) / 2, discharge[-1]]
    conductance = np.r_[0., ((area[:-1] + area[1:]) / 2
        * (dispersion[:-1] + dispersion[1:]) / 2 / np.diff(chainage)), 0.]
    rate = (np.maximum(q_face[1:], 0) + np.maximum(-q_face[:-1], 0)
            + conductance[:-1] + conductance[1:]) / volume
    return volume, q_face, conductance, float(rate.max())


def conservative_step(concentration, volume, q_face, conductance, dt, upstream, downstream):
    """迎风对流 + 面通量扩散；内部面通量严格成对抵消。"""
    left = np.concatenate([upstream[:, None], concentration], axis=1)
    right = np.concatenate([concentration, downstream[:, None]], axis=1)
    flux = np.where(q_face >= 0, left * q_face, right * q_face)
    flux[:, 1:-1] -= conductance[1:-1] * np.diff(concentration, axis=1)
    mass = concentration * volume + dt * (flux[:, :-1] - flux[:, 1:])
    if np.any(mass < -1e-8) or np.any(~np.isfinite(mass)):
        raise ValueError('输移步失稳：出现负质量或非有限数值')
    inflow = dt * (np.maximum(flux[:, 0], 0) + np.maximum(-flux[:, -1], 0))
    outflow = dt * (np.maximum(-flux[:, 0], 0) + np.maximum(flux[:, -1], 0))
    return np.maximum(mass, 0) / volume, inflow, outflow
