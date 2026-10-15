# -*- coding: utf-8 -*-
"""按坐标查询地形起伏（带磁盘缓存与降级）。

为什么需要
----------
`regional_defaults.classify_region` 原先只用经纬度方框判断区域类型，实测
穷举中国全境网格 **99.3% 判为 lowland**，山地/丘陵一次都不返回 ——
山区河道拿到平原坡降，错 20~33 倍。

地形起伏（周边高程的 max−min）是干净得多的判据。实测：

    塔里木河     均值  916 m  起伏    5 m   （高原上的平缓河谷）
    雅瑶水道     均值    7 m  起伏   27 m
    嘉陵江重庆段 均值  291 m  起伏  176 m
    金沙江虎跳峡 均值 3132 m  起伏 2640 m

**判据以起伏为主、高程为辅** —— 塔里木河高程很高但极平，只看高程会误判成山区。

降级设计（**任何一级失败都不阻断参数推导**）
--------------------------------------------
    1. 进程内缓存 → 2. 磁盘缓存 → 3. OpenTopoData 查询 → 4. 返回 None

返回 None 时由 `classify_region` 回落到原有的经纬度启发式。也就是说：
**有网更准，没网退回旧逻辑**，不会因为网络问题让整条参数推导链失败。

数据源
------
OpenTopoData 公共 API，数据集 srtm90m（NASA/USGS SRTM，**公有领域**），无需密钥。
公开实例约 1000 次/天、1 次/秒。每条河一次请求（9 个坐标打包在一次调用里）。

用法:
    from river_model.terrain_lookup import lookup_relief
    mean_m, relief_m, source = lookup_relief(23.11, 113.14)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request

logger = logging.getLogger(__name__)

_API = "https://api.opentopodata.org/v1/srtm90m"

# 采样半径（度），约 10 km。太近只采到河床本身，太远会把远处的山算进来。
_RADIUS_DEG = 0.09

# 缓存网格：0.02° ≈ 2 km。同一河段反复查询可命中缓存。
_GRID = 0.02

# 单次请求超时（秒）。**故意设短** —— 这个函数在参数推导路径上，
# 不能让网络问题拖住整个流程；超时就回落到旧逻辑。
_TIMEOUT = 12

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "external", "terrain", "terrain_cache.json",
)

# 离线开关。设 RIVER_TERRAIN_OFFLINE=1 则完全不联网，只走缓存/回落。
# 用途：测试与离线复现不应依赖网络；也便于对比"开/关地形"两种行为。
_OFFLINE = os.environ.get("RIVER_TERRAIN_OFFLINE", "").strip().lower() \
    in ("1", "true", "yes", "on")

_lock = threading.Lock()
_mem_cache: dict[str, dict] = {}
_disk_loaded = False


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat / _GRID) * _GRID:.3f},{round(lon / _GRID) * _GRID:.3f}"


def _load_disk():
    global _disk_loaded
    if _disk_loaded:
        return
    _disk_loaded = True
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            _mem_cache.update(json.load(f).get("entries", {}))
    except (OSError, ValueError):
        pass


def _save_disk():
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"note": "地形起伏查询缓存，可安全删除（删后自动重新查询）",
                       "grid_deg": _GRID,
                       "entries": _mem_cache}, f, ensure_ascii=False, indent=1)
    except OSError as e:
        logger.debug("地形缓存写入失败（不影响功能）: %s", e)


def _sample_points(lat: float, lon: float):
    """中心点 + 周边 8 点。周边点的偏移随纬度做经度收缩，保持近似等距。"""
    coslat = max(0.2, abs(__import__("math").cos(__import__("math").radians(lat))))
    dlon = _RADIUS_DEG / coslat
    pts = [(lat, lon)]
    for dy, dx in [(1, 0), (-1, 0), (0, 1), (0, -1),
                   (0.8, 0.8), (0.8, -0.8), (-0.8, 0.8), (-0.8, -0.8)]:
        pts.append((round(lat + dy * _RADIUS_DEG, 5), round(lon + dx * dlon, 5)))
    return pts


def _fetch(lat: float, lon: float):
    """向 OpenTopoData 查 9 点高程。失败返回 None。"""
    locs = "|".join(f"{a},{b}" for a, b in _sample_points(lat, lon))
    req = urllib.request.Request(
        f"{_API}?locations={locs}",
        headers={"User-Agent": "water-quality-terrain/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:                      # 网络/解析/超时一律降级
        logger.info("地形高程查询失败（将回落到经纬度启发式）: %s: %s",
                    type(e).__name__, e)
        return None
    if data.get("status") != "OK":
        logger.info("地形高程查询返回异常状态: %s", data.get("status"))
        return None
    els = [x.get("elevation") for x in data.get("results", [])]
    els = [e for e in els if e is not None]
    if len(els) < 4:                            # 有效点太少，不足以算起伏
        return None
    return {
        "mean_m": round(sum(els) / len(els), 1),
        "relief_m": round(max(els) - min(els), 1),
        "n": len(els),
        "ts": time.strftime("%Y-%m-%d"),
    }


def lookup_relief(lat: float, lon: float, allow_network: bool = True):
    """查地形起伏。

    Returns:
        (mean_m, relief_m, source)；失败时 mean/relief 为 None，
        source 说明原因（供日志与 UI 标注）。
    """
    try:
        lat = float(lat)
        lon = float(lon)
    except (TypeError, ValueError):
        return None, None, "坐标无效"

    key = _cache_key(lat, lon)

    with _lock:
        _load_disk()
        hit = _mem_cache.get(key)
    if hit:
        return hit["mean_m"], hit["relief_m"], "terrain_cache"

    if not allow_network or _OFFLINE:
        return None, None, "未联网且无缓存（离线模式）"

    val = _fetch(lat, lon)
    if val is None:
        return None, None, "高程查询失败"

    with _lock:
        _mem_cache[key] = val
        _save_disk()
    return val["mean_m"], val["relief_m"], "terrain_srtm"


def cache_stats() -> dict:
    """缓存概况（供自检/展示）。"""
    with _lock:
        _load_disk()
        return {"entries": len(_mem_cache), "file": _CACHE_FILE,
                "exists": os.path.exists(_CACHE_FILE)}
