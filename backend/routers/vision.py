"""Q18 视觉证据路由：显式提交一张当前项目的图片/视频帧。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key

from ..deps import get_session
from ..session import Session
from ..vision import VisionError
from ..vision_evidence import VisionServiceError, observe, observe_sequence

router = APIRouter(prefix='/vision', tags=['vision'])


class ObserveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    kind: Literal['video_frame', 'image_asset', 'video_frames']
    frame_id: int | None = Field(None, ge=0)
    frame_ids: list[int] | None = Field(None, min_length=2, max_length=8)
    video_revision: str | None = Field(None, min_length=64, max_length=64)
    asset_id: str | None = Field(None, min_length=32, max_length=32)
    asset_revision: str | None = Field(None, min_length=64, max_length=64)
    question: str = Field('', max_length=500)

    @model_validator(mode='after')
    def check_source(self):
        if self.kind == 'video_frame':
            if self.frame_id is None or not self.video_revision:
                raise ValueError('视频帧观察须同时提供 frame_id 和 video_revision')
            if self.frame_ids is not None or self.asset_id is not None or self.asset_revision is not None:
                raise ValueError('视频帧观察不能携带序列或图片附件字段')
        elif self.kind == 'video_frames':
            if not self.frame_ids or not self.video_revision:
                raise ValueError('视频序列观察须同时提供 frame_ids 和 video_revision')
            if self.frame_id is not None or self.asset_id is not None or self.asset_revision is not None:
                raise ValueError('视频序列观察不能携带单帧或图片附件字段')
            if any(type(frame_id) is not int or frame_id < 0 for frame_id in self.frame_ids):
                raise ValueError('帧编号必须为非负整数')
            if len(set(self.frame_ids)) != len(self.frame_ids):
                raise ValueError('同一帧不能重复选择')
        else:
            if not self.asset_id or not self.asset_revision:
                raise ValueError('图片观察须同时提供 asset_id 和 asset_revision')
            if self.frame_id is not None or self.frame_ids is not None or self.video_revision is not None:
                raise ValueError('图片观察不能携带视频帧字段')
        return self


@router.post('/observe')
def observe_image(payload: ObserveRequest, session: Session = Depends(get_session)):
    """显式发送一张图片/视频帧，返回结构化可见观察；不写入报告或排查板。"""
    api_key = resolve_api_key('DEEPSEEK_API_KEY')
    if not api_key:
        raise HTTPException(400, '未配置 DeepSeek API Key：可先查看图片和已有记录；配置后重试视觉观察。')
    try:
        if payload.kind == 'video_frames':
            return observe_sequence(session, frame_ids=payload.frame_ids,
                                    video_revision=payload.video_revision,
                                    question=payload.question, api_key=api_key)
        return observe(session,
                       kind=payload.kind,
                       frame_id=payload.frame_id,
                       video_revision=payload.video_revision,
                       asset_id=payload.asset_id,
                       asset_revision=payload.asset_revision,
                       question=payload.question,
                       api_key=api_key)
    except VisionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except VisionServiceError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
