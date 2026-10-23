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
import argparse
import tempfile
from unittest.mock import patch

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

from fastapi.testclient import TestClient

from backend.main import app

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('sample', nargs='?', default=r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx")
parser.add_argument('--offline', action='store_true', help='以受控外部适配器覆盖成功字段；不是在线服务验收')
args = parser.parse_args()
SAMPLE = args.sample

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
if args.offline and not data_ready:
    raise RuntimeError('离线字段检查样本未成功加载，拒绝跳过')

misses = []
scenarios = []

# ---- 1. 黑臭检测 ----
r = c.post("/api/analysis/black-spots", headers=H)
scenarios.append((
    "1. /api/analysis/black-spots -> BlackSpotResult.vue",
    "frontend/src/components/BlackSpotResult.vue",
    [r.json()] if r.status_code == 200 else [],
    ("提示",),  # 仅零预警分支返回；组件v-if显示，零预警SSR回归另行覆盖。
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
if args.offline:
    with patch('water_quality_mcp.src.water_quality_mcp.pollution_permit.search_pollution_permits', return_value=[]), \
         patch('water_quality_mcp.src.water_quality_mcp.pollution_permit.search_key_pollution_sources', return_value=[]):
        r = c.post('/api/trace/multi-source', headers=H, json={})
else:
    r = c.post("/api/trace/multi-source", headers=H, json={})
trace_payloads = [r.json()] if r.status_code == 200 else []
if args.offline:
    # HTTP/融合内核真实运行，外部适配器受控返回空/候选；不跳过成功分支的字段检查。
    if r.status_code != 200 or '状态' not in r.json():
        raise RuntimeError('离线多源降级响应不符合预期')
    with patch('backend.routers.trace.resolve_api_key', return_value='ci-fixture-not-a-credential'), \
         patch('water_quality_mcp.src.water_quality_mcp.geo_tools.search_nearby_pollution_sources',
               return_value=[{'name':'CI仿真POI（非真实企业）','address':'仿真位置','distance':10}]), \
         patch('water_quality_mcp.src.water_quality_mcp.pollution_permit.search_pollution_permits',
               return_value=[{'企业名称':'CI仿真许可（非真实企业）','数据源':'排污许可证','地址':'仿真位置','距离':20}]):
        controlled = c.post('/api/trace/multi-source', headers=H, json={})
    if controlled.status_code != 200 or len(controlled.json().get('综合污染源列表', [])) != 2:
        raise RuntimeError('受控多源成功分支未运行')
    trace_payloads.append(controlled.json())
    print('[受控适配器] 多源成功+降级字段；不代表真实外部数据命中')
scenarios.append((
    "5. /api/trace/multi-source -> MultiSourceTrace.vue",
    "frontend/src/components/MultiSourceTrace.vue",
    trace_payloads,
    # 「状态」「建议」只在"所有数据源均无结果"分支出现；
    # 「距离」「地址」在组件里有 || 英文兜底（s['距离'] || s['distance']）
    ("状态", "建议", "距离", "地址"),
))

# ---- 6. RAG ----
if args.offline:
    with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.RAGEngine.retrieve',
               side_effect=ModuleNotFoundError('CI受控缺依赖')):
        r = c.post('/api/rag/search', headers=H, json={'query':'黑臭水体治理','top_k':2})
else:
    r = c.post("/api/rag/search", headers=H, json={"query": "黑臭水体治理", "top_k": 2})
rag_payloads = [r.json()] if r.status_code == 200 else []
if args.offline:
    if r.status_code != 503:
        raise RuntimeError('CI不应依赖已安装的真实向量库/嵌入模型')
    from water_quality_mcp.src.water_quality_mcp.rag_engine import RAGEngine
    with tempfile.TemporaryDirectory(prefix='ci-rag-fields-') as folder:
        engine = RAGEngine(folder)
        hits = [{'metadata':{'title':'CI仿真资料','source':'CI_仿真资料.md'},
                 'distance':.2,'document':'仅供接口字段校验的仿真文字，非治理效果证据。','id':'CI_仿真资料.md_0'}]
        with patch('water_quality_mcp.src.water_quality_mcp.rag_engine.get_rag_engine', return_value=engine), \
             patch.object(engine, 'retrieve', return_value=hits):
            controlled = c.post('/api/rag/search', headers=H, json={'query':'仿真字段检查','top_k':2})
    if controlled.status_code != 200 or controlled.json().get('共检索到') != 1:
        raise RuntimeError('受控RAG成功分支未运行')
    rag_payloads.append(controlled.json())
    print('[受控适配器] RAG格式化成功字段；不是实际语义检索效果验收')
scenarios.append((
    "6. /api/rag/search -> RagSearch.vue",
    "frontend/src/components/RagSearch.vue",
    rag_payloads,
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

# ======================================================================
# 附：会话头契约检查
# ======================================================================
# 为什么单列一节：这与字段名是**同一类**缺陷 —— 接口返回 200，契约却被违反，
# 界面表现自相矛盾且不报错。
#
# 实际发生过：ChatView.vue 直接用 fetch 发 SSE 请求，只设了 Content-Type，
# 没带 X-Session-Id。后端于是**新建一个空会话**，Agent 的所有工具都报
# "数据未加载"，而侧栏/顶栏（走 client.js 的 request() 包装器，带了头）
# 却正常显示"1805 条"。用户看到的是"我明明上传了数据，它说没有"。
#
# 规则：frontend/src 下**任何**直接调用 fetch 的地方都必须带会话头
# （字面量 'X-Session-Id'，或 client.js 导出的 sseHeaders()）。

print()
print("=" * 72)
print("附：会话头契约（直接 fetch 必须带 X-Session-Id）")
print("=" * 72)

SRC = os.path.join(PROJ, "frontend", "src")
offenders = []
for root, _dirs, files in os.walk(SRC):
    for fn in files:
        if not fn.endswith((".vue", ".js")):
            continue
        path = os.path.join(root, fn)
        rel = os.path.relpath(path, PROJ).replace("\\", "/")
        if rel.endswith("api/client.js"):
            continue                      # 包装器本身，正确实现就在这里
        raw = open(path, encoding="utf-8").read()
        # 剥掉注释，避免把说明文字当成代码（这个坑本仓库踩过一次）
        raw = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
        raw = "\n".join(li.split("//")[0] for li in raw.splitlines())
        for m in re.finditer(r"fetch\((.{0,300}?)\)", raw, flags=re.S):
            call = m.group(1)
            if "/api/" not in call:
                continue
            if "X-Session-Id" in call or "sseHeaders" in call:
                continue
            line_no = raw[:m.start()].count("\n") + 1
            offenders.append(f"{rel}:{line_no}")

if offenders:
    print(f"  [FAIL] {len(offenders)} 处直接 fetch 未带会话头：")
    for o in offenders:
        print(f"        x {o}")
    print()
    print("  后果：后端会为这些请求新建空会话 —— Agent 看不到已上传的数据，")
    print("        而其它走 client.js 的请求正常，界面自相矛盾且接口全返回 200。")
else:
    print("  [PASS] 所有直接 fetch 均带会话头")

print()
print("=" * 72)
n_bad = len(misses) + len(offenders)
if n_bad == 0:
    print("结果: 全部通过")
else:
    if misses:
        print(f"结果: {len(misses)} 个键不匹配 + {len(offenders)} 处缺会话头")
        print()
        print("这些缺陷共同的特征是：**接口返回 200、数据完全正常，")
        print("界面却显示空白或自相矛盾 —— 必须修。**")
    else:
        print(f"结果: {len(offenders)} 处直接 fetch 缺会话头")
print("=" * 72)
sys.exit(0 if n_bad == 0 else 1)
