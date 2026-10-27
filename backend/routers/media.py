"""图片附件上传、预览与删除；按会话隔离，不暴露磁盘路径。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from ..deps import get_exclusive_session, get_session
from ..media_assets import MAX_UPLOAD_BYTES, MediaError, delete_media, get_media, store_media
from ..session import Session

router = APIRouter(prefix='/media', tags=['media'])


@router.post('')
async def upload(file: UploadFile = File(...),
                 session: Session = Depends(get_exclusive_session)) -> dict:
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise HTTPException(400, '图片文件为空')
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, '图片超过8MB上限，请压缩或裁剪后重试')
    try:
        meta = await run_in_threadpool(store_media, session, raw, file.filename or '截图')
    except MediaError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'ok': True, **meta}


@router.get('/{asset_id}')
def preview(asset_id: str, session: Session = Depends(get_session)):
    try:
        meta, data = get_media(session, asset_id)
    except MediaError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content=data, media_type=meta['media_type'],
                    headers={'Cache-Control': 'private, max-age=0, must-revalidate',
                             'X-Content-Type-Options': 'nosniff'})


@router.delete('/{asset_id}')
def remove(asset_id: str, session: Session = Depends(get_exclusive_session)):
    try:
        delete_media(session, asset_id)
    except MediaError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'ok': True}
