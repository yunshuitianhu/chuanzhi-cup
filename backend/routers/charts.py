"""图表数据路由 —— 返回 **JSON 数据**而非图片。

设计取舍
--------
原 app.py 用 matplotlib 在后端出 PNG，前端只能 <img>。新前端改为：
后端只出数据，ECharts / 地图组件在前端渲染。好处是
  1. 可缩放、可悬浮查看数值、可切换图例 —— 体验差距很大
  2. 这正是赛题"前端工程能力 + 数据分析与可视化"要考的点
  3. 后端不再依赖 matplotlib 的字体、DPI 等环境问题

因此本模块**不 import matplotlib**，只做数据整形。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query

from water_quality_mcp.src.water_quality_mcp.analysis import (
    get_cluster_extent,
    get_severity_info,
)

from ..deps import get_session
from ..session import Session

router = APIRouter(prefix="/charts", tags=["charts"])

# 单条曲线的最大点数。超过则等距抽样 —— 1800 点 ECharts 画得动，
# 但十万级会卡；抽样后前端仍能看出趋势。
MAX_POINTS = 4000

# 与原 matplotlib 版本一致的四个面板
PANELS = [
    ("ammonia_nitrogen", "氨氮", "mg/L", "nh3", "#E94560"),
    ("dissolved_oxygen", "溶解氧", "mg/L", "do", "#3388FF"),
    ("turbidity", "浑浊度", "NTU", None, "#8B5CF6"),
    ("ph", "pH", "", None, "#10B981"),
]


def _clean(v):
    """把 NaN/inf 转成 None —— JSON 不支持它们。

    注意：不能用 `v or None`，因为 0.0 是合法数值。
    """
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)


def _series(values) -> list:
    return [_clean(v) for v in values]


def _downsample(n: int) -> np.ndarray:
    if n <= MAX_POINTS:
        return np.arange(n)
    return np.linspace(0, n - 1, MAX_POINTS).astype(int)


@router.get("/time-series")
def time_series(session: Session = Depends(get_session)) -> dict:
    """时间序列四面板数据 + 黑点散点。

    对应 app.py 中 tool_generate_time_series 生成的 time_series.png，
    但改为返回数据。
    """
    df = session.df
    if df is None or len(df) == 0:
        raise HTTPException(400, "数据未加载")

    idx = _downsample(len(df))
    sub = df.iloc[idx]

    panels = []
    limits = session.analysis_config.get_target_limits()
    for col, label, unit, limit_key, color in PANELS:
        if col not in df.columns:
            continue
        panels.append({
            "key": col,
            "label": label,
            "unit": unit,
            "limit": limits.get(limit_key),
            "limit_direction": "minimum" if limit_key == "do" else "maximum",
            "color": color,
            "values": _series(sub[col].values),
        })

    # 黑点散点：按严重等级着色，颜色与后端 GB3838 分级一致
    spots = []
    black = session.black_spots
    if black is not None and len(black) > 0:
        b = black.iloc[_downsample(len(black))] if len(black) > MAX_POINTS else black
        for _, r in b.iterrows():
            _, label, color = get_severity_info(float(r["black_score"]), session.analysis_config)
            spots.append({
                "t": str(r["datetime"]),
                "ammonia": _clean(r.get("ammonia_nitrogen")),
                "do": _clean(r.get("dissolved_oxygen")),
                "turbidity": _clean(r.get("turbidity")),
                "ph": _clean(r.get("ph")),
                "score": _clean(r["black_score"]),
                "severity": r.get("severity", "normal"),
                "severity_label": r.get("severity_label", ""),
                "color": color,
            })

    return {
        "target_water_class": session.target_water_class,
        "time": [str(t) for t in sub["datetime"].values],
        "panels": panels,
        "black_spots": spots,
        "total_points": int(len(df)),
        "returned_points": int(len(idx)),
        "downsampled": bool(len(idx) < len(df)),
    }


@router.get("/trace-map")
def trace_map(session: Session = Depends(get_session)) -> dict:
    """溯源地图数据：轨迹、聚类范围、Top5 严重点位、热力点。

    对应 trace_map.html（folium），改为返回数据由前端地图组件渲染。
    """
    df = session.df
    black = session.black_spots
    if df is None or len(df) == 0:
        raise HTTPException(400, "数据未加载")
    if black is None or len(black) == 0:
        raise HTTPException(400, "请先运行黑臭检测")

    if not {'latitude', 'longitude'} <= set(df.columns):
        raise HTTPException(400, '缺少经纬度，无法绘制溯源地图')
    coords = df[["latitude", "longitude"]].dropna()
    if coords.empty:
        raise HTTPException(400, '没有有效经纬度，无法绘制溯源地图')
    track = [[_clean(r.latitude), _clean(r.longitude)] for r in coords.itertuples()]

    clusters = []
    for cid in sorted(black["cluster_id"].unique()):
        if cid == -1:
            continue
        c = black[black["cluster_id"] == cid]
        avg = float(c["black_score"].mean())
        _, sev_label, sev_color = get_severity_info(avg, session.analysis_config)
        clat, clon, radius = get_cluster_extent(c)
        clusters.append({
            "id": int(cid),
            "count": int(len(c)),
            "center": [_clean(clat), _clean(clon)],
            "radius_m": _clean(radius),
            "avg_score": _clean(avg),
            "grade": sev_label,
            "color": sev_color,
            "time_range": [str(c["datetime"].min()), str(c["datetime"].max())],
            "ammonia_mean": _clean(c["ammonia_nitrogen"].mean()) if "ammonia_nitrogen" in c else None,
            "do_mean": _clean(c["dissolved_oxygen"].mean()) if "dissolved_oxygen" in c else None,
        })

    top5 = []
    for i, (_, r) in enumerate(black.nlargest(5, "black_score").iterrows(), 1):
        _, label, color = get_severity_info(float(r["black_score"]), session.analysis_config)
        top5.append({
            "rank": i,
            "coord": [_clean(r["latitude"]), _clean(r["longitude"])],
            "time": str(r["datetime"]),
            "score": _clean(r["black_score"]),
            "grade": label,
            "color": color,
            "ammonia": _clean(r.get("ammonia_nitrogen")),
            "do": _clean(r.get("dissolved_oxygen")),
            "cod": _clean(r.get("cod")),
            "turbidity": _clean(r.get("turbidity")),
            "ph": _clean(r.get("ph")),
        })

    # 热力点（黑点密度）
    heat = [[_clean(r.latitude), _clean(r.longitude), 1.0]
            for r in black[["latitude", "longitude"]].dropna().itertuples()]

    center = [_clean(df["latitude"].mean()), _clean(df["longitude"].mean())]
    return {"center": center, "track": track, "clusters": clusters,
            "top5": top5, "heat": heat,
            "bounds": {
                "lat": [_clean(df["latitude"].min()), _clean(df["latitude"].max())],
                "lon": [_clean(df["longitude"].min()), _clean(df["longitude"].max())],
            }}


@router.get("/cross-validation")
def cross_validation(session: Session = Depends(get_session)) -> dict:
    """视频-传感器交叉印证数据（相关系数矩阵 + 一致性）。"""
    cv = session.cross_validation
    if not cv:
        raise HTTPException(400, "尚未运行交叉印证")
    if "error" in cv:
        raise HTTPException(400, cv["error"])
    return {
        "correlations": cv.get("相关系数矩阵", []),
        "agreement": cv.get("黑臭判定一致性", {}),
        "alignment": cv.get("时间对齐", {}),
        "findings": cv.get("关键发现", []),
    }
