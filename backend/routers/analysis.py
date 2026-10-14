"""分析路由：数据摘要、黑臭检测、交叉印证、综合摘要。

全部直接调用既有内核函数（`water_quality_mcp`），不在本层重写任何算法。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from water_quality_mcp.src.water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    data_summary,
    find_black_spots,
)

from ..deps import get_session
from ..session import Session

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    """数据统计摘要 —— 对应 app.py 的 tool_data_summary。"""
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")
    return json.loads(data_summary(session.df))


@router.post("/black-spots")
def black_spots(session: Session = Depends(get_session)) -> dict:
    """黑臭检测 + DBSCAN 聚类。

    返回两部分：
      - `result`  —— 内核原始 JSON（中文键，供技术展示页逐字段渲染）
      - `cluster_fields` —— 聚类详情面板实际可用的字段名清单
        （前端据此渲染，避免像原 app.py 那样读了不存在的键而显示空白）
    """
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")

    cfg = session.analysis_config
    result_json, black = find_black_spots(session.df, cfg)
    if black is not None:
        session.black_spots = black
        session.black_spots_json = result_json

    parsed = json.loads(result_json)
    clusters = parsed.get("聚类详情", []) or []
    return {
        "result": parsed,
        "cluster_fields": sorted(clusters[0].keys()) if clusters else [],
        "analysis_reset": False,
    }


@router.post("/cross-validate")
def cross_validate(payload: dict | None = None,
                   session: Session = Depends(get_session)) -> dict:
    """视频-传感器交叉印证。

    Args:
        time_offset_sec: 视频第 0 秒相对首条监测记录的时间偏移（正=视频滞后）
        time_window_sec: 匹配窗口宽度（秒）
    """
    payload = payload or {}
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")
    if not session.video_frames:
        raise HTTPException(400, "未提取视频帧，请先上传视频")

    offset = int(payload.get("time_offset_sec", 0) or 0)
    window = int(payload.get("time_window_sec", 30) or 30)

    ws = session.water_quality_state()
    raw = cross_validate_video_with_monitoring(ws, offset, window)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if "error" in result:
        raise HTTPException(400, result["error"])

    session.cross_validation = result
    return result


@router.get("/comprehensive")
def comprehensive(session: Session = Depends(get_session)) -> dict:
    """综合摘要 —— Agent 与报告生成的数据源，供技术展示页核对。"""
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")

    ws = session.water_quality_state()
    raw = build_comprehensive_summary(ws, session.get_river_state(), session.analysis_config)
    session.comprehensive_summary = raw
    return json.loads(raw)


@router.get("/captions")
def captions(session: Session = Depends(get_session)) -> dict:
    """图表说明文字（数据驱动生成）—— 前端在每张图下方展示。"""
    return {"captions": session.captions}
