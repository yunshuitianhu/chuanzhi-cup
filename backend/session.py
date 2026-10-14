"""会话状态管理 —— 替代 Streamlit 的 st.session_state。

为什么需要它
------------
Streamlit 把会话状态挂在服务端的一个 session_state 里；换成 HTTP 后
每个请求是独立的，必须显式携带会话标识。这里用内存字典按 session_id
隔离，语义与原 session_state 一一对应，便于对照移植。

局限（已知，当前可接受）
------------------------
- 进程内存储：多 worker 部署时需换成 Redis 等共享存储
- 无过期淘汰：长时间运行会累积（单人自用场景影响可忽略）
两者都是部署层面的事，已在 README 中标注。
"""

from __future__ import annotations

import threading
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

    # ---- 图表说明（数据驱动生成的文字，供前端展示）----
    captions: dict[str, str] = field(default_factory=dict)

    # ---- 配置 ----
    target_water_class: str = "V"

    # ---- 对话 ----
    messages: list[dict] = field(default_factory=list)
    full_messages: list[Any] = field(default_factory=list)
    analysis_done: bool = False

    # ---- 河道模型 ----
    river_state: Any = None                  # 惰性创建 RiverModelState

    @property
    def analysis_config(self) -> AnalysisConfig:
        cfg = AnalysisConfig()
        cfg.target_water_class = self.target_water_class
        return cfg

    def water_quality_state(self) -> WaterQualityState:
        """构造下游函数需要的 WaterQualityState（它们不认识 Session）。"""
        st = WaterQualityState()
        st.df = self.df
        st.black_spots = self.black_spots
        st.video_frames = self.video_frames
        st.cross_validation_result = self.cross_validation
        return st

    def get_river_state(self):
        if self.river_state is None:
            from river_model.state import RiverModelState
            self.river_state = RiverModelState()
        return self.river_state

    def reset_analysis(self) -> None:
        """清空分析结果但保留数据 —— 切换目标水质等级时用。"""
        self.black_spots = None
        self.black_spots_json = None
        self.cross_validation = None
        self.comprehensive_summary = None
        self.captions = {}
        self.messages = []
        self.full_messages = []
        self.analysis_done = False
        self.river_state = None

    def clear_all(self) -> None:
        """加载新数据时调用 —— 与原 app.py 的「重置所有分析状态」一致。"""
        self.video_frames = []
        self.data_filename = ""
        self.video_filename = ""
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
            if s is None:
                s = Session(id=session_id)
                self._sessions[session_id] = s
            return s

    def drop(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    @property
    def count(self) -> int:
        return len(self._sessions)


STORE = SessionStore()
