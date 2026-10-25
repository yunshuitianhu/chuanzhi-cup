"""视频区域、画面质量与人工帧复核；声明随已有视频帧字段保存。"""
import base64
from hashlib import sha256
import json
from datetime import datetime,timezone
from copy import deepcopy
import cv2
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from ..deps import get_session,get_exclusive_session
from ..session import Session
from water_quality_mcp.src.water_quality_mcp import video_quality as core

router=APIRouter(prefix='/video',tags=['video'])


class Change(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    expected_revision:str=Field(min_length=64,max_length=64)


class SettingsChange(Change):
    settings:dict
    frame_id:int|None=Field(None,ge=0)
    review:dict|None=None


class FrameChange(Change):
    review:dict


def assessment(session):
    frames=session.video_frames
    context=(session.project_id,session.analysis_revision)
    if not frames:raise HTTPException(400,'请先上传视频并抽帧')
    try:
        # 相同视频在不同项目/数据版本中也不能共用写入令牌。
        key=sha256(json.dumps([core.revision(frames),*context]).encode()).hexdigest()
        cached=getattr(session,'_video_quality_cache',None)
        if cached is None or cached['revision']!=key or cached['_frames'] is not frames:
            cached={**core.inspect_video(frames),'revision':key,'_frames':frames}
        if session.video_frames is not frames or context!=(session.project_id,session.analysis_revision):
            raise HTTPException(409,'视频或项目已变化，请重新载入')
        session._video_quality_cache=cached
        return cached
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc)) from exc


def check(session,expected):
    if assessment(session)['revision']!=expected:raise HTTPException(409,'视频或复核设置已变化，请重新载入后核对再保存')


@router.get('/quality')
def quality(offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=100),
            category:Literal['all','review','usable']='all',session:Session=Depends(get_session)):
    current=assessment(session)
    rows=[r for r in current['rows'] if category=='all' or (r['eligible'] if category=='usable' else not r['eligible'])]
    return {**{k:v for k,v in current.items() if k not in ('rows','_frames')},'rows':rows[offset:offset+limit],
        'total':len(rows),'offset':offset,'limit':limit}


@router.get('/frames/{frame_id}')
def frame(frame_id:int,expected_revision:str=Query(...,min_length=64,max_length=64),
          session:Session=Depends(get_session)):
    current=assessment(session)
    if current['revision']!=expected_revision:raise HTTPException(409,'视频或复核设置已变化，请重新载入')
    if not 0<=frame_id<len(current['_frames']):raise HTTPException(404,'当前视频没有该帧')
    try:
        image=core.decode_frame(current['_frames'][frame_id])
        # 单帧证据按JPEG返回；不信任导入数据声称的媒体类型或SVG/HTML。
        if image.shape[1]>960:image=cv2.resize(image,(960,round(image.shape[0]*960/image.shape[1])))
        ok,encoded=cv2.imencode('.jpg',image)
        if not ok:raise ValueError('帧图像无法编码')
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    if current['_frames'] is not session.video_frames:raise HTTPException(409,'视频已变化，请重新载入')
    return {'revision':expected_revision,'frame':current['rows'][frame_id],
        'image_base64':base64.b64encode(encoded).decode(),'media_type':'image/jpeg'}


@router.put('/settings')
def settings(payload:SettingsChange,session:Session=Depends(get_exclusive_session)):
    check(session,payload.expected_revision)
    try:
        value=core.validate_settings(payload.settings)
        if (payload.frame_id is None)!=(payload.review is None):raise ValueError('帧编号与复核记录须成对提供')
        reviewed=core.validate_review(payload.review) if payload.review is not None else None
        if payload.frame_id is not None and payload.frame_id>=len(session.video_frames):raise ValueError('当前视频没有该帧')
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    frames=list(session.video_frames);frames[0]={**frames[0],'video_settings':deepcopy(value)}
    if reviewed is not None:
        reviewed['reviewed_at']=datetime.now(timezone.utc).isoformat()
        frames[payload.frame_id]={**frames[payload.frame_id],'review':reviewed}
    session.replace_video(frames,session.video_filename)
    return {'revision':assessment(session)['revision'],'settings':value,'notice':core.NOTICE}


@router.put('/frames/{frame_id}')
def review(frame_id:int,payload:FrameChange,session:Session=Depends(get_exclusive_session)):
    check(session,payload.expected_revision)
    if not 0<=frame_id<len(session.video_frames):raise HTTPException(404,'当前视频没有该帧')
    try:value=core.validate_review(payload.review)
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    value['reviewed_at']=datetime.now(timezone.utc).isoformat()
    frames=list(session.video_frames);frames[frame_id]={**frames[frame_id],'review':value}
    session.replace_video(frames,session.video_filename)
    return {'revision':assessment(session)['revision'],'notice':core.NOTICE}
