"""数据路由：上传走航数据 / 视频，查询加载状态。"""

from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Query
from starlette.concurrency import run_in_threadpool

from water_quality_mcp.src.water_quality_mcp.loaders import extract_video, load_data
from water_quality_mcp.src.water_quality_mcp.detection_support import DETECTION_VERSION
from water_quality_mcp.src.water_quality_mcp.evidence import EVIDENCE_VERSION

from ..deps import get_session, get_exclusive_session
from ..session import Session

router = APIRouter(prefix="/data", tags=["data"])

MAX_DATA_MB = 50
MAX_VIDEO_MB = 500


@router.post("/upload")
async def upload_data(file: UploadFile = File(...),
                      session: Session = Depends(get_exclusive_session)) -> dict:
    """上传并加载走航监测数据（xlsx/xls/csv）。

    与 app.py 一致：加载新数据会**清除上一轮的全部分析结果**，
    避免新旧数据混淆。
    """
    raw = await file.read()
    if len(raw) > MAX_DATA_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过 {MAX_DATA_MB}MB")
    if not raw:
        raise HTTPException(400, "文件为空")

    # 先校验候选数据；失败时保留完整旧会话，避免数据与分析状态脱节。
    df, report = await run_in_threadpool(
        load_data, base64.b64encode(raw).decode(), file.filename or "data.xlsx")
    if df is None or df.empty:
        raise HTTPException(400, report)

    session.clear_all()
    session.df = df
    session.data_filename = file.filename or "data.xlsx"
    return {
        "ok": True,
        "report": report,
        "rows": int(len(df)),
        "columns": list(df.columns),
        "time_range": [str(df["datetime"].min()), str(df["datetime"].max())],
    }


@router.post("/video")
async def upload_video(file: UploadFile = File(...), interval: int = Query(5, ge=1, le=3600),
                       session: Session = Depends(get_exclusive_session)) -> dict:
    """上传巡航视频并抽帧（可选功能，供视频-传感器交叉印证使用）。"""
    raw = await file.read()
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
        "video_frames": len(session.video_frames),
        "video_filename": session.video_filename,
        "has_black_spots": session.black_spots is not None and len(session.black_spots) > 0,
        "black_spot_count": int(len(session.black_spots)) if session.black_spots is not None else 0,
        "has_cross_validation": session.cross_validation is not None,
        "analysis_done": session.analysis_done,
        "analysis_status": session.analysis_status,
        "analysis_steps": session.analysis_steps,
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
