"""进程内任务进度与协作停止；不保存线程、事件或锁到项目包。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
import uuid


class AgentStopped(Exception):
    pass


@dataclass
class AgentRun:
    revision: int
    auto: bool = False
    managed: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    status: str = 'running'
    stage: str = '准备分析'
    turn: int = 0
    tool_count: int = 0
    steps: list = field(default_factory=list)
    stop_reason: str = ''
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    finished: threading.Event = field(default_factory=threading.Event, repr=False)
    transport_closed: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    question_context: dict | None = field(default=None, repr=False)

    def request_stop(self, reason='user') -> bool:
        with self.lock:
            if self.ended is not None:
                return False
            self.stop_reason = reason
            self.cancel.set()
            self.status = 'stopping'
            self.stage = '停止请求已收到，等待当前调用收尾'
            return True

    def check(self):
        if self.cancel.is_set():
            raise AgentStopped('已请求停止；已完成的部分分析可保留，本轮不标记完成。')

    def close_transport(self):
        self.transport_closed.set()
        if not self.finished.is_set():
            self.request_stop('disconnect')

    def update(self, stage, turn=None):
        with self.lock:
            self.check()
            self.stage = stage
            if turn is not None:
                self.turn = turn

    def start_tool(self, name):
        with self.lock:
            self.check()
            step_id = uuid.uuid4().hex
            self.tool_count += 1
            self.steps.append(dict(id=step_id, name=name, status='running', started=time.monotonic(), ended=None))
            self.steps = self.steps[-100:]
            self.stage = '执行工具：' + name
            return step_id

    def finish_tool(self, step_id, status):
        with self.lock:
            for step in self.steps:
                if step['id'] == step_id:
                    step.update(status=status, ended=time.monotonic())

    def complete(self, status, commit):
        """完成标记与最后正文一次提交，停止请求不能插入到两者之间。"""
        with self.lock:
            self.check()
            commit()
            self.end(status)

    def end(self, status):
        with self.lock:
            if self.ended is not None:
                return
            self.status = status
            self.stage = {'succeeded':'本轮已完成','partial':'部分完成','failed':'执行失败',
                          'cancelled':'本轮已停止'}.get(status,status)
            self.ended = time.monotonic()
            for step in self.steps:
                if step['status'] == 'running':
                    step.update(status='cancelled' if status=='cancelled' else 'failed', ended=self.ended)

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            return dict(run_id=self.id, analysis_revision=self.revision, auto=self.auto,
                status=self.status, stage=self.stage, turn=self.turn, tool_count=self.tool_count, started_at=self.started_at,
                elapsed_s=round((self.ended or now)-self.started,3),
                busy=not self.finished.is_set() if self.managed else self.ended is None,
                stop_requested=self.cancel.is_set(),
                steps=[dict(id=s['id'],name=s['name'],status=s['status'],
                            elapsed_s=round((s['ended'] or now)-s['started'],3)) for s in self.steps],
                notice='停止为协作请求；等待中的网络或计算调用需先收尾，期间禁止替换数据或启动新任务。')
