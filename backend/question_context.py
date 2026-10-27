"""Server-owned, bounded question context snapshots; never trust browser prose as facts."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import re
from typing import Annotated, Literal, Union
from uuid import uuid4

import numpy as np
import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from water_quality_mcp.src.water_quality_mcp.agent_safety import citation_catalog
from water_quality_mcp.src.water_quality_mcp.analysis import quality_report
from water_quality_mcp.src.water_quality_mcp.data_quality import observed_mask
from .map_samples import METRICS, finite
from .project_codec import _scrubber
from .text_sources import TextSource, select_text, MAX_QUOTE
from .report_evidence import _digest, record

MAX_CONTEXTS = 20
MAX_RETRIEVALS = 8
MAX_RANGE_RECORDS = 20
LABELS = {'ammonia_nitrogen':('氨氮','mg/L'), 'dissolved_oxygen':('溶解氧','mg/L'),
    'turbidity':('浊度','NTU'), 'ph':('pH',''), 'cod':('COD','mg/L')}
BASE_NOTICE = ('这是用户选定对象的服务器快照，解释与推断仍须核对；仿真、插值、'
    '视觉线索和历史案例不能替代当前实测，也不能确认污染责任。')


class ContextScope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    project_id: str | None = Field(max_length=100)
    analysis_revision: int = Field(ge=0)
    results_revision: int = Field(ge=0)


class SampleContext(ContextScope):
    kind: Literal['sample']
    sample_id: int = Field(ge=0)


class TimeContext(ContextScope):
    kind: Literal['time_range']
    start: str = Field(min_length=1,max_length=64)
    end: str = Field(min_length=1,max_length=64)
    indicator: Literal['ammonia_nitrogen','dissolved_oxygen','turbidity','ph','cod']


class VideoContext(ContextScope):
    kind: Literal['video_frame']
    frame_id: int = Field(ge=0)
    video_revision: str = Field(min_length=64,max_length=64,pattern=r'^[0-9a-f]{64}$')


class RagContext(ContextScope):
    kind: Literal['rag_excerpt']
    retrieval_id: str = Field(min_length=32,max_length=32,pattern=r'^[0-9a-f]{32}$')
    evidence_id: str = Field(min_length=1,max_length=1000)


class TextContext(ContextScope):
    kind: Literal['text_quote']
    text: str = Field(min_length=1,max_length=MAX_QUOTE)
    source: TextSource


class ImageAssetContext(ContextScope):
    kind: Literal['image_asset']
    asset_id: str = Field(min_length=32,max_length=32,pattern=r'^[0-9a-f]{32}$')
    asset_revision: str = Field(min_length=64,max_length=64,pattern=r'^[0-9a-f]{64}$')


ContextRequest = Annotated[Union[SampleContext,TimeContext,VideoContext,RagContext,TextContext,ImageAssetContext], Field(discriminator='kind')]


def _clean(value):
    if isinstance(value, dict): return {str(k):_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,np.ndarray)): return [_clean(v) for v in value]
    if isinstance(value,np.generic): return _clean(value.item())
    if isinstance(value,float): return value if np.isfinite(value) else None
    if isinstance(value,(str,int,bool)) or value is None: return value
    if isinstance(value,(datetime,pd.Timestamp)): return value.isoformat()
    return str(value)


def _safe(value):
    return json.loads(_scrubber()(json.dumps(_clean(value),ensure_ascii=False,allow_nan=False)))


def scope(session):
    """Fingerprint only the monitored table here; video has its own revision token."""
    return {'project_id':session.project_id or None,
        'analysis_revision':session.analysis_revision,'results_revision':session.results_revision,
        'data_version':_digest({'df':session.df,'filename':session.data_filename})}


def _check_request_scope(session, payload):
    expected=(payload.project_id,payload.analysis_revision,payload.results_revision)
    actual=(session.project_id or None,session.analysis_revision,session.results_revision)
    if expected != actual:
        raise HTTPException(409,'项目或数据版本已变化，请从当前页面重新选择对象。')


def _cache(session, name):
    value=getattr(session,name,None)
    if not isinstance(value,dict):
        value={};setattr(session,name,value)
    return value


def _keep(cache, key, value, maximum):
    cache[key]=value
    while len(cache)>maximum: cache.pop(next(iter(cache)))


def _frame(session):
    df=session.df
    if df is None or df.empty: raise HTTPException(400,'请先载入当前监测数据。')
    return df


def _observed(df,key):
    if key in ('ammonia_nitrogen','dissolved_oxygen'):
        return observed_mask(df,key).to_numpy(dtype=bool)
    values=pd.to_numeric(df.get(key,pd.Series(np.nan,index=df.index)),errors='coerce')
    valid=np.isfinite(values).to_numpy(dtype=bool)
    if '__observed_'+key in df: valid &= df['__observed_'+key].eq(True).to_numpy()
    if key=='ph': valid &= values.between(0,14).to_numpy()
    else: valid &= values.ge(0).to_numpy()
    return valid


def _record(session, position, quality, masks=None):
    row=record(session,position,quality)
    masks=masks or {key:_observed(session.df,key) for key in METRICS}
    for key in METRICS:
        if not masks[key][position]: row['values'][key]=None
    # Raw instrument strings may be arbitrarily large; disclose this display bound.
    texts=row.get('quality',{}).get('raw_text',{})
    row['raw_text_truncated']=[key for key,value in texts.items() if isinstance(value,str) and len(value)>256]
    if texts:
        row['quality']={**row['quality'],'raw_text':{
            key:value[:256] if isinstance(value,str) else value for key,value in texts.items()}}
    return _safe(row)


def _value(value,unit):
    return '缺测' if value is None else str(value)+(' '+unit if unit else '')


def _sample(session,payload):
    df=_frame(session)
    if payload.sample_id>=len(df): raise HTTPException(404,'当前数据没有该采样记录。')
    row=_record(session,payload.sample_id,quality_report(df,session.analysis_config))
    summary=(str(row['time'] or '时间缺失')+' · 氨氮 '+_value(row['values']['ammonia_nitrogen'],'mg/L')
        +' · DO '+_value(row['values']['dissolved_oxygen'],'mg/L'))
    if row['coord'] is None: summary+=' · 无有效GPS'
    return {'title':f"采样记录 #{payload.sample_id+1}",'summary':summary,
        'selection':{'sample_id':payload.sample_id},'payload':{'record':row},'anchor':'trace-map'}


def _timestamp(value):
    if not re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}',value):
        raise HTTPException(422,'时间须包含日期和时分，并与监测记录使用一致时区。')
    try:
        stamp=pd.Timestamp(value)
        if pd.isna(stamp): raise ValueError()
        return stamp
    except (ValueError,TypeError,OverflowError) as exc:
        raise HTTPException(422,'时间范围格式无效。') from exc


def _time_range(session,payload):
    df=_frame(session)
    if 'datetime' not in df: raise HTTPException(422,'监测数据缺少可用的采样时间。')
    start,end=_timestamp(payload.start),_timestamp(payload.end)
    if bool(start.tzinfo)!=bool(end.tzinfo): raise HTTPException(422,'起止时间的时区必须一致。')
    try:
        if end<start: raise HTTPException(422,'结束时间不能早于开始时间。')
        stamps=pd.to_datetime(df['datetime'],errors='coerce')
        if not pd.api.types.is_datetime64_any_dtype(stamps.dtype):
            raise HTTPException(422,'监测记录包含混合时区，须先规范时间再选择。')
        timezone=stamps.dt.tz
        if (timezone is not None) != bool(start.tzinfo):
            raise HTTPException(422,'所选时间与监测记录的时区形式不一致，不能推测时区。')
        if timezone is not None: start,end=start.tz_convert(timezone),end.tz_convert(timezone)
        positions=np.flatnonzero((stamps.ge(start)&stamps.le(end)).to_numpy())
    except (TypeError,ValueError,OverflowError) as exc:
        raise HTTPException(422,'监测时间无法与所选范围比较，请核对时区和格式。') from exc
    if not len(positions): raise HTTPException(404,'所选时间范围内没有监测记录。')
    key=payload.indicator;label,unit=LABELS[key]
    sub=df.iloc[positions]
    values=pd.to_numeric(sub.get(key,pd.Series(np.nan,index=sub.index)),errors='coerce')
    mask=_observed(sub,key);valid=values.iloc[np.flatnonzero(mask)]
    stats={'total_records':len(sub),'valid_observations':int(mask.sum()),
        'missing_or_invalid':int(len(sub)-mask.sum()),'unit':unit,
        'minimum':finite(valid.min()) if len(valid) else None,
        'maximum':finite(valid.max()) if len(valid) else None,
        'mean':finite(valid.mean()) if len(valid) else None}
    quality=quality_report(df,session.analysis_config)
    masks={key:_observed(df,key) for key in METRICS}
    rows=[_record(session,int(position),quality,masks) for position in positions[:MAX_RANGE_RECORDS]]
    selection={'start':start.isoformat(),'end':end.isoformat(),'indicator':key}
    return {'title':label+'时间范围','summary':f"{selection['start']} ～ {selection['end']} · {len(sub)}条记录，{int(mask.sum())}条有效原始观测",
        'selection':selection,'payload':{'statistics':stats,'records':rows,
            'returned_records':len(rows),'records_truncated':len(rows)<len(sub),
            'notice':'统计覆盖所选全部记录；示例最多20条，插值和无效读数不计入有效原始观测统计。'},'anchor':'time-series'}


def _video(session,payload):
    from .routers.video import assessment
    current=assessment(session)
    if payload.video_revision!=current['revision']:
        raise HTTPException(409,'视频或ROI/复核设置已变化，请重新选择当前帧。')
    if payload.frame_id>=len(current['rows']): raise HTTPException(404,'当前视频没有该帧。')
    row=_safe(current['rows'][payload.frame_id])
    settings={k:current['settings'][k] for k in ('roi_confirmed','roi_note','sync_confirmed','sync_note','time_offset_sec','time_window_sec','reviewer')}
    quality_label={'usable':'可作视觉线索','needs_review':'待复核','invalid':'无法读取'}.get(row['quality_status'],'待核对')
    summary=f"视频第{payload.frame_id+1}帧 · {row['time_sec'] if row['time_sec'] is not None else '时间无效'}秒 · {quality_label}"
    if not settings['sync_confirmed']: summary+=' · 同步未确认'
    return {'title':f'视频帧 #{payload.frame_id+1}','summary':summary,
        'selection':{'frame_id':payload.frame_id,'video_revision':current['revision']},
        'payload':{'frame':row,'settings':settings,'notice':current['notice'],
            'image_source':{'kind':'video_frame','frame_id':payload.frame_id,
                'video_revision':current['revision'],'frame_number':payload.frame_id+1},
            'source_kind':'视觉辅助线索；提问时服务器可按当前视频版本发送该帧图像；'
                '图像不是浓度测量，也不确认ROI或同步状态'},'anchor':'video-quality'}


def _image_asset(session,payload):
    from .media_assets import MediaError, get_media
    try:
        meta,_=get_media(session,payload.asset_id,payload.asset_revision)
    except MediaError as exc:
        raise HTTPException(422,str(exc)) from exc
    summary=f"{meta['width']}×{meta['height']} · {round(meta['bytes']/1024)}KB · 来源：{meta.get('source_name') or '用户上传截图'}"
    return {'title':(meta.get('source_name') or '用户截图')[:100],'summary':summary,
        'selection':{'asset_id':meta['id'],'asset_revision':meta['sha256']},
        'payload':{'asset':{'asset_id':meta['id'],'sha256':meta['sha256'],'media_type':meta['media_type'],
                'width':meta['width'],'height':meta['height'],'bytes':meta['bytes'],
                'source_name':meta.get('source_name',''),'created_at':meta.get('created_at','')},
            'notice':'这是用户提供的图片材料，不是实测数据；画面内容不能直接当作浓度、水质或污染责任结论。',
            'source_kind':'用户上传图片；提问时服务器按内容哈希发送该图'},
        'notice':'这是用户提供的图片材料，不是实测数据；画面内容不能直接当作浓度、水质或污染责任结论。',
        'path':'/chat','anchor':'image-asset'}


def register_retrieval(session, result):
    """Store exact structured returned snippets; source prose never grants an ID."""
    cases=result.get('检索结果')
    try: citation_catalog(cases)
    except ValueError as exc: raise HTTPException(503,'本次检索含冲突证据ID，不能作为问答上下文。') from exc
    snippets={}
    for case in cases[:10] if isinstance(cases,list) else []:
        if not isinstance(case,dict): continue
        parts=case.get('证据片段')
        for part in parts[:2] if isinstance(parts,list) else []:
            if not isinstance(part,dict): continue
            ident,text=part.get('证据ID'),part.get('原文')
            if not isinstance(ident,str) or not 1<=len(ident)<=1000 or not isinstance(text,str) or not text.strip(): continue
            if len(text)>4000:
                # Reject rather than silently expanding or changing citation support.
                raise HTTPException(503,'本次检索原文片段超出问答支持范围，请缩小检索。')
            snippets[ident]=_safe({'evidence_id':ident,'text':text,'section':str(part.get('章节',''))[:300],
                'truncated':bool(part.get('截断',False)),'title':str(case.get('标题','未提供标题'))[:300],
                'source_file':str(case.get('来源文件',''))[:500],
                'source_notice':str(case.get('出处状态','本次返回片段，原始发布内容未独立核验。'))[:1000]})
    ident=uuid4().hex
    _keep(_cache(session,'_question_retrievals'),ident,{'scope':scope(session),'snippets':snippets},MAX_RETRIEVALS)
    return ident


def _rag(session,payload):
    stored=_cache(session,'_question_retrievals').get(payload.retrieval_id)
    if stored is None: raise HTTPException(404,'当前会话没有该检索快照，请重新检索。')
    if stored['scope']!=scope(session): raise HTTPException(409,'检索所处的项目或数据已变化，请重新检索。')
    snippet=stored['snippets'].get(payload.evidence_id)
    if snippet is None: raise HTTPException(404,'该证据ID不在本次实际检索片段中。')
    notice=BASE_NOTICE+' 当前解释的是本会话当时返回的资料片段；未展示部分不得据此引用。'
    return {'title':snippet['title']+' · '+(snippet['section'] or '原文片段'),
        'summary':snippet['source_file']+' · '+snippet['evidence_id'],
        'selection':{'retrieval_id':payload.retrieval_id,'evidence_id':payload.evidence_id},
        'payload':{'snippet':snippet},'catalog':{snippet['evidence_id']:snippet['text']},
        'excerpt':snippet['text'],'notice':notice,'anchor':'rag-search'}


def create_context(session,payload):
    _check_request_scope(session,payload)
    selected={'sample':_sample,'time_range':_time_range,'video_frame':_video,
        'rag_excerpt':_rag,'text_quote':select_text,'image_asset':_image_asset}[payload.kind](session,payload)
    ident=uuid4().hex
    card={'id':ident,'kind':payload.kind,'title':selected['title'],'summary':selected['summary'],
        'notice':selected.get('notice',BASE_NOTICE),'scope':scope(session),'selection':selected['selection'],
        'source_url':selected.get('path', '/tech' if payload.kind=='rag_excerpt' else '/charts')+'?context_id='+ident+'#'+selected['anchor'],
        'data_identity':session.data_identity}
    if 'excerpt' in selected: card['excerpt']=selected['excerpt']
    card=_safe(card)
    entry={'public':card,'payload':_safe({'kind':payload.kind,'selection':selected['selection'],
        'data_identity':session.data_identity,**selected['payload']}),'catalog':selected.get('catalog',{})}
    _keep(_cache(session,'_question_contexts'),ident,entry,MAX_CONTEXTS)
    return deepcopy(card)


def resolve_context(session, ident):
    if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{32}',ident):
        raise HTTPException(404,'当前会话没有该选择上下文。')
    entry=_cache(session,'_question_contexts').get(ident)
    if entry is None: raise HTTPException(404,'当前会话没有该选择上下文；恢复项目后请重新选择。')
    if entry['public']['scope']!=scope(session):
        raise HTTPException(409,'选择来源已过期，请重新选择；历史卡片不能关联当前数据。')
    if entry['public']['kind']=='video_frame':
        from .routers.video import assessment
        if assessment(session)['revision']!=entry['public']['selection']['video_revision']:
            raise HTTPException(409,'视频或ROI/复核设置已变化，请重新选择。')
    if entry['public']['kind']=='image_asset':
        from .media_assets import MediaError, get_media
        selection=entry['public']['selection']
        try:
            get_media(session,selection.get('asset_id'),selection.get('asset_revision'))
        except MediaError as exc:
            raise HTTPException(409,str(exc)) from exc
    return deepcopy(entry)


def public_context(session, ident):
    return resolve_context(session,ident)['public']