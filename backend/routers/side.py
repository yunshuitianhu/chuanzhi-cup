"""Quoted-text explanation streams, isolated from the main agent history."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import threading
from queue import Queue, Empty, Full
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..deps import get_session, get_exclusive_session
from ..agent.progress import AgentRun
from ..question_context import resolve_context, scope
from .. import side_chat as core
from .agent import AgentStreamResponse

router = APIRouter(prefix='/side', tags=['side'])


class Create(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    context_id: str = Field(pattern=r'^[0-9a-f]{32}$')


class Ask(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    message: str = Field(min_length=1, max_length=10000)
    request_id: str = Field(pattern=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def run_status(session):
    run = getattr(session, '_side_run', None)
    return {**(run.snapshot() if run else {'run_id': None, 'status': 'idle', 'busy': False}),
        'thread_id': getattr(session, '_side_thread_id', None),
        'save_notice': getattr(session, '_side_save_notice', '')}


@router.get('/run')
def status(session=Depends(get_session)):
    return run_status(session)


@router.get('/threads')
def threads(session=Depends(get_session)):
    current = scope(session)
    return {'threads': [{**deepcopy(t), 'historical': t['quote'].get('scope') != current}
        for t in session.side_threads], 'run': run_status(session)}


@router.post('/threads')
def create(payload: Create, session=Depends(get_exclusive_session)):
    entry = resolve_context(session, payload.context_id)
    if entry['public']['kind'] != 'text_quote':
        raise HTTPException(422, '侧边问答需要先选择文字引用。')
    # Reopening the same selected passage does not create duplicate threads.
    found = next((t for t in session.side_threads if t['quote']['scope'] == entry['public']['scope']
        and t['quote']['selection'] == entry['public']['selection']), None)
    if found:
        return deepcopy(found)
    if len(session.side_threads) >= core.MAX_THREADS:
        raise HTTPException(409, '本项目已达20个解释线程，请使用已有线程；新一轮调查可新建项目。')
    thread = {'id': uuid4().hex, 'quote': entry['public'], 'messages': [], 'requests': [],
        'updated_at': datetime.now(timezone.utc).isoformat()}
    session.side_threads.append(thread)
    return deepcopy(thread)


@router.post('/runs/{run_id}/stop')
def stop(run_id: str, session=Depends(get_session)):
    run = getattr(session, '_side_run', None)
    if run is None or run.id != run_id:
        raise HTTPException(404, '该侧边任务不存在，请刷新状态。')
    run.request_stop()
    return run_status(session)


@router.post('/threads/{thread_id}/chat')
def chat(thread_id: str, payload: Ask, request: Request, session=Depends(get_session)):
    if not payload.message.strip():
        raise HTTPException(422, '请输入问题。')
    if not session.operation_lock.acquire(blocking=False):
        raise HTTPException(409, '当前项目任务尚未收尾，请稍后发送；引用和草稿可以保留。',headers={'X-Session-Conflict':'busy'})
    try:
        thread = core.find_thread(session, thread_id)
        core.check_quote(session, thread['quote'])
        if payload.request_id in thread['requests']:
            raise HTTPException(409, '该请求已接收，请核对已有回答；显式重试会创建新请求。')
        if len(thread['messages']) > core.MAX_MESSAGES - 3:
            raise HTTPException(409, '本解释线程已达消息上限，请选择新的文字开启线程。')
        run = AgentRun(session.analysis_revision, managed=True)
    except BaseException:
        session.operation_lock.release()
        raise
    session._side_run = run
    session._side_thread_id = thread_id
    session._side_save_notice = ''
    events = Queue(maxsize=256)

    def worker():
        frames = None
        try:
            frames = core.explain(session, thread, payload.message, payload.request_id, run)
            for frame in frames:
                while not run.transport_closed.is_set():
                    try:
                        events.put(frame, timeout=.2)
                        break
                    except Full:
                        continue
                if run.transport_closed.is_set():
                    break
        except Exception:
            run.end('failed')
            from ..agent.loop import _sse
            try:
                events.put_nowait(_sse('error', {'run_id':run.id,'thread_id':thread_id,
                    'status':'failed','message':'解释初始化或执行失败，请核对当前任务状态；问题草稿仍可保留。',
                    'progress':run.snapshot()}))
            except Full:
                pass
        finally:
            try:
                if frames is not None and callable(getattr(frames,'close',None)):
                    try:
                        frames.close()
                    except Exception:
                        if run.ended is None:
                            run.end('failed')
                if run.ended is None:
                    run.end('cancelled')
                recorded=any(message.get('run_id')==run.id for message in thread['messages'])
                if not recorded:
                    session._side_save_notice='本轮没有新增问答记录；问题草稿未写入项目。'
                elif session.project_id:
                    try:
                        from ..projects import repository
                        repository().write(session)
                        session._side_save_notice = '本轮侧边问答已随项目保存。'
                    except Exception:
                        session._side_save_notice = '本轮仍在当前会话中，但项目保存失败；请手动保存或导出。'
                else:
                    session._side_save_notice = '临时会话尚未保存，请保存项目以便重启恢复。'
            finally:
                session.operation_lock.release()
                run.finished.set()

    thread_worker = threading.Thread(target=worker, name='quote-' + run.id[:8], daemon=True)
    try:
        thread_worker.start()
    except BaseException:
        run.end('failed'); run.finished.set(); session.operation_lock.release()
        raise

    async def stream():
        while not run.finished.is_set() or not events.empty():
            if await request.is_disconnected():
                run.request_stop('disconnect')
                break
            try:
                yield events.get_nowait()
            except Empty:
                await asyncio.sleep(.1)
                yield ': heartbeat\n\n'

    return AgentStreamResponse(stream(), run=run, media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Session-Id': session.id, 'X-Agent-Run-Id': run.id})
