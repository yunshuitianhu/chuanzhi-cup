"""Estimate the covered reach for repeated passes along a single corridor."""
import numpy as np
from scipy.ndimage import gaussian_filter1d

from .cruise_alignment import RADIUS_M, _longitude_delta, distance, project_points, reference_geometry


def _turns(axis, threshold):
    """Find sustained reversals, ignoring small changes around the extrema."""
    start = peak = 0
    direction = 0
    for index, value in enumerate(axis):
        if direction == 0:
            if abs(value - axis[start]) >= threshold:
                direction = 1 if value > axis[start] else -1
                peak = index
        elif direction * (value - axis[peak]) >= 0:
            peak = index
        elif direction * (axis[peak] - value) >= threshold:
            yield peak
            direction = -direction
            peak = index


def repeated_track_length(points):
    """Count one reference pass only, provided the remaining points fit it."""
    failure = '返航轨迹无法确认沿同一河段重合，请核实参考线后手工填写河段长度'
    if not 6 <= len(points) <= 20000:
        raise ValueError(failure)
    steps = distance(points[:-1], points[1:])
    moving = steps[steps >= .01]
    if not len(moving) or steps.max() > max(30., 6 * np.median(moving)):
        raise ValueError(failure)

    # Use a local metric plane for finding turnarounds, not for measuring river length.
    longitude_scale = np.cos(np.radians(points[:, 0].mean()))
    offsets = np.radians(np.column_stack((points[:, 0] - points[0, 0],
        _longitude_delta(points[:, 1] - points[0, 1]) * longitude_scale))) * RADIUS_M
    smooth = gaussian_filter1d(offsets, sigma=2., axis=0, mode='nearest')
    _, vectors = np.linalg.eigh(np.cov(smooth.T))
    axis = smooth @ vectors[:, -1]
    threshold = max(12., .03 * np.ptp(axis))
    turns = list(_turns(axis, threshold))
    if not turns:
        raise ValueError(failure)

    bounds = [0, *turns, len(points) - 1]
    start, end = max(zip(bounds, bounds[1:]), key=lambda pair: abs(axis[pair[1]] - axis[pair[0]]))
    candidates = smooth[start:end + 1]
    candidates = candidates[np.r_[True, np.linalg.norm(np.diff(candidates, axis=0), axis=1) >= .01]]
    if len(candidates) < 2 or abs(axis[end] - axis[start]) < 40.:
        raise ValueError(failure)
    # Limit the projection matrix. The sampled line still follows the observed bends.
    node_count = min(len(candidates), 240, 5_000_000 // len(points))
    candidates = candidates[np.linspace(0, len(candidates) - 1, node_count, dtype=int)]
    reference = np.degrees(candidates / RADIUS_M)
    reference[:, 0] += points[0, 0]
    reference[:, 1] = _longitude_delta(reference[:, 1] / longitude_scale + points[0, 1])
    try:
        _, lengths = reference_geometry(reference)
        _, errors, ambiguous = project_points(points, reference, ambiguity_m=.5)
    except ValueError as exc:
        raise ValueError(failure) from exc
    if np.percentile(errors, 95) > 6. or errors.max() > 15. or ambiguous.mean() > .02:
        raise ValueError(failure)
    return float(lengths[-1])
