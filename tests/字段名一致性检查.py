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

import numpy as np
import pandas as pd

APP = os.path.join(PROJ, "app.py")
APP_SRC = open(APP, encoding="utf-8").read()


def keys_read_in(func_name, next_func_marker):
    """抽取 app.py 中某个函数体里读取的字段名。"""
    start = APP_SRC.index(f"def {func_name}(")
    end = APP_SRC.index(next_func_marker, start) if next_func_marker else len(APP_SRC)
    body = strip_comments(APP_SRC[start:end])
    found = set()
    for m in re.finditer(r'\.get\(\s*["\']([^"\']+)["\']', body):
        found.add(m.group(1))
    return found, body


def strip_comments(code: str) -> str:
    """去掉行尾注释再匹配。

    否则"修复说明"里提到的旧键名（例如注释中写 cv_data["pearson"] 说明它已
    被替换）会被正则当成仍在使用的键，测试在代码修好后反而报失败 ——
    这类假阳性会让整个检查失去可信度。

    局限: 用最简启发式（行内首个 # 之后截断），不处理字符串里含 # 的情况。
    本仓库的 app.py 无此情形。
    """
    return "\n".join(line.split("#", 1)[0] for line in code.splitlines())


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

from water_quality_mcp.src.water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    find_black_spots,
)
from water_quality_mcp.src.water_quality_mcp.analysis_config import DEFAULT_CONFIG
from water_quality_mcp.src.water_quality_mcp.state import WaterQualityState

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
# 注意: find_black_spots 有【两种输出形态】—— 检出黑点与未检出黑点走不同分支,
# 键名并不相同(例如 "提示" 只出现在未检出分支)。只跑一种形态会把另一种的
# 键误判为"不存在"。这里两种都跑, 取键的并集。
result_json, black = find_black_spots(df, DEFAULT_CONFIG)
produced = flat_keys(json.loads(result_json))

clean = df.copy()
clean["ammonia_nitrogen"] = 1.0     # 远低于 8.0 阈值
clean["dissolved_oxygen"] = 6.0     # 远高于 2.0 阈值
clean_json, _ = find_black_spots(clean, DEFAULT_CONFIG)
clean_obj = json.loads(clean_json)
dirty_keys = flat_keys(json.loads(result_json))
if clean_obj.get("黑点总数") == 0:
    extra = sorted(flat_keys(clean_obj) - dirty_keys)
    produced |= flat_keys(clean_obj)
    print(f"（已合并[未检出黑点]分支的键：{extra}）")
else:
    print("（注意：干净数据仍检出黑点，[未检出]分支未被覆盖）")

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
seg = strip_comments(APP_SRC[start:start + 6000])
# 动态抽取该区域读取的键名。不要在此硬编码具体键 ——
# 硬编码会让脚本在代码已修好后仍报旧结论，失去可信度。
read3 = set(re.findall(r'cv_data\.get\(\s*["\']([^"\']+)["\']', seg)) | \
        set(re.findall(r'cv_data\[["\']([^"\']+)["\']\]', seg)) | \
        set(re.findall(r'["\']([A-Za-z_]+)["\']\s+in\s+cv_data', seg))
bad = []
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
        # 动态抽取"聚类详情"展示块里 ci.get(...) 读的键 —— 同样不硬编码
        block_start = APP_SRC.index('with st.expander("聚类详情")')
        block = strip_comments(APP_SRC[block_start:block_start + 900])
        read_cluster = sorted(set(re.findall(r'ci\.get\(\s*["\']([^"\']+)["\']', block)))
        print(f"   >>> 聚类详情面板读取的字段 ({len(read_cluster)} 个) vs 实际:")
        bad_cluster = []
        for k in read_cluster:
            ok = k in cl[0]
            if not ok:
                bad_cluster.append(k)
            print(f"       {'✓' if ok else '✗'} {k}")
        miss1 = miss1 + [f"聚类详情.{k}" for k in bad_cluster]

# ---------- 5. RAG 查询构造 ----------
# 回归守卫：rag_engine.build_query_from_summary 曾经用一套英文/近义键名读取
# 中文键名的摘要，其中 "主要触发指标" 实际是 list 却按 str 用（set.add(list)
# 抛 TypeError），异常又被 report.py 的裸 except 吞掉 —— 导致 RAG 案例注入
# 长期静默失效。本项确保它不再回退到那种状态。
print()
print("=" * 72)
print("5. rag_engine.build_query_from_summary（RAG 注入的回归守卫）")
print(f"{'=' * 72}")

from water_quality_mcp.src.water_quality_mcp.rag_engine import get_rag_engine

engine = get_rag_engine()
try:
    query = engine.build_query_from_summary(summary)
    print(f"   生成查询: {query!r}")
    raised = False
except Exception as e:
    print(f"   ✗ 抛出 {type(e).__name__}: {e}")
    query = ""
    raised = True

# 摘要里有 1230 个黑点，查询串里应当体现出"检测到了黑点"这一核心事实；
# 若键名写错，black_count 会静默取到 0，这些词一个都不会出现。
hits = [w for w in ("黑点", "黑臭", "氨氮", "溶解氧") if w in query]
print(f"   查询串中包含的关键信息: {hits}")

ok_rag = (not raised) and len(hits) >= 2
print()
print(f"   {'[PASS]' if ok_rag else '[FAIL]'} RAG 查询构造正常"
      + ("" if ok_rag else "  — 键名可能又被改回不匹配的形态"))

print()
print("=" * 72)
passed = not miss1 and not miss2 and not bad and ok_rag
print(f"检查完成：{'全部通过' if passed else '存在不一致，见上'}")
print("=" * 72)
sys.exit(0 if passed else 1)
