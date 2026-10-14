"""FastAPI 后端 —— 把既有的 Python 分析内核包装成 HTTP/SSE 服务。

给谁用
------
`frontend/`（Vue 3）通过它调用水 Quality 分析能力。原 Streamlit 版
（app.py）不经过本层，两者并存：Streamlit 用于快速自检，FastAPI 供新前端。

导入路径
--------
**必须使用 `water_quality_mcp.src.water_quality_mcp.X` 全路径风格**，
不能写成 `water_quality_mcp.X`。原因：本机存在一个指向 `D:\\aiRAG` 的
editable 安装，短路径会被它截获，静默加载到另一份代码。全路径风格要求
项目根在 sys.path 上 —— 下面已处理。
"""

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 触发 .env 载入（包 __init__ 中会调用 load_env）
from water_quality_mcp.src.water_quality_mcp.config import load_env  # noqa: E402

load_env()

PROJECT_ROOT = _PROJECT_ROOT
