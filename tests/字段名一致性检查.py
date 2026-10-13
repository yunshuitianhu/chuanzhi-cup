# -*- coding: utf-8 -*-
"""界面字段名 vs 后端产出字段名 —— 一致性检查

背景
----
app.py 的展示函数用 data.get("某字段") 从后端 JSON 里取数。若取的字段名
与后端实际产出的不一致，.get() 会静默返回默认值，界面上就是空白或 0，
不会报错 —— 这类缺陷只能靠逐字段比对发现。

做法
----
不猜测：实际调用后端函数拿到真实 JSON，再把 app.py 源码里所有
.get("...") / ["..."] 的字段名抽出来，做集合差。
"""
import base64
import io
import json
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)
sys.path.insert(0, os.path.join(PROJ, "water_quality_mcp", "src"))

import numpy as np
import pandas as pd

APP = os.path.join(PROJ, "app.py")
APP_SRC = open(APP, encoding="utf-8").read()


def keys_read_in(func_name, next_func_marker):
    """抽取 app.py 中某个函数体里读取的字段名。"""
    start = APP_SRC.index(f"def {func_name}(")
    end = APP_SRC.index(next_func_marker, start) if next_func_marker else len(APP_SRC)
    body = APP_SRC[start:end]
    found = set()
    for m in re.finditer(r'\.get\(\s*["\']([^"\']+)["\']', body):
        found.add(m.group(1))
    return found, body


def flat_keys(obj, prefix=""):
    """递归收集 JSON 中出现的全部键名。"""
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            out |= flat_keys(v, prefix)
    elif isinstance(obj, list):
        for it in obj[:3]:
            out |= flat_keys(it, prefix)
    return out


def compare(title, produced, read_names, ignore=None):
    ignore = ignore or set()
    missing = sorted(n for n in read_names if n not in produced and n not in ignore)
    print(f"\n{'=' * 72}")
    print(title)
    print(f"{'=' * 72}")
    print(f"后端实际产出的键 ({len(produced)}):")
    print("   " + ", ".join(sorted(produced)[:30]))
    print(f"\n界面读取的字段名 ({len(read_names)}):")
    print("   " + ", ".join(sorted(read_names)))
    if missing:
        print(f"\n>>> 界面读取但后端【不存在】的字段 ({len(missing)}):")
        for n in missing:
            print(f"      ✗ {n}")
    else:
        print("\n>>> 全部匹配")
    return missing


# ============================================================
print("#" * 72)
print("# 界面字段名 vs 后端产出字段名 一致性检查")
print("#" * 72)

from water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    find_black_spots,
)
from water_quality_mcp.analysis_config import DEFAULT_CONFIG
from water_quality_mcp.state import WaterQualityState

# ---------- 造一份能触发聚类与触发指标的数据 ----------
rng = np.random.default_rng(7)
n = 60
t0 = pd.Timestamp("2024-03-15 08:00:00")
df = pd.DataFrame({
    "datetime": [t0 + pd.Timedelta(seconds=10 * i) for i in range(n)],
    "longitude": 113.1412 + np.arange(n) * 0.00002,
    "latitude": 23.1123 + np.arange(n) * 0.00002,
    "ammonia_nitrogen": rng.uniform(9.0, 18.0, n),
    "dissolved_oxygen": rng.uniform(0.1, 1.5, n),
    "cod": rng.uniform(50, 90, n),
    "turbidity": rng.uniform(100, 200, n),
    "ph": rng.uniform(7.0, 7.8, n),
    "water_temperature": rng.uniform(20, 28, n),
})

# ---------- 1. 黑臭检测 ----------
result_json, black = find_black_spots(df, DEFAULT_CONFIG)
produced = flat_keys(json.loads(result_json))
read_names, _ = keys_read_in("_display_black_spot_result", "def _display_trace_result")
miss1 = compare("1. find_black_spots  →  _display_black_spot_result（tab4 第2区）",
                produced, read_names)

# ---------- 2. 交叉印证 ----------
# 造视频帧
import cv2
frames = []
for i in range(12):
    img = np.full((120, 160, 3), 60 + i * 5, dtype=np.uint8)
    cv2.circle(img, (80, 60), 30, (40, 90, 110), -1)
    _, buf = cv2.imencode(".jpg", img)
    frames.append({"time_sec": i * 30.0, "image_base64": base64.b64encode(buf).decode()})

# 数据时间密度要够，保证窗口内能匹配到记录
t1 = pd.Timestamp("2024-03-15 08:00:00")
dense = pd.DataFrame({
    "datetime": [t1 + pd.Timedelta(seconds=10 * i) for i in range(400)],
    "longitude": 113.1412 + np.arange(400) * 0.00001,
    "latitude": 23.1123 + np.arange(400) * 0.00001,
    "ammonia_nitrogen": rng.uniform(2.0, 14.0, 400),
    "dissolved_oxygen": rng.uniform(0.5, 6.0, 400),
    "cod": rng.uniform(20, 90, 400),
    "turbidity": rng.uniform(30, 200, 400),
    "ph": rng.uniform(6.8, 7.6, 400),
    "water_temperature": rng.uniform(20, 28, 400),
})
st_ws = WaterQualityState()
st_ws.df = dense
st_ws.video_frames = frames
st_ws.black_spots = black
cv_json = cross_validate_video_with_monitoring(st_ws, 0, 60)
cv_obj = json.loads(cv_json)
if "error" in cv_obj:
    print(f"\n（交叉印证未能构造有效样例: {cv_obj['error']}，跳过第 2 项）")
    miss2 = []
else:
    produced2 = flat_keys(cv_obj)
    read2, body2 = keys_read_in("_build_cross_validation_caption", "def _build_correlation_heatmap_caption")
    miss2 = compare("2. cross_validate_video  →  _build_cross_validation_caption",
                    produced2, read2)

# ---------- 3. tab4 交叉印证区的读取 ----------
start = APP_SRC.index("第 4 区：视频-传感器交叉印证")
seg = APP_SRC[start:start + 6000]
read3 = set(re.findall(r'cv_data\.get\(\s*["\']([^"\']+)["\']', seg)) | \
        set(re.findall(r'\[["\'](pearson)["\']\]', seg)) | \
        set(re.findall(r'if\s+["\']([a-z_]+)["\']\s+in\s+cv_data', seg))
if miss2 or read3:
    produced3 = flat_keys(cv_obj) if "error" not in cv_obj else set()
    print(f"\n{'=' * 72}")
    print("3. tab4 交叉印证区读取的字段")
    print(f"{'=' * 72}")
    print(f"   读取: {sorted(read3)}")
    if produced3:
        bad = sorted(x for x in read3 if x not in produced3)
        print(f"   后端产出的键: {sorted(produced3)}")
        print(f"\n>>> 不存在于后端的: {bad if bad else '无'}")

# ---------- 4. 综合摘要 ----------
ws2 = WaterQualityState()
ws2.df = dense
ws2.black_spots = black
summary = build_comprehensive_summary(ws2, None, DEFAULT_CONFIG)
prod4 = flat_keys(json.loads(summary))
print(f"\n{'=' * 72}")
print("4. build_comprehensive_summary 的顶层结构")
print(f"{'=' * 72}")
s = json.loads(summary)
print("   顶层键:", list(s.keys()))
if "黑臭检测" in s:
    print("   黑臭检测的键:", list(s["黑臭检测"].keys()))
    cl = s["黑臭检测"].get("聚类详情", [])
    if cl:
        print("   聚类详情[0] 的键:", list(cl[0].keys()))
        print()
        print("   >>> app.py L1637-1643 读取的聚类字段 vs 实际:")
        for k in ["聚类ID", "严重程度", "黑臭等级", "趋势", "点位数量", "空间跨度_m", "持续超标时长_min"]:
            ok = k in cl[0]
            print(f"       {'✓' if ok else '✗'} {k}")

print()
print("=" * 72)
print("检查完成")
print("=" * 72)
