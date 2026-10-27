"""会话状态管理 —— 替代 Streamlit 的 st.session_state。

为什么需要它
------------
Streamlit 把会话状态挂在服务端的一个 session_state 里；换成 HTTP 后
每个请求是独立的，必须显式携带会话标识。这里用内存字典按 session_id
隔离，语义与原 session_state 一一对应，便于对照移植。

临时会话保留在进程内；已保存项目通过SQLite索引与版本快照恢复。
运行中任务仍只支持单worker，磁盘项目保存使用版本冲突检查。
"""

from __future__ import annotations

import threading
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState


@dataclass
class Session:
    """单个会话的全部状态。字段与原 app.py 的 session_state 对应。"""

    id: str
    project_id: str = ''
    project_revision: int = 0
    restore_error: str = ''

    # ---- 数据 ----
    df: pd.DataFrame | None = None
    video_frames: list[dict] = field(default_factory=list)
    # 已加载的文件名。浏览器出于安全不允许回填 <input type=file>，
    # 刷新后选择框必然为空；若不单独记住，界面会显示成「尚未上传」
    # 而状态区却显示「1805 条」，自相矛盾。
    data_filename: str = ""
    video_filename: str = ""

    # ---- 分析结果 ----
    black_spots: pd.DataFrame | None = None
    black_spots_json: str | None = None      # find_black_spots 的原始 JSON
    cross_validation: dict | None = None
    comprehensive_summary: str | None = None
    national_station_result: dict | None = None

    # ---- 图表说明（数据驱动生成的文字，供前端展示）----
    captions: dict[str, str] = field(default_factory=dict)
    tool_evidence: dict[str, str] = field(default_factory=dict)
    investigations: dict = field(default_factory=lambda: {'version':1,'revision':0,'items':[]})

    # ---- 配置 ----
    target_water_class: str = "V"

    # ---- 对话 ----
    messages: list[dict] = field(default_factory=list)
    side_threads: list[dict] = field(default_factory=list)
    full_messages: list[Any] = field(default_factory=list)
    analysis_done: bool = False
    analysis_status: str = 'idle'
    analysis_steps: dict[str, str] = field(default_factory=dict)
    analysis_revision: int = 0
    # 进程内派生结果缓存标记；不改变监测数据/物理模型版本，不属于项目文件格式。
    results_revision: int = 0
    operation_lock: Any = field(default_factory=threading.Lock, repr=False)
    agent_run: Any = field(default=None, repr=False)  # 仅进程内状态，不进入项目包

    # ---- 河道模型 ----
    river_state: Any = None                  # 惰性创建 RiverModelState
    river_ui: dict = field(default_factory=dict)

    # ---- 图片附件（元数据随项目保存；字节见 _media_bytes）----
    media_assets: dict = field(default_factory=dict)
    _media_bytes: dict = field(default_factory=dict, repr=False)

    @property
    def analysis_config(self) -> AnalysisConfig:
        cfg = AnalysisConfig()
        cfg.target_water_class = self.target_water_class
        if self.df is not None and 'quality_control_config' in self.df.attrs:
            from water_quality_mcp.src.water_quality_mcp.quality_control import QualityConfig
            cfg.quality_control = QualityConfig(**self.df.attrs['quality_control_config'])
        return cfg

    @property
    def data_identity(self) -> dict:
        from water_quality_mcp.src.water_quality_mcp.evidence import data_identity
        return data_identity(self.df, self.data_filename)

    def water_quality_state(self) -> WaterQualityState:
        """构造下游函数需要的 WaterQualityState（它们不认识 Session）。"""
        st = WaterQualityState()
        st.df = self.df
        st.data_filename = self.data_filename
        st.black_spots = self.black_spots
        st.video_frames = self.video_frames
        st.cross_validation_result = self.cross_validation
        st.national_station_result = self.national_station_result
        from .cruise_alignment import alignment_result
        try:
            st.model_alignment = alignment_result(self)['summary']
        except ValueError as exc:
            st.model_alignment = {'status':'unavailable', 'notice':str(exc)}
        return st

    def get_river_state(self):
        if self.river_state is None:
            from river_model.state import RiverModelState
            self.river_state = RiverModelState()
        if not self.river_state.output_dir:
            from .projects import runtime_directory
            self.river_state.output_dir = runtime_directory()
        return self.river_state

    def reset_analysis(self) -> None:
        """清空分析结果但保留数据 —— 切换目标水质等级时用。"""
        self.analysis_revision += 1
        self.agent_run = None
        self.results_revision += 1
        self.black_spots = None
        self.black_spots_json = None
        self.cross_validation = None
        self.comprehensive_summary = None
        self.national_station_result = None
        self.captions = {}
        self.tool_evidence = {}
        self.messages = []
        self.full_messages = []
        self.analysis_done = False
        self.analysis_status = 'idle'
        self.analysis_steps = {}
        self.river_state = None
        self.river_ui = {}

    def invalidate_report(self) -> None:
        """保留历史正文，撤销依赖旧结果的摘要与完成标记。"""
        self.comprehensive_summary = None
        self.captions = {}
        self.analysis_steps.pop('get_comprehensive_summary', None)
        self.analysis_steps.pop('generate_final_report', None)
        if self.analysis_status != 'running' and (self.analysis_done or self.analysis_status in ('succeeded','partial','stale')):
            self.analysis_status = 'stale'
        self.analysis_done = False

    def set_detection(self, raw, black) -> None:
        self.black_spots = black
        self.black_spots_json = raw
        self.invalidate_report()
        for name in ('reverse_geocode','search_nearby_pollution_sources','search_treatment_cases'):
            self.tool_evidence.pop(name, None)
            self.analysis_steps.pop(name, None)
        self.results_revision += 1

    def set_cross_validation(self, result) -> None:
        self.cross_validation = result
        self.invalidate_report()
        self.results_revision += 1

    def set_station_result(self, result) -> None:
        """新的站点事实使依赖旧证据的完成标记失效；历史正文保留。"""
        self.national_station_result = result
        self.invalidate_report()
        self.results_revision += 1

    def replace_video(self, frames, filename) -> None:
        self._video_quality_cache = None
        self._vision_image_cache = {}
        self.video_frames = frames
        self.video_filename = filename
        self.cross_validation = None
        self.invalidate_report()
        for name in ('analyze_video','cross_validate_video'):
            self.tool_evidence.pop(name, None)
            self.analysis_steps.pop(name, None)
        self.results_revision += 1

    def clear_media(self) -> None:
        """切换数据或项目时避免旧附件与新数据混用。"""
        self.media_assets = {}
        self._media_bytes = {}
        self._vision_image_cache = {}

    def clear_all(self) -> None:
        """加载新数据时调用 —— 与原 app.py 的「重置所有分析状态」一致。"""
        self.video_frames = []
        self._video_quality_cache = None
        self.data_filename = ""
        self.video_filename = ""
        self.clear_media()
        self.reset_analysis()


class SessionStore:
    """按 session_id 管理 Session 的内存存储。"""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        sid = uuid.uuid4().hex
        with self._lock:
            self._sessions[sid] = Session(id=sid)
            return self._sessions[sid]

    def get(self, session_id: str | None) -> Session:
        """取会话；不存在则创建（前端首次调用可不带 id）。"""
        if not session_id:
            return self.create()
        with self._lock:
            s = self._sessions.get(session_id)
        if s is not None:
            return s
        from .projects import restore_saved_session
        from .project_codec import ProjectError
        try:
            s = restore_saved_session(session_id) or Session(id=session_id)
        except (ProjectError, OSError, sqlite3.Error) as exc:
            s = Session(id=session_id, restore_error=f'已保存项目恢复失败：{exc}')
        with self._lock:
            return self._sessions.setdefault(session_id,s)

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    @property
    def count(self) -> int:
        return len(self._sessions)


STORE = SessionStore()
