# -*- coding: utf-8 -*-
"""泛化能力检查 —— 换一条河还能不能用

为什么需要这个
--------------
本项目的目标不是"只服务雅瑶水道一条河"，而是"喂任意河流的数据都能跑"。
但 2026-10-14 的审计发现：**多项能力只在珠三角成立，换条河会静默算错。**

实测证据（审计当日）：
  - 真实数据上 10 个参数里 9 个来自 KNOWN_RIVERS_GD —— 一张只有 9 条
    广东河流的手工表。出了表，参数退化到区域默认值。
  - classify_region 穷举中国全境网格，99.3% 判为 lowland，
    foothill/mountain/coastal 一次都没返回过 —— 山区河道坡降错 20~33 倍。
  - 数据入口只认一种列命名，别名/英文列名一律 KeyError。

本脚本把这些缺陷固定成可回归的检查。**改动任何一项相关逻辑后都要跑。**

设计原则（沿用 前端字段名检查.py 的教训）：
  - 只测**可观察行为**，不测实现细节
  - 不写会在正确代码上误报的断言（假阳性会毁掉检查工具的可信度）
  - 每一项都注明"为什么这样测"

用法:
    python tests/泛化能力检查.py
"""
import base64
import os
import re
import sys
import warnings

warnings.filterwarnings("ignore")

import logging

logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


# ======================================================================
section("1. 数据入口：不同列命名约定都应被接受")
# ======================================================================
# 为什么这样测：真实河流的数据由不同单位采集，列名不可能统一。
# 只认一种命名 = 只服务一份数据 = 不能泛化。

from water_quality_mcp.src.water_quality_mcp.loaders import load_data


def try_load(csv_text, fname="t.csv"):
    b = base64.b64encode(csv_text.encode("utf-8")).decode()
    try:
        df, rep = load_data(b, fname)
        return ("ok", df, rep)
    except Exception as e:
        return ("exc", None, f"{type(e).__name__}: {e}")


CANON = "时间,经度,纬度,氨氮(mg/L),溶解氧(mg/L),pH\n2025-08-01 10:33:00,113.14,23.11,3.0,2.0,7.2\n"

variants = [
    ("原样列名", CANON, "时间"),
    ("中文别名", "监测时间,经度,纬度,NH3-N,DO,pH\n2025-08-01 10:33:00,113.14,23.11,3.0,2.0,7.2\n", "监测时间"),
    ("英文列名", "datetime,longitude,latitude,ammonia_nitrogen,dissolved_oxygen,ph\n2025-08-01 10:33:00,113.14,23.11,3.0,2.0,7.2\n", "datetime"),
    # 本项验证时间拼接；补齐现在必需的 DO，缺指标拒绝由 test_integrity_regressions 覆盖。
    ("采样日期+时刻分列", "采样日期,采样时刻,经度,纬度,氨氮(mg/L),溶解氧(mg/L)\n2025-08-01,10:33:00,113.14,23.11,3.0,2.0\n", "采样日期"),
]

for label, text, timecol in variants:
    kind, df, info = try_load(text)
    if kind == "exc":
        check(f"1.{label} 可加载", False, info)
    else:
        ok = df is not None and len(df) > 0
        check(f"1.{label} 可加载", ok, f"{len(df) if df is not None else 0} 行")

# 缺时间列：应当给出**可读的错误信息**，而不是 KeyError 崩栈
kind, df, info = try_load("经度,纬度,氨氮(mg/L)\n113.14,23.11,3.0\n")
if kind == "exc":
    check("1.缺时间列时给出可读错误（非 KeyError 崩栈）", False, info)
else:
    check("1.缺时间列时给出可读错误（非 KeyError 崩栈）",
          df is None and "时间" in str(info),
          f"返回 None 且提示含'时间': {str(info)[:60]}")

# 空文件：不能"静默成功返回 0 行"
kind, df, info = try_load("时间,氨氮(mg/L)\n")
if kind == "exc":
    check("1.空文件不静默通过", True, "抛出异常（可接受，但更希望是友好提示）")
else:
    ok = df is None
    check("1.空文件不静默通过", ok,
          "返回 None 并说明" if ok else f"静默返回了 {len(df)} 行的空结果")


# ======================================================================
section("2. 可移植引擎与通用河段")
from river_model.hydro_engine import detect_engine, run_hydrodynamic
from river_model.config import RiverConfig
check("2.无需本机商业软件", detect_engine() == 'pure_python')
check("2.运行入口无 COM 桥接", 'hecras_bridge' not in run_hydrodynamic.__code__.co_names)
check("2.模板不伪造真实位置", RiverConfig().center_lat is None and RiverConfig().catchment_area_km2 is None)

# ======================================================================
section("3. 纵向离散系数：必须随河道属性变化")
# ======================================================================
# 为什么这样测：离散系数物理上取决于河宽/水深/流速/摩阻流速。
# 实测：wq_engine 把它写死为 5.0，而本该算它的 _fischer_dispersion
# 定义了却从未被调用 —— 换任何河，扩散范围都按雅瑶的标定值算。

wq_src = open(os.path.join("river_model", "wq_engine.py"), encoding="utf-8").read()
# 统计"调用"（排除 def 那一行）
calls = [ln for ln in wq_src.splitlines()
         if "_fischer_dispersion(" in ln and not ln.strip().startswith("def ")]
check("3._fischer_dispersion 被实际调用", len(calls) > 0,
      f"调用点 {len(calls)} 处" if calls else "定义了但 0 处调用（离散系数恒为常数）")

const_k = re.search(r"K_array\s*=\s*np\.ones\([^)]*\)\s*\*\s*[\d.]+", wq_src)
check("3.离散系数不是常数赋值", const_k is None,
      f"发现: {const_k.group(0)}" if const_k else "未发现常数赋值")

const_b = re.search(r"B_array\s*=\s*np\.array\(\[\s*[\d.]+\s*\]\s*\*\s*n_cs\)", wq_src)
check("3.河宽不是写死的 55.0", const_b is None,
      f"发现: {const_b.group(0)}" if const_b else "未发现写死河宽")


# ======================================================================
section("4. 潮汐判定：不能只认 5 个海岸参考点")
# ======================================================================
# 为什么这样测：is_tidal 决定潮汐振幅是否为 0。
# 实测参考点仅珠江口/汕头/湛江/福州/上海，半径 50 km。
# 闽江口(26.0,119.6)、辽河口(40.9,121.8)、海河口(39.0,117.7) 等
# 真实感潮河口不在表内 → 判为非潮汐 → 潮汐边界整段归零。

from river_model.parameter_estimator import RiverParameterEstimator

estuaries = [
    ("珠江口", 22.5, 113.5, True),
    ("长江口", 31.4, 121.5, True),
    ("杭州湾", 30.3, 121.0, True),
    ("闽江口", 26.05, 119.60, True),
    ("辽河口", 40.90, 121.80, True),
    ("海河口", 39.00, 117.70, True),
    ("黄河口", 37.80, 119.10, True),
    ("内陆·兰州", 36.06, 103.80, False),
    ("内陆·成都", 30.67, 104.06, False),
]
bad = []
for name, lat, lon, expect in estuaries:
    got = RiverParameterEstimator._is_near_coast(lat, lon)
    if got != expect:
        bad.append(f"{name}(期望{'潮汐' if expect else '非潮汐'},实得{'潮汐' if got else '非潮汐'})")
check("4.主要感潮河口均被识别为潮汐", not bad,
      "；".join(bad) if bad else f"{len(estuaries)} 个测试点全部正确")


# ======================================================================
section("5. 自动检测出的参数必须能进求解器")
# ======================================================================
# 为什么这样测：tools.py 的 run_hydrodynamic 调用传的是**函数形参默认值**
# （6.9/0.83/0.8/12.42），而不是 config 里刚自动算出来的值。
# 后果：辛苦推导的参数白算，求解器永远用雅瑶的默认值。

tools_src = open(os.path.join("river_model", "tools.py"), encoding="utf-8").read()
sigm = re.search(r"def tool_river_hydro_simulation\((.*?)\)\s*->", tools_src, re.S)
if not sigm:
    check("5.能定位 tool_river_hydro_simulation 签名", False)
else:
    sig = sigm.group(1)
    still_default = re.findall(r"(upstream_flow_m3s|downstream_stage_m|tidal_amplitude_m)"
                               r"\s*:\s*float\s*=\s*[\d.]+", sig)
    check("5.水动力入口不再用写死的默认值覆盖 config",
          not still_default,
          f"仍写死: {still_default}" if still_default else "参数默认值已改为 None 并回落到 config")

# 注意：不要限定成 `state.config.xxx` —— 实现里可能先 `cfg = state.config`
# 再 `cfg.xxx`。写成 `state\.config\.` 会误报（本检查器第一版就这么错过一次，
# 而当时行为已验证正确）。只认字段名本身。
uses_cfg = len(re.findall(
    r"\b(?:state\.config|cfg|config)\.(avg_discharge_m3s|tidal_range_m"
    r"|high_water_mean_m|m2_period_h)\b", tools_src))
check("5.求解器入口引用了 config 的自动参数", uses_cfg > 0,
      f"引用 {uses_cfg} 处" if uses_cfg else "0 处引用 —— 自动参数进不了求解器")


# ======================================================================
section("6. 溯源不得写死行政区")
# ======================================================================
# 为什么这样测：trace_engine 把 "广东省"/"佛山市" 写死在**计算路径**里。
# 换任何非佛山的河流，重点排污单位数据源返回的都是佛山的名单 ——
# 这是正确性问题，不是展示问题。

tsrc = open(os.path.join("water_quality_mcp", "src", "water_quality_mcp",
                         "trace_engine.py"), encoding="utf-8").read()
hard_admin = re.findall(r'search_key_pollution_sources\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)', tsrc)
check("6.溯源不写死行政区", not hard_admin,
      f"发现写死: {hard_admin}" if hard_admin else "行政区由调用方传入或从数据推导")


# ======================================================================
section("7. 区域分类：地形起伏判据与降级链")
# ======================================================================
# 为什么这样测：classify_region 原先只用经纬度方框，穷举中国全境网格
# 99.3% 判为 lowland，山地/丘陵一次都不返回 → 山区河道坡降错 20~33 倍。
# 现改用真实地形起伏（SRTM）。本节守住两件事：
#   ① 起伏→类别 的映射符合实测样本
#   ② 地形不可用时**必须**回落到旧逻辑，不能抛异常、更不能返回垃圾
# 全部用纯函数与离线模式检验，**不依赖网络**。

os.environ["RIVER_TERRAIN_OFFLINE"] = "1"   # 本节一律离线

from river_model.regional_defaults import (  # noqa: E402
    classify_region, classify_by_relief, _classify_by_coords, lookup_slope,
)

# ① 起伏 → 类别（阈值取自 15 条实测样本的自然断点）
relief_cases = [
    ("塔里木河(高原平谷)",   915.7,    5.0, "lowland"),
    ("雅瑶水道(三角洲)",       6.6,   27.0, "delta_plain"),
    ("淮河蚌埠段",           28.7,   34.0, "lowland"),
    ("松花江哈尔滨段",       125.4,   43.0, "lowland"),
    ("长江南京段",           19.9,   65.0, "delta_plain"),
    ("嘉陵江重庆段",         291.1,  176.0, "foothill"),
    ("北江清远段",            51.1,  185.0, "foothill"),
    ("澜沧江云南段",         708.8,  466.0, "mountain"),
    ("黄河兰州段",          1774.6,  536.0, "mountain"),
    ("金沙江虎跳峡",        3131.6, 2640.0, "mountain"),
]
wrong = [f"{n}(期望{e},实得{classify_by_relief(m, r)})"
         for n, m, r, e in relief_cases if classify_by_relief(m, r) != e]
check("7.起伏→区域类别映射符合实测样本", not wrong,
      "；".join(wrong) if wrong else f"{len(relief_cases)} 条样本全部正确")

# ② 关键兼容性：样本河道必须判为 delta_plain（否则会打乱既有演示结果）
check("7.样本河道仍判为 delta_plain（演示结果不受影响）",
      classify_by_relief(6.6, 27.0) == "delta_plain"
      and lookup_slope(classify_by_relief(6.6, 27.0), "river") == 0.00010,
      f"坡降 {lookup_slope(classify_by_relief(6.6, 27.0), 'river'):.5f}")

# ③ 离线时仍须给出合法类别。注意 lookup_relief 的顺序是
#    **先查缓存、再判离线** —— 所以离线+有缓存时仍会用真实地形，
#    离线+无缓存才回落到旧逻辑。两种结果都合法，此处只断言不抛异常、
#    不返回非法值；把"山区必须拿到山地坡降"交给 ① 的纯函数断言去保证，
#    否则这个检查会随缓存是否存在而时灵时不灵。
legal = ("delta_plain", "lowland", "foothill", "mountain")
got = classify_region(31.00, 103.60)
check("7.地形不可用时仍返回合法类别（缓存或回落均可）", got in legal,
      f"实得 {got}（{'命中缓存' if got == 'mountain' else '已回落到旧逻辑'}）")

# ④ 降级必须彻底：非法坐标、查询异常都不能把异常抛给调用方
bad_inputs = [("abc", None), (None, None), ("", ""), (float("nan"), 113.0)]
leaked = []
for a, b in bad_inputs:
    try:
        classify_region(a, b)
    except Exception as e:
        leaked.append(f"{a!r}:{type(e).__name__}")
check("7.非法坐标不抛异常", not leaked, "；".join(leaked) if leaked else "4 组非法输入均已兜住")

# ⑤ 旧逻辑确实只覆盖极少数区域（记录这个事实，防止有人误以为它是正确实现）
import random  # noqa: E402
random.seed(0)
seen = set()
for _ in range(3000):
    seen.add(_classify_by_coords(random.uniform(18, 54), random.uniform(74, 136)))
check("7.旧经纬度逻辑的覆盖缺陷被记录在案", len(seen) <= 2,
      f"随机 3000 点只返回 {sorted(seen)} —— 与审计结论一致（占位实现，非正确实现）")


# ======================================================================
print()
print("=" * 74)
n_bad = sum(1 for _, ok in results if not ok)
if n_bad == 0:
    print(f"结果: 全部通过（共 {len(results)} 项）")
else:
    print(f"结果: {n_bad} 项失败 / 共 {len(results)} 项")
    print()
    print("这些是泛化能力的实际缺口 —— 换一条河会静默算错。")
print("=" * 74)
sys.exit(0 if n_bad == 0 else 1)
