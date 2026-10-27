"""数据路由：上传走航数据 / 视频，查询加载状态。"""

from __future__ import annotations

import base64
import json
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from water_quality_mcp.src.water_quality_mcp.loaders import extract_video, load_data
from water_quality_mcp.src.water_quality_mcp.analysis import find_black_spots
from water_quality_mcp.src.water_quality_mcp.detection_support import DETECTION_VERSION
from water_quality_mcp.src.water_quality_mcp.evidence import EVIDENCE_VERSION

from ..deps import get_session, get_exclusive_session
from ..session import Session
from .. import data_service, sample_catalog
from ..projects import repository, apply_snapshot
from ..project_codec import _scrubber
from .projects import shelve, current_state, capture_form, storage_errors

router = APIRouter(prefix="/data", tags=["data"])
logger = logging.getLogger(__name__)

MAX_DATA_MB = 50
MAX_VIDEO_MB = 500


@router.post("/upload")
async def upload_data(file: UploadFile = File(...),
                      session: Session = Depends(get_exclusive_session)) -> dict:
    """上传并加载走航监测数据（xlsx/xls/csv）。

    与 app.py 一致：加载新数据会**清除上一轮的全部分析结果**，
    避免新旧数据混淆。
    """
    raw = await file.read(MAX_DATA_MB * 1024 * 1024 + 1)
    if len(raw) > MAX_DATA_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_DATA_MB}MB")
    if not raw:
        raise HTTPException(400, "文件为空")

    # 先校验候选数据；失败时保留完整旧会话，避免数据与分析状态脱节。
    filename = file.filename or "data.xlsx"
    df, report = await run_in_threadpool(data_service.parse_monitoring, raw, filename, load_data)
    if df is None or df.empty:
        raise HTTPException(400, report)

    data_service.apply_monitoring(session, df, filename)
    return {
        "ok": True,
        "report": report,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "time_range": [str(df["datetime"].min()), str(df["datetime"].max())],
    }


class SampleLoadRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    project_id: str | None = Field(None, min_length=32, max_length=32)
    analysis_revision: int = Field(ge=0)
    results_revision: int = Field(ge=0)
    detect: bool = False
    river_form: dict | None = None


@router.get('/samples')
def samples() -> dict:
    """只读示例目录；不加载数据、不创建演示项目、不读取任意路径。"""
    try:
        return sample_catalog.list_samples()
    except sample_catalog.SampleCatalogError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post('/samples/{sample_id}/load')
async def load_sample(sample_id: str, payload: SampleLoadRequest,
                      session: Session = Depends(get_exclusive_session)) -> dict:
    """校验候选文件后保存当前项目，再在独立演示项目中载入；可显式执行基础筛查。"""
    try:
        item, raw = sample_catalog.get_sample(sample_id)
    except sample_catalog.SampleNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except sample_catalog.SampleCatalogError as exc:
        raise HTTPException(503, str(exc)) from exc
    current_project = session.project_id or None
    if payload.project_id != current_project:
        raise HTTPException(409, '当前项目已变化，请刷新后重新选择示例。')
    if payload.analysis_revision != session.analysis_revision:
        raise HTTPException(409, '监测数据版本已变化，请刷新后重新选择示例。')
    if payload.results_revision != session.results_revision:
        raise HTTPException(409, '分析结果版本已变化，请刷新后重新选择示例。')

    try:
        df, report = await run_in_threadpool(data_service.parse_monitoring, raw, item['file'])
    except Exception as exc:  # noqa: BLE001 —— 内置资源损坏按服务错误处理
        logger.warning('示例解析异常（%s）', type(exc).__name__)
        df, report = None, '示例数据无法解析'
    if df is None or df.empty:
        raise HTTPException(503, '内置示例数据无法解析；请重新准备示例资源或联系维护者。')

    # 候选已通过；先按既有项目规则保存当前项目，失败则不切换。
    with storage_errors():
        capture_form(session, payload.river_form)
        shelve(session)
        df.attrs['sample_meta'] = {'id': item['id'], 'version': item['version'],
            'title': item['title'], 'data_kind': item['data_kind'], 'source_reference': item['source_reference']}
        blank = Session(id=session.id)
        blank.target_water_class = session.target_water_class
        data_service.apply_monitoring(blank, df, item['file'])
        info = repository().write(blank, f"仿真示例 {item['title']}", new=True)
        _, snapshot = repository().read(info['id'])
        apply_snapshot(session, info, snapshot)

    detection = {'requested': bool(payload.detect), 'ok': False}
    if payload.detect:
        try:
            raw_detection, black = await run_in_threadpool(
                find_black_spots, session.df, session.analysis_config)
            parsed = json.loads(raw_detection) if isinstance(raw_detection, str) else raw_detection
            if not isinstance(parsed, dict) or parsed.get('error') or black is None:
                raise ValueError('筛查未返回可用的黑点结果')
            session.set_detection(raw_detection, black)
            saved = True
            try:
                with storage_errors():
                    repository().write(session)
            except HTTPException as exc:
                saved = False
                detection_warning = str(exc.detail)
            else:
                detection_warning = ''
            detection = {'requested': True, 'ok': True,
                         'black_spots': int(len(black)),
                         'clusters': parsed.get('聚类热点数'),
                         'saved': saved}
            if not saved:
                detection['warning'] = '数据已载入且筛查在会话中可用，但项目保存失败：' + detection_warning
        except Exception as exc:  # noqa: BLE001 —— 检测失败不回滚已载入数据
            logger.warning('示例载入后筛查失败（%s）', type(exc).__name__)
            detection = {'requested': True, 'ok': False,
                         'error': _scrubber()(f'{type(exc).__name__}: {exc}')[:300]}

    return {'ok': True, 'sample': sample_catalog.public_sample(item),
            'report': str(report)[:2000], 'rows': int(len(session.df)),
            'columns': list(session.df.columns),
            'time_range': [str(session.df['datetime'].min()), str(session.df['datetime'].max())],
            'data_identity': session.data_identity,
            'video_frames': len(session.video_frames),
            'detection': detection,
            'project_state': current_state(session)}


@router.post("/video")
async def upload_video(file: UploadFile = File(...), interval: int = Query(5, ge=1, le=3600),
                       session: Session = Depends(get_exclusive_session)) -> dict:
    """上传巡航视频并抽帧（可选功能，供视频-传感器交叉印证使用）。"""
    raw = await file.read(MAX_VIDEO_MB * 1024 * 1024 + 1)
    if len(raw) > MAX_VIDEO_MB * 1024 * 1024:
        raise HTTPException(413, f"视频超过 {MAX_VIDEO_MB}MB")
    if not raw:
        raise HTTPException(400, "文件为空")

    frames, report = await run_in_threadpool(
        extract_video, base64.b64encode(raw).decode(), interval)
    if not frames:
        raise HTTPException(400, report)
    session.replace_video(frames, file.filename or "video.mp4")
    return {"ok": bool(frames), "report": report, "frames": len(frames)}


@router.get("/status")
def status(session: Session = Depends(get_session)) -> dict:
    """当前会话的数据与分析状态 —— 前端顶栏/侧栏据此显示。"""
    df = session.df
    out: dict = {
        "has_data": df is not None and len(df) > 0,
        "rows": int(len(df)) if df is not None else 0,
        "data_filename": session.data_filename,
        'data_identity': session.data_identity,
        'sample_meta': session.df.attrs.get('sample_meta') if session.df is not None else None,
        "video_frames": len(session.video_frames),
        "video_filename": session.video_filename,
        "has_black_spots": session.black_spots is not None and len(session.black_spots) > 0,
        "black_spot_count": int(len(session.black_spots)) if session.black_spots is not None else 0,
        "has_cross_validation": session.cross_validation is not None,
        "analysis_done": session.analysis_done,
        "analysis_status": session.analysis_status,
        "analysis_steps": session.analysis_steps,
        "agent_run": session.agent_run.snapshot() if session.agent_run else None,
        "has_detection": session.black_spots is not None,
        "analysis_revision": session.analysis_revision,
        "results_revision": session.results_revision,
        "target_water_class": session.target_water_class,
        "messages": len(session.messages),
    }
    if df is not None and len(df) > 0:
        out["time_range"] = [str(df["datetime"].min()), str(df["datetime"].max())]
    try:
        evidence = json.loads(session.black_spots_json or '{}')
        evidence = evidence if isinstance(evidence, dict) else {}
        version = evidence.get('算法版本')
    except (ValueError,TypeError):
        evidence = {}
        version = None
    out['detection_algorithm_version'] = version
    out['detection_outdated'] = session.black_spots is not None and (
        version != DETECTION_VERSION or evidence.get('证据版本') != EVIDENCE_VERSION)
    return out


@router.get("/preview")
def preview(limit: int = 50, session: Session = Depends(get_session)) -> dict:
    """数据预览（前 N 行）—— 前端表格展示用，避免一次性传回 1800 行。"""
    df = session.df
    if df is None or len(df) == 0:
        raise HTTPException(400, "数据未加载")
    cols = [c for c in ["datetime", "longitude", "latitude", "ammonia_nitrogen",
                        "dissolved_oxygen", "turbidity", "ph", "cod"] if c in df.columns]
    head = df[cols].head(max(1, min(limit, 500)))
    return {
        "columns": cols,
        "rows": json.loads(head.to_json(orient="records", date_format="iso")),
        "total": int(len(df)),
    }
