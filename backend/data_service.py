"""上传数据与内置示例共用的解析/应用服务。

不在路由函数之间互相调用带 Depends 的处理器；文件读取和解析由调用方在
线程池执行。本模块只负责生产加载器调用和会话数据应用。
"""
from __future__ import annotations

import base64

import pandas as pd

from water_quality_mcp.src.water_quality_mcp.loaders import load_data


def parse_monitoring(raw: bytes, filename: str, loader=None) -> tuple[pd.DataFrame | None, str]:
    """用生产加载器解析候选监测表；不触碰任何会话状态。

    loader 仅供测试替换同一调用点；生产默认使用内核 load_data。
    """
    return (loader or load_data)(base64.b64encode(raw).decode(), filename)


def apply_monitoring(session, df: pd.DataFrame, filename: str) -> None:
    """把已解析候选数据应用到会话；调用方须先完成校验和项目保存。"""
    session.clear_all()
    session.df = df
    session.data_filename = filename
