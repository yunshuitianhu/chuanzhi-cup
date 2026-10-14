# -*- coding: utf-8 -*-
"""前端字段名检查 —— Vue 组件读取的键 vs 后端实际产出的键

为什么需要这个
--------------
项目里反复出现同一类缺陷：**调用层按一套键名读取，后端产出的是另一套**
（英文 snake_case vs 中文键名）。语言层不报错 —— `dict.get()` 与 JS 的
可选链取不到时都静默返回兜底值，界面表现为空白，接口却返回 200。

`tests/字段名一致性检查.py` 只覆盖 `app.py`。新前端写完后没有对应的检查，
于是在 CrossValidation.vue 里又犯了一次：读了 alignment / agreement /
correlations / findings 四个英文键，后端一个都没有 —— 四个指标卡全是
空白横杠，而接口数据完全正常。

设计要点（踩过的坑）
--------------------
比较时**必须取该组件依赖的全部接口响应的键之并集**。只拿一个接口去比会
大量误报 —— 例如 RiverView 同时用 params / init / hydro / pollution 四个
接口，用其中一个比会把其余十几个键全判成"不存在"。检查工具的假阳性会
直接毁掉它的可信度。

同理，只在条件分支里出现的键（如空结果分支的「状态/建议」）以及在组件里
有 `||` 英文兜底的键，需要显式列入 ignore 并注明原因。

用法：
    python tests/前端字段名检查.py
"""
import json
import logging
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

from fastapi.testclient import TestClient

from backend.main import app

SAMPLE = (sys.argv[1] if len(sys.argv) > 1 else
          r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def flat_keys(obj, out=None):
    """递归收集 JSON 中出现的全部键名。"""
    if out is None:
        out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            flat_keys(v, out)
    elif isinstance(obj, list):
        for it in obj[:3]:
            flat_keys(it, out)
    return out


def keys_read_in(rel_path):
    """抽取源码里以下标方式读取的字符串键。

    只取 `xxx['键名']` / `xxx?.['键名']` / `.get('键名')` 这些形态；
    属性访问与 CSS 类名不会被误收。注释先剥掉 —— 否则"修复说明里提到的
    旧键名"会被当成仍在使用的键（这个坑在 app.py 的检查器里踩过一次）。
    """
    src = open(rel_path, encoding="utf-8").read()
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = "\n".join(line.split("//")[0] for line in src.splitlines())

    found = set()
    for m in re.finditer(r"""\[\s*['"]([^'"]+)['"]\s*\]""", src):
        found.add(m.group(1))
    for m in re.finditer(r"""\.get\(\s*['"]([^'"]+)['"]""", src):
        found.add(m.group(1))
    return found


def compare(title, component, produced, ignore=None):
    read = keys_read_in(component)
    ignore = set(ignore or [])
    missing = sorted(k for k in read if k not in produced and k not in ignore)
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print(f"  组件: {component}")
    print(f"  后端产出键 {len(produced)} 个 | 组件读取键 {len(read)} 个"
          f"{f' | 豁免 {len(ignore)} 个' if ignore else ''}")
    if missing:
        print()
        print(f"  >>> 组件读取但后端【不存在】的键 ({len(missing)}):")
        for k in missing:
            print(f"        x {k}")
    else:
        print("  >>> 全部匹配")
    return missing


c = TestClient(app)
H = {"X-Session-Id": c.get("/api/health").headers["X-Session-Id"]}

print("=" * 72)
print("前端字段名检查")
print("=" * 72)

data_ready = False
if os.path.isfile(SAMPLE):
    with open(SAMPLE, "rb") as f:
        r = c.post("/api/data/upload", headers=H,
                   files={"file": (os.path.basename(SAMPLE), f.read(),
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    data_ready = r.status_code == 200
print(f"\n会话数据: {'已加载' if data_ready else '未加载（部分检查将跳过）'}")

misses = []
scenarios = []

# ---- 1. 黑臭检测 ----
r = c.post("/api/analysis/black-spots", headers=H)
scenarios.append((
    "1. /api/analysis/black-spots -> BlackSpotResult.vue",
    "frontend/src/components/BlackSpotResult.vue",
    [r.json()] if r.status_code == 200 else [],
    (),
))

# ---- 2. 交叉印证（需视频帧；没有就跳过，不伪造数据）----
r = c.post("/api/analysis/cross-validate", headers=H, json={})
if r.status_code == 200:
    scenarios.append((
        "2. /api/analysis/cross-validate -> CrossValidation.vue",
        "frontend/src/components/CrossValidation.vue",
        [r.json()],
        (),
    ))
else:
    print(f"\n[跳过] cross-validate：HTTP {r.status_code}（本机无视频帧，需在有视频的会话里跑）")

# ---- 3. 溯源地图 ----
r = c.get("/api/charts/trace-map", headers=H)
scenarios.append((
    "3. /api/charts/trace-map -> TraceMap.vue",
    "frontend/src/components/TraceMap.vue",
    [r.json()] if r.status_code == 200 else [],
    # 豁免的是**本地常量**而非接口字段：BASE_LAYERS 对象用 Leaflet 图层名
    # 作键（BASE_LAYERS['Esri 卫星图 (WGS-84 无偏移)']），与后端无关
    ("Esri 卫星图 (WGS-84 无偏移)",),
))

# ---- 4. 时序图 ----
r = c.get("/api/charts/time-series", headers=H)
scenarios.append((
    "4. /api/charts/time-series -> ChartsView.vue",
    "frontend/src/views/ChartsView.vue",
    [r.json()] if r.status_code == 200 else [],
    (),
))

# ---- 5. 多渠道溯源 ----
r = c.post("/api/trace/multi-source", headers=H, json={})
scenarios.append((
    "5. /api/trace/multi-source -> MultiSourceTrace.vue",
    "frontend/src/components/MultiSourceTrace.vue",
    [r.json()] if r.status_code == 200 else [],
    # 「状态」「建议」只在"所有数据源均无结果"分支出现；
    # 「距离」「地址」在组件里有 || 英文兜底（s['距离'] || s['distance']）
    ("状态", "建议", "距离", "地址"),
))

# ---- 6. RAG ----
r = c.post("/api/rag/search", headers=H, json={"query": "黑臭水体治理", "top_k": 2})
scenarios.append((
    "6. /api/rag/search -> RagSearch.vue",
    "frontend/src/components/RagSearch.vue",
    [r.json()] if r.status_code == 200 else [],
    (),
))

# ---- 7. 河道模型：该视图依赖四个接口，必须取并集 ----
river_payloads = []
for method, path, body in [
    ("GET", "/api/river/params", None),
    ("POST", "/api/river/init", {"n_cross_sections": 30, "auto_detect": True}),
    ("POST", "/api/river/hydro", {"tidal_amplitude_m": 0.0, "duration_h": 2.0}),
    ("POST", "/api/river/pollution", {"load_kg": 50.0, "simulation_hours": 2.0}),
]:
    rr = c.get(path, headers=H) if method == "GET" else c.post(path, headers=H, json=body)
    if rr.status_code == 200:
        river_payloads.append(rr.json())
scenarios.append((
    "7. /api/river/{params,init,hydro,pollution} -> RiverView.vue",
    "frontend/src/views/RiverView.vue",
    river_payloads,
    # 「局限说明」只在 params/init 的响应里，hydro/pollution 用「结果可信度」
    (),
))

for title, component, payloads, ignore in scenarios:
    produced = set()
    for pl in payloads:
        produced |= flat_keys(pl)
    if not produced:
        print(f"\n[跳过] {title}（接口不可用）")
        continue
    misses += compare(title, component, produced, ignore)

print()
print("=" * 72)
n_bad = len(misses)
if n_bad == 0:
    print("结果: 全部通过")
else:
    print(f"结果: {n_bad} 个键不匹配")
    print()
    print("这些键在前端永远取不到值，界面会静默显示空白/兜底值，")
    print("而接口返回 200、数据完全正常 —— 必须修。")
print("=" * 72)
sys.exit(0 if n_bad == 0 else 1)
