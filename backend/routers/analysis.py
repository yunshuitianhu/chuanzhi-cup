"""分析路由：数据摘要、黑臭检测、交叉印证、综合摘要。

全部直接调用既有内核函数（`water_quality_mcp`），不在本层重写任何算法。
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from water_quality_mcp.src.water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    data_summary,
    find_black_spots,
    quality_report,
)
from water_quality_mcp.src.water_quality_mcp.data_quality import coordinate_text

from ..deps import get_session, get_exclusive_session
from ..session import Session
from water_quality_mcp.src.water_quality_mcp.evidence import EVIDENCE_VERSION
from water_quality_mcp.src.water_quality_mcp.quality_control import QualityConfig

router = APIRouter(prefix="/analysis", tags=["analysis"])


class CrossValidationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    time_offset_sec: float = 0.
    time_window_sec: float = Field(30., gt=0)


class SpikeLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    ammonia_nitrogen: FiniteFloat = Field(gt=0, le=1000)
    dissolved_oxygen: FiniteFloat = Field(gt=0, le=20)


class FlatTolerances(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    ammonia_nitrogen: FiniteFloat = Field(ge=0, le=100)
    dissolved_oxygen: FiniteFloat = Field(ge=0, le=20)


class QualityConfigRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    max_gap_s: FiniteFloat = Field(ge=1, le=86400)
    flat_duration_s: FiniteFloat = Field(ge=1, le=86400)
    flat_min_points: int = Field(ge=3, le=1000)
    spike_thresholds: SpikeLimits
    flat_tolerances: FlatTolerances


@router.post('/quality-config')
def update_quality_config(payload: QualityConfigRequest,
                          session: Session = Depends(get_exclusive_session)) -> dict:
    if session.df is None or session.df.empty:
        raise HTTPException(400, '数据未加载')
    config = QualityConfig(**payload.model_dump())
    cfg = replace(session.analysis_config, quality_control=config)
    # 先完成候选计算，失败时不改参数和旧结果；保留所有监测值与物理模型。
    detection = None
    if session.black_spots is not None:
        raw, black = find_black_spots(session.df, cfg)
        if black is None:
            raise HTTPException(400, '质控配置计算未完成，保留原配置')
        detection = raw, black
    summary = quality_report(session.df, cfg).summary
    session.df.attrs['quality_control_config'] = asdict(config)
    if detection:
        session.set_detection(*detection)
    else:
        session.invalidate_report()
        session.results_revision += 1
    return {'quality_config': asdict(config), 'summary': summary,
            'results_revision': session.results_revision}


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
    if parsed is not None and parsed.get('证据版本') != EVIDENCE_VERSION:
        warnings.append('检测摘要使用旧版证据口径，请重新运行检测；历史趋势和指标表述尚未复核。')
    clusters = (parsed or {}).get('聚类详情') or []
    if not isinstance(clusters, list):
        clusters = []
    black = None if parsed is None else {'result':parsed,
        'cluster_fields':sorted(clusters[0]) if clusters and isinstance(clusters[0],dict) else []}
    cross = stored_object(session.cross_validation, '交叉印证')
    comprehensive = stored_object(session.comprehensive_summary, '综合摘要')
    if comprehensive is not None and comprehensive.get('证据版本') != EVIDENCE_VERSION:
        warnings.append('综合摘要使用旧版证据口径，请重新分析生成当前报告。')
    return {'analysis_revision':session.analysis_revision, 'results_revision':session.results_revision,
        'black_spots':black, 'cross_validation':cross,
        'comprehensive':comprehensive, 'warnings':warnings}


@router.get("/summary")
def summary(session: Session = Depends(get_session)) -> dict:
    """数据统计摘要 —— 对应 app.py 的 tool_data_summary。"""
    if session.df is None or len(session.df) == 0:
        raise HTTPException(400, "数据未加载")
    return json.loads(data_summary(session.df, session.data_filename, session.analysis_config))


@router.get('/quality')
def data_quality(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                 category: Literal['review', 'all', 'missing', 'invalid', 'unevaluated'] = 'review',
                 session: Session = Depends(get_session)) -> dict:
    """质量复核分页清单；只读，不更改原表、已有检测、模型或报告。"""
    if session.df is None or session.df.empty:
        raise HTTPException(400, '数据未加载')
    qc = quality_report(session.df, session.analysis_config)
    flag = {'review': 3, 'missing': 9, 'invalid': 4, 'unevaluated': 2}.get(category)
    selected = [row for row in qc.rows if flag is None or row['flag'] == flag]
    records = []
    for row in selected[offset:offset+limit]:
        original = session.df.iloc[row['id']]
        records.append({**row, 'time': str(original.get('datetime', '')),
            'coordinate': coordinate_text(original),
            'input_record_id': int(original['__input_record_id']) if '__input_record_id' in original else None})
    return {'summary': qc.summary, 'records': records, 'total': len(selected),
            'offset': offset, 'limit': limit, 'category': category}


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
