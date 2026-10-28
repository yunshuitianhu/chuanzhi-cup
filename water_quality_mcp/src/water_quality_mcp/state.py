from dataclasses import dataclass, field
import sys
import os

import pandas as pd

# 确保 river_model 在 Python 路径中
_river_model_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _river_model_path not in sys.path:
    sys.path.insert(0, _river_model_path)


@dataclass
class WaterQualityState:
    df: pd.DataFrame | None = None
    black_spots: pd.DataFrame | None = None
    video_frames: list[dict] = field(default_factory=list)
    cross_validation_result: dict | None = None
    national_station_result: dict | None = None
    data_filename: str = ''

    def has_data(self) -> bool:
        return self.df is not None and len(self.df) > 0

    def has_black_spots(self) -> bool:
        return self.black_spots is not None and len(self.black_spots) > 0

    def has_video_frames(self) -> bool:
        return len(self.video_frames) > 0

    def replace_data(self, df: pd.DataFrame, filename: str = '') -> None:
        """换一份监测数据后，旧数据派生的结果全部失效。

        解析失败时调用方不调用本方法，保留旧状态；成功时统一在这里清理，
        避免各入口各自维护一份不一致的失效清单。
        """
        self.df = df
        self.data_filename = filename
        self.black_spots = None
        self.video_frames = []
        self.cross_validation_result = None
        self.national_station_result = None

    def replace_video(self, frames: list[dict]) -> None:
        """视频替换只失效依赖旧视频的交叉印证，监测数据与黑点保持不变。"""
        self.video_frames = list(frames or [])
        self.cross_validation_result = None


# 在模块级别导入 RiverModelState (延迟以避免循环)
_RIVER_STATE = None


def _get_river_state():
    """获取或创建 RiverModelState 单例。"""
    global _RIVER_STATE
    if _RIVER_STATE is None:
        from river_model.state import RiverModelState
        _RIVER_STATE = RiverModelState()
    return _RIVER_STATE


def reset_river_state():
    """监测数据被替换后丢弃旧河道模型，避免旧几何/结果与新数据混用。"""
    global _RIVER_STATE
    _RIVER_STATE = None
