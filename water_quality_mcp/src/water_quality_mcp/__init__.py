"""water_quality_mcp —— 水质分析核心包。

导入本包时自动把项目根目录的 `.env` 载入环境变量。

为什么放在这里
--------------
密钥的读取入口是 `config.resolve_api_key()`，它只查 `os.environ`。
若加载 `.env` 需要每个调用方各自记得先调 `load_env()`，迟早有人漏掉 ——
表现是"密钥明明配了却读不到"，且很难定位。

放在包 `__init__` 里可保证：无论从哪个模块进来（app.py / river_model /
MCP server / 测试脚本），只要用到本包，`.env` 就已经生效。

行为说明
--------
- 默认**不覆盖**已存在的真实环境变量（CI / 容器可覆盖本地配置）
- `.env` 不存在时静默跳过，不报错
- `.env` 已被 .gitignore 排除，密钥不进入仓库
"""

from .config import load_env

load_env()

__all__ = ["load_env"]
