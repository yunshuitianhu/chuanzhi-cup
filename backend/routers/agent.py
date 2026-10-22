"""Agent 路由 —— SSE 流式对话。"""

from __future__ import annotations

import asyncio
import json
from queue import Queue, Empty, Full
import threading
import time

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse

from ..agent.loop import run_agent
from ..agent.progress import AgentRun
from ..deps import get_session
from ..session import Session

router = APIRouter(prefix="/agent", tags=["agent"])


class AgentStreamResponse(StreamingResponse):
    def __init__(self, *args, run, **kwargs):
        super().__init__(*args,**kwargs)
        self.run=run

    async def __call__(self,scope,receive,send):
        try:
            await super().__call__(scope,receive,send)
        finally:
            self.run.close_transport()


def start_run(session, message, auto):
    if not session.operation_lock.acquire(blocking=False):
        raise HTTPException(409,'当前会话正在处理任务，请等待当前调用收尾后重试')
    run = AgentRun(session.analysis_revision, auto, managed=True)
    session.agent_run = run
    events = Queue(maxsize=256)

    def worker():
        frames = None
        try:
            frames = run_agent(session,message,auto=auto,run=run)
            for frame in frames:
                while True:
                    if run.transport_closed.is_set():
                        return
                    try:
                        events.put(frame,timeout=.2)
                        break
                    except Full:
                        if run.cancel.is_set() or run.transport_closed.is_set():
                            return
        except Exception:
            run.end('failed')
            if session.agent_run is run and session.analysis_revision==run.revision:
                session.analysis_done=False
                session.analysis_status='failed'
            try:
                events.put_nowait('event: error\ndata: '+json.dumps({'run_id':run.id,'status':'failed',
                    'message':'任务初始化或清理失败，请核对当前任务状态。','progress':run.snapshot()},ensure_ascii=False)+'\n\n')
            except Full:
                pass
        finally:
            try:
                if callable(getattr(frames,'close',None)):
                    frames.close()
            finally:
                if run.ended is None:
                    run.end('cancelled')
                    if session.agent_run is run and session.analysis_revision==run.revision:
                        session.analysis_done=False
                        session.analysis_status='cancelled'
                session.operation_lock.release()
                run.finished.set()
    thread=threading.Thread(target=worker,name='water-agent-'+run.id[:8],daemon=True)
    try:
        thread.start()
    except BaseException:
        run.end('failed'); run.finished.set()
        session.operation_lock.release()
        raise
    return run,events


@router.post("/chat")
def chat(payload: dict, request: Request, session: Session = Depends(get_session)):
    """与 Agent 对话，SSE 流式返回。

    Body:
        message: 用户输入
        auto:    是否为「一键自动分析」（使用带 SOP 的 system prompt）
    """
    message = str(payload.get("message", "") or "").strip()
    auto = bool(payload.get("auto", False))
    if not message:
        message = "请开始自动分析。" if auto else "你好"

    if len(message)>20000:
        raise HTTPException(422,'问题过长，请控制在20000字以内')
    run,events=start_run(session,message,auto)

    async def stream():
        heartbeat=time.monotonic()
        try:
            while not run.finished.is_set() or not events.empty():
                if await request.is_disconnected():
                    run.request_stop('disconnect')
                    break
                try:
                    frame=events.get_nowait()
                except Empty:
                    await asyncio.sleep(.1)
                    if time.monotonic()-heartbeat>=5:
                        heartbeat=time.monotonic()
                        yield ': heartbeat\n\n'
                    continue
                yield frame
        finally:
            run.close_transport()

    return AgentStreamResponse(
        stream(),
        run=run,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",     # 关掉 nginx 缓冲，否则流会被攒起来
            "X-Session-Id": session.id,
            'X-Agent-Run-Id': run.id,
        },
    )


@router.get('/run')
def progress(session: Session=Depends(get_session)):
    return session.agent_run.snapshot() if session.agent_run else {'run_id':None,'status':'idle','busy':False,'steps':[]}


@router.post('/runs/{run_id}/stop')
def stop(run_id: str, session: Session=Depends(get_session)):
    run=session.agent_run
    if run is None or run.id!=run_id:
        raise HTTPException(404,'任务不存在或已切换，请刷新当前任务状态')
    requested=run.request_stop()
    return {**run.snapshot(),'stop_accepted':requested}


@router.get('/history')
def history(session: Session = Depends(get_session)) -> dict:
    return {'messages': session.messages, 'analysis_revision': session.analysis_revision}
