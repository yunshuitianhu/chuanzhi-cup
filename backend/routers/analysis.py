"""分析路由：数据摘要、黑臭检测、交叉印证、综合摘要。

全部直接调用既有内核函数（`water_quality_mcp`），不在本层重写任何算法。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from water_quality_mcp.src.water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    data_summary,
    find_black_spots,
)

from ..deps import get_session, get_exclusive_session
from ..session import Session

router = APIRouter(prefix="/analysis", tags=["analysis"])


class CrossValidationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    time_offset_sec: float = 0.
    time_window_sec: float = Field(30., gt=0)


@router.get('/results')
def saved_results(session: Session = Depends(get_session)) -> dict:
    """读取已经保存的分析结果；不重跑检测、视频解码、模型或外部查询。"""
    warnings = []
    def stored_object(raw, label):
        if raw is None:
            return None
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(value, dict):
                raise ValueError('expected object')
            json.dumps(value, allow_nan=False)
            return value
        except (ValueError, TypeError):
            warnings.append(f'{label}缓存无法读取，请重新运行对应分析。')
            return None
    parsed = stored_object(session.black_spots_json, '检测') if session.black_spots is not None else None
    clusters = (parsed or {}).get('聚类详情') or []
    if not isinstance(clusters, list):
        clusters = []
    black = None if parsed is None else {'result':parsed,
        'cluster_fields':sorted(clusters[0]) if clusters and isinstance(clusters[0],dict) else []}
    cross = stored_object(session.cross_validation, '交叉印证')
    return {'analysis_revision':session.analysis_revision, 'results_revision':session.results_revision,
        'black_spots':black, 'cross_validation':cross,
        'comprehensive':stored_object(session.comprehensive_summary, '综合摘要'), 'warnings':warnings}


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    """数据统计摘要 —— 对应 app.py 的 tool_data_summary。"""
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")
    return json.loads(data_summary(session.df))


@router.post("/black-spots")
def black_spots(session: Session = Depends(get_exclusive_session)) -> dict:
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
        session.set_detection(result_json, black)

    parsed = json.loads(result_json)
    if 'error' in parsed:
        raise HTTPException(400, parsed['error'])
    clusters = parsed.get("聚类详情", []) or []
    return {
        "result": parsed,
        "cluster_fields": sorted(clusters[0].keys()) if clusters else [],
        "analysis_reset": False,
    }


@router.post("/cross-validate")
def cross_validate(payload: CrossValidationRequest | None = None,
                   session: Session = Depends(get_exclusive_session)) -> dict:
    """视频-传感器交叉印证。

    Args:
        time_offset_sec: 视频第 0 秒相对首条监测记录的时间偏移（正=视频滞后）
        time_window_sec: 匹配窗口宽度（秒）
    """
    payload = payload or CrossValidationRequest()
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")
    if not session.video_frames:
        raise HTTPException(400, "未提取视频帧，请先上传视频")

    offset = payload.time_offset_sec
    window = payload.time_window_sec

    ws = session.water_quality_state()
    raw = cross_validate_video_with_monitoring(ws, offset, window)
    result = json.loads(raw) if isinstance(raw, str) else raw
    if "error" in result:
        raise HTTPException(400, result["error"])

    session.set_cross_validation(result)
    return result


@router.get("/comprehensive")
def comprehensive(session: Session = Depends(get_exclusive_session)) -> dict:
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
