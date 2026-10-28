"""本机项目操作：显式保存，切换前保存，导入创建副本。"""
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
import math
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_session, get_exclusive_session
from ..session import Session
from ..projects import repository, apply_snapshot, ProjectConflict, ProjectMissing, _name
from ..project_codec import MAX_PACKAGE, ProjectError, load_project

router=APIRouter(prefix='/projects',tags=['projects'])
FORM_KEYS={'n_cross_sections','mannings_n','river_name','length_m','width_avg_m','depth_avg_m',
    'longitudinal_slope','bed_elevation_avg_m','high_water_mean_m','ammonia_nh3_mgL',
    'dissolved_oxygen_mgL','bod_cbod_mgL','water_temp_C','k_nitrification','k_cbod_decay',
    'k_sediment_oxygen_demand','do_saturation_mgL','upstream_flow_m3s','tidal_amplitude_m',
    'duration_h','load_kg','source_start_h','duration_min','chainage_m','pollutant_type','wq_refinement'}


class SaveRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    name: str | None = Field(None,min_length=1,max_length=100)
    river_form: dict | None = None


class CreateRequest(SaveRequest):
    name: str = Field(min_length=1,max_length=100)
    from_current: bool = True


@contextmanager
def storage_errors():
    try:
        yield
    except ProjectConflict as exc:
        raise HTTPException(409,str(exc)) from exc
    except ProjectMissing as exc:
        raise HTTPException(404,str(exc)) from exc
    except ProjectError as exc:
        raise HTTPException(400,str(exc)) from exc
    except (OSError,sqlite3.Error) as exc:
        raise HTTPException(503,'项目存储暂不可用；当前会话仍保留，请检查磁盘空间或目录权限') from exc


def capture_form(session,form):
    if form is None:
        return
    if set(form)-FORM_KEYS or any(not (v is None or isinstance(v,(str,int,float)))
        or isinstance(v,bool) or (isinstance(v,float) and not math.isfinite(v))
        or (isinstance(v,str) and len(v)>100) for v in form.values()):
        raise ProjectError('河道表单包含未知字段或无效值')
    session.river_ui['form']=dict(form)


def shelve(session):
    repo=repository()
    if session.restore_error:
        # 损坏的原快照不写回；允许打开其它项目/导入备份。恢复期间新录入的数据另存。
        if session.df is not None or session.video_frames or session.river_state or session.messages or session.investigations['items'] or session.side_threads or session.media_assets:
            return repo.write(session,'恢复期间临时数据 '+datetime.now().strftime('%m-%d %H:%M:%S'),new=True)
        return None
    if session.project_id:
        return repo.write(session)
    if session.df is not None or session.video_frames or session.river_state or session.messages or session.river_ui or session.investigations['items'] or session.side_threads or session.media_assets:
        return repo.write(session,'未命名项目 '+datetime.now().strftime('%m-%d %H:%M:%S'),new=True)
    return None


def current_state(session):
    current=repository().info(session.project_id) if session.project_id else None
    if current:
        current={k:v for k,v in current.items() if k!='snapshot'}
        current['revision']=session.project_revision
    model=session.river_state
    return {'projects':repository().list(),'current':current,'restore_error':session.restore_error,
        'river_ui':session.river_ui,
        'model':None if model is None else {'config':asdict(model.config),
            'cross_section_count':len(model.cross_sections),
            'initialized':model.model_initialized,'has_hydro':model.has_hydro(),'has_wq':model.has_wq(),
            'hydro_params':model.hydro_result.params if model.hydro_result else {},
            'wq_params':model.wq_result.params if model.wq_result else {}}}


@router.get('')
def list_projects(session: Session=Depends(get_session)):
    with storage_errors():
        return current_state(session)


@router.post('')
def create_project(payload:CreateRequest,session:Session=Depends(get_exclusive_session)):
    with storage_errors():
        _name(payload.name)
        capture_form(session,payload.river_form)
        # 另存为可解决版本冲突，原项目的旧版本仍在；不先写回冲突项目。
        if payload.from_current:
            if session.project_id:
                try:
                    repository().write(session)
                except ProjectConflict:
                    pass
            repository().write(session,payload.name,new=True)
        else:
            shelve(session)
            blank=Session(id=session.id)
            info=repository().write(blank,payload.name,new=True,bind_session=False)
            repository().activate_new(session,info)
        return current_state(session)


@router.post('/save')
def save_project(payload:SaveRequest,session:Session=Depends(get_exclusive_session)):
    with storage_errors():
        if not session.project_id:
            raise ProjectError('请先将当前内容另存为项目')
        capture_form(session,payload.river_form)
        repository().write(session,payload.name)
        return current_state(session)


@router.post('/import')
async def import_project(file:UploadFile=File(...),river_form:str=Form('null'),
                         session:Session=Depends(get_exclusive_session)):
    import json
    raw=await file.read(MAX_PACKAGE+1)
    with storage_errors():
        snapshot=load_project(raw)
        try:
            form=json.loads(river_form)
            if form is not None and not isinstance(form,dict):
                raise ValueError()
        except (ValueError,TypeError) as exc:
            raise ProjectError('河道表单格式无效') from exc
        capture_form(session,form)
        shelve(session)
        candidate=Session(id=session.id)
        for key,value in snapshot.state.items():
            setattr(candidate,key,value)
        # 先物化导入附件，后续重新打包不会丢失图片/动画。
        apply_snapshot(candidate,{'id':'0'*32,'revision':0},snapshot)
        info=repository().write(candidate,snapshot.name,new=True,bind_session=False)
        repository().activate_new(session,info)
        return current_state(session)


@router.post('/{project_id}/open')
def open_project(project_id:str,payload:SaveRequest,session:Session=Depends(get_exclusive_session)):
    with storage_errors():
        info,snapshot=repository().read(project_id)
        if session.project_id==project_id:
            capture_form(session,payload.river_form)
            return current_state(session)
        capture_form(session,payload.river_form)
        shelve(session)
        # 旧项目保存期间目标可能在另一会话被更新，读取最新已保存版本。
        info,snapshot=repository().read(project_id)
        repository().activate(session,info,snapshot)
        return current_state(session)


@router.post('/{project_id}/export')
def export_project(project_id:str,payload:SaveRequest,session:Session=Depends(get_exclusive_session)):
    with storage_errors():
        if session.project_id==project_id:
            capture_form(session,payload.river_form)
            repository().write(session)
        _,raw=repository().raw(project_id)
        return Response(raw,media_type='application/zip',headers={
            'Content-Disposition':f'attachment; filename="river-project-{project_id}.wqproj"'})
