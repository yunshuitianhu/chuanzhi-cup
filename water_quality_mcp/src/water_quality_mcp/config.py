"""API 密钥解析与 .env 加载。

密钥的存放约定
--------------
- **绝不写入任何会被提交的文件**（源码、示例文件、文档一律只放占位符）
- 本地密钥放项目根目录的 `.env`，该文件已被 .gitignore 排除
- `.env.example` 是模板（只含占位符），提交进仓库

读取优先级
----------
1. 工具/函数参数显式传入
2. 环境变量（真实环境变量优先于 .env 文件）
3. .env 文件（经 load_env() 载入 os.environ 后，对使用者而言与 2 无异）
"""

import os

# 项目根目录（相对本文件：src/water_quality_mcp/config.py）
_PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
DEFAULT_ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")


def load_env(path: str | None = None, override: bool = False) -> int:
    """把 .env 中的键值对载入 os.environ。

    Args:
        path: .env 路径，默认项目根目录下的 .env。
        override: 是否覆盖已存在的环境变量。默认 False ——
            真实环境变量优先于文件，便于 CI/容器覆盖本地配置。

    Returns:
        实际载入的键数量（文件不存在时返回 0）。

    实现说明：优先用 python-dotenv（若已安装，边界处理更完善），
    否则退回内置轻量解析，避免为此新增硬依赖。
    """
    env_path = path or DEFAULT_ENV_PATH
    if not os.path.isfile(env_path):
        return 0

    try:
        from dotenv import dotenv_values  # type: ignore
        items = dotenv_values(env_path).items()
    except ImportError:
        items = _parse_env_file(env_path)

    loaded = 0
    for key, value in items:
        if not key or value is None:
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded


def _parse_env_file(path: str):
    """轻量 .env 解析（不依赖 python-dotenv）。

    支持: 空行、# 注释、export 前缀、KEY=VALUE、
    值两侧的单/双引号、值内含 = 的情况。
    """
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 去掉成对的引号
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            if key:
                yield key, value


def resolve_api_key(env_var: str, tool_param: str | None = None) -> str | None:
    """解析 API 密钥。

    优先级: 显式传入的参数 > 环境变量（含 load_env() 从 .env 载入的）。

    注意: 空字符串与纯占位符（如 "your-amap-key-here"）视为未配置，
    返回 None —— 否则调用方会拿占位符去请求，得到难以定位的认证错误。
    """
    candidate = tool_param or os.environ.get(env_var) or ""
    candidate = candidate.strip()
    if not candidate or _is_placeholder(candidate):
        return None
    return candidate


def _is_placeholder(value: str) -> bool:
    """判断是否为示例文件里的占位符值。"""
    lowered = value.lower()
    return (
        lowered.startswith("your-")
        or lowered.startswith("<")
        or lowered in ("changeme", "none", "null")
        or "你的" in value
    )
