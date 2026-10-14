# -*- coding: utf-8 -*-
"""用真实样本数据驱动 app.py 前端 —— 端到端演示

流程（与人工操作完全一致）：
    1. 侧边栏上传走航数据 xlsx
    2. 点「加载数据」
    3. tab1 点「🚀 开始自动分析」→ DeepSeek Agent 自主调用工具链
    4. 打印 Agent 的结论、图表产物、技术展示页的结果

依赖真实的 DEEPSEEK_API_KEY 与 AMAP_KEY（从项目 .env 读取）。
会真实调用 LLM 与高德接口。

用法：
    python tests/前端演示_真实数据.py [样本路径]
"""
import logging
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

SAMPLE = (sys.argv[1] if len(sys.argv) > 1 else
          r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx")

from streamlit.testing.v1 import AppTest

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def sep(t):
    print()
    print("=" * 74)
    print(t)
    print("=" * 74)


def ss(at, key, default=None):
    """安全读取 session_state。

    注意：AppTest 的 session_state 是 SafeSessionState，**没有 .get() 方法**
    （属性访问会被代理成 key 查找）。必须用 [] 并捕获 KeyError。
    """
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


def show_errors(at, label):
    if at.exception:
        print(f"  ✗ {label}：{len(at.exception)} 个异常")
        for e in at.exception:
            print("     ", str(e.value)[:400])
        return True
    print(f"  ✓ {label}：无异常")
    return False


# ---------------------------------------------------------------- 1
sep("1. 启动应用")
t0 = time.time()
at = AppTest.from_file("app.py", default_timeout=600)
at.run()
print(f"  启动耗时 {time.time()-t0:.1f}s")
show_errors(at, "启动")
print(f"  标题: {[t.value for t in at.title]}")
print(f"  标签页: {[t.label for t in at.tabs]}")
print(f"  已配置密钥: DeepSeek={'✓' if at.sidebar.text_input[0].value else '✗'} "
      f"高德={'✓' if at.sidebar.text_input[1].value else '✗'} "
      f"星图={'✓' if at.sidebar.text_input[2].value else '✗'}")
print(f"  目标水质等级: {at.sidebar.selectbox[0].value}")

# ---------------------------------------------------------------- 2
sep("2. 上传走航数据")
with open(SAMPLE, "rb") as f:
    content = f.read()
print(f"  文件: {os.path.basename(SAMPLE)}  ({len(content)} 字节)")
at.sidebar.file_uploader[0].set_value((os.path.basename(SAMPLE), content, XLSX_MIME))
at.run()
show_errors(at, "上传后重跑")

# ---------------------------------------------------------------- 3
sep("3. 点击「加载数据」")
load_btn = None
for b in at.sidebar.button:
    if "加载数据" in b.label:
        load_btn = b
        break
if load_btn is None:
    print("  ✗ 未找到「加载数据」按钮")
    sys.exit(1)
print(f"  点击: {load_btn.label!r}")
t0 = time.time()
load_btn.click()
at.run()
print(f"  耗时 {time.time()-t0:.1f}s")
show_errors(at, "加载数据")

# 加载结果以 success 形式呈现；这里改为直接看 session_state
try:
    df = ss(at, "df")
    if df is not None:
        print(f"  ✓ 数据已载入: {len(df)} 条记录")
        print(f"    时间范围: {df['datetime'].min()} ~ {df['datetime'].max()}")
        print(f"    列: {list(df.columns)}")
    else:
        print("  ✗ session_state.df 为空")
except KeyError:
    print("  ✗ session_state 中没有 df")

# ---------------------------------------------------------------- 4
sep("4. tab1 点击「🚀 开始自动分析」")
start_btn = None
for b in at.button:
    if "开始自动分析" in b.label:
        start_btn = b
        break
if start_btn is None:
    print("  ✗ 未找到「开始自动分析」按钮")
    print("     当前按钮:", [b.label for b in at.button][:8])
    sys.exit(1)
print(f"  点击: {start_btn.label!r}")
print("  （DeepSeek Agent 正在自主调用工具链，可能需要 30~120 秒）")
t0 = time.time()
start_btn.click()
at.run()
el = time.time() - t0
print(f"  耗时 {el:.1f}s")
show_errors(at, "自动分析")

# ---------------------------------------------------------------- 4.5
sep("4.5 Agent 实际调用了哪些工具（按顺序）")
full = ss(at, "full_messages", []) or []
seq = []
for m in full:
    role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
    if role != "assistant":
        continue
    tcs = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
    for tc in (tcs or []):
        fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
        name = (fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", "?")) if fn else "?"
        args = (fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", "")) if fn else ""
        seq.append((name, (args or "")[:90]))

print(f"  共 {len(seq)} 次工具调用，{len(full)} 条上下文消息")
for i, (name, args) in enumerate(seq, 1):
    print(f"  {i:2d}. {name:34s} {args}")

# 关键验证：RAG 检索是否真的被调用、且返回了内容
rag_calls = [a for n, a in seq if "treatment_cases" in n]
rag_tool_msgs = [m for m in full
                 if (m.get("role") if isinstance(m, dict) else getattr(m, "role", "")) == "tool"]
print()
print(f"  RAG 检索工具被调用: {'✓ ' + str(len(rag_calls)) + ' 次' if rag_calls else '✗ 未调用'}")
if rag_calls:
    for a in rag_calls:
        print(f"     查询: {a}")

# ---------------------------------------------------------------- 5
sep("5. Agent 结论")
msgs = ss(at, "messages", []) or []
print(f"  对话消息数: {len(msgs)}")
for m in msgs:
    role = m.get("role")
    content = (m.get("content") or "").strip()
    if not content:
        continue
    print(f"  --- {role} ---")
    for line in content.splitlines()[:40]:
        print("   ", line)

# ---------------------------------------------------------------- 6
sep("6. 生成的产物")
import glob
artifacts = ["time_series.png", "trace_map.html", "cross_validation.png",
             "correlation_heatmap.png", "_uploaded_data.xlsx",
             "river_hydro_profile.png", "river_pollution_animation.html"]
for a in artifacts:
    if os.path.exists(a):
        print(f"  ✓ {a:34s} {os.path.getsize(a):>10,} 字节")
    else:
        print(f"  · {a:34s} （未生成）")

# ---------------------------------------------------------------- 7
sep("7. 图表说明文字（由本次分析自动生成）")
for k, v in (ss(at, "chart_captions", {}) or {}).items():
    print(f"  [{k}]")
    for line in str(v).splitlines():
        print("   ", line)
    print()

# ---------------------------------------------------------------- 8
sep("8. 技术展示页 —— 各模块状态")
for label, key in [("黑臭检测", "tech_black_result"), ("国控站点", "tech_ns_result"),
                   ("多渠道溯源", "tech_trace_result"), ("RAG 检索", "tech_rag_result"),
                   ("河道参数", "tech_river_config")]:
    print(f"  {label:10s}: {'有' if ss(at, key) else '无'}")

# 分析图表 tab 里实际渲染出的图片文件
print()
print("  图表说明是否已生成:")
caps = ss(at, "chart_captions", {}) or {}
for k in ["time_series", "trace_map", "cross_validation", "correlation_heatmap"]:
    print(f"    {k:20s}: {'有' if caps.get(k) else '无'}")

sep("演示结束")
