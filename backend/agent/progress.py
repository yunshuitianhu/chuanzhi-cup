"""进程内任务进度与协作停止；不保存线程、事件或锁到项目包。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import time
import uuid


def tool_notice(name, status, outcome, *, references=None):
    """进度只显示固定原因分类；不回显工具原文、路径、凭据或模型推理。"""
    data = outcome.get('data') if isinstance(outcome, dict) else None
    error = str(outcome.get('error') or '') if isinstance(outcome, dict) else ''
    if status == 'skipped':
        if name in ('analyze_video', 'cross_validate_video'):
            return '未上传视频，此步骤不适用；监测表分析可继续。'
        if name in ('search_nearby_pollution_sources', 'get_national_station_data'):
            return '仿真数据不关联真实企业和国控站点；地名仅供地图参考。'
        return '当前输入不满足该可选步骤的条件，已跳过。'
    if status == 'failed':
        if isinstance(outcome, dict) and outcome.get('error_code') == 'report_prerequisites_missing':
            labels = {'get_data_summary':'监测摘要', 'find_black_spots':'异常点筛查',
                      'get_comprehensive_summary':'综合摘要', 'analyze_video':'视频线索分析',
                      'cross_validate_video':'视频与监测记录核对'}
            missing = outcome.get('missing_steps')
            names = [labels[key] for key in missing if key in labels] if isinstance(missing, list) else []
            return '生成报告尚需完成：' + ('、'.join(names) if names else '本轮必要分析步骤') + '。'
        if any(key in error.lower() for key in ('chromadb', 'sentence_transformers', '依赖', '索引', '知识库')):
            return '知识库或运行依赖不可用，请在技术展示页核对检索状态。'
        if any(key in error.lower() for key in ('timeout', 'timed out', 'connect', '网络', '超时')):
            return '外部请求超时或连接失败，可检查网络后重试。'
        if any(key in error.lower() for key in ('api key', '401', '403', '未配置', '鉴权')):
            return '外部服务配置或访问权限不可用，请核对本机服务配置。'
        if any(key in error for key in ('坐标', '聚类', '黑点')):
            return '缺少可用定位依据；可在技术展示页指定坐标查询，不自动猜测污染源。'
        if any(key in error for key in ('先运行', '先完成', '未初始化', '未加载', '自动检测需要', '参数', '必填', '缺少', '类型')):
            return '输入、参数或前置步骤不满足要求；请核对数据和相应页面的配置。'
        return '该步骤未完成；已完成的结果保留，可核对输入或服务状态后重试。'
    if name == 'generate_final_report' and isinstance(references, dict) and not references.get('检索结果'):
        return '报告已生成，但本次未取得可用治理案例，不补造引用。'
    if name == 'get_national_station_data' and isinstance(data, dict) and data.get('含实时监测值') is False:
        return '未取得实时监测值；当前返回站点目录或背景资料，不能作为同期水质证据。'
    return ''


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
    _operations: dict = field(default_factory=dict, repr=False)
    _failures: dict = field(default_factory=dict, repr=False)
    _report_result: dict | None = field(default=None, repr=False)
    _report_attempts: int = 0

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

    def start_tool(self, name, *, operation_key=None, results_revision=None):
        with self.lock:
            self.check()
            step_id = uuid.uuid4().hex
            step = dict(id=step_id, name=name, status='running', started=time.monotonic(), ended=None)
            if operation_key is not None:
                # Keys may contain query parameters; only opaque IDs leave this object.
                operation = self._operations.setdefault(operation_key, uuid.uuid4().hex)
                step['operation_id'] = operation
            self.tool_count += 1
            self.steps.append(step)
            self.steps = self.steps[-100:]
            if name == 'generate_final_report':
                self._report_attempts += 1
                self._report_result = dict(step_id=step_id, status='running', attempts=self._report_attempts,
                                          analysis_revision=self.revision, results_revision=results_revision)
            self.stage = '执行工具：' + name
            return step_id

    def finish_tool(self, step_id, status, notice=''):
        with self.lock:
            step = next((s for s in self.steps if s['id'] == step_id), None)
            if step is None or step['status'] != 'running':
                return
            step.update(status=status, ended=time.monotonic(), notice=notice[:160])
            if status == 'failed':
                self._failures[step_id] = step
            elif status == 'succeeded' and step.get('operation_id'):
                matching = [s for s in self._failures.values()
                            if s.get('operation_id') == step['operation_id']]
                if matching:
                    step['retry_of'] = matching[-1]['id']
                for failed in matching:
                    failed['recovered_by'] = step_id
                    self._failures.pop(failed['id'])
            if self._report_result and self._report_result['step_id'] == step_id:
                self._report_result['status'] = status

    def has_unresolved_failures(self, exclude=()):
        with self.lock:
            return any(s['name'] not in exclude for s in self._failures.values())

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
                    self.finish_tool(step['id'], 'cancelled' if status=='cancelled' else 'failed')

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            return dict(run_id=self.id, analysis_revision=self.revision, auto=self.auto,
                status=self.status, stage=self.stage, turn=self.turn, tool_count=self.tool_count, started_at=self.started_at,
                elapsed_s=round((self.ended or now)-self.started,3),
                busy=not self.finished.is_set() if self.managed else self.ended is None,
                stop_requested=self.cancel.is_set(),
                steps=[dict(id=s['id'],name=s['name'],status=s['status'],
                            elapsed_s=round((s['ended'] or now)-s['started'],3),
                            **({'notice':s['notice']} if s.get('notice') else {}),
                            **{key:s[key] for key in ('operation_id','retry_of','recovered_by') if key in s}) for s in self.steps],
                **({'report_result':dict(self._report_result)} if self._report_result else {}),
                notice='停止为协作请求；等待中的网络或计算调用需先收尾，期间禁止替换数据或启动新任务。')
