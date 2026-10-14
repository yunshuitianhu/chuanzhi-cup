# -*- coding: utf-8 -*-
"""数据完整性检查 —— 守住两处"静默丢数据 / 编造数据"的缺陷

一、时间列解析静默丢点
----------------------
geo_mapping.build_chainage_gps_mapping 曾用裸的
`pd.to_datetime(series, errors='coerce')`。pandas 会**从首个非空值推断格式**
并套用整列；本项目的样本首个时间值是 "2025-08-01 10:33"（无秒），
于是所有带秒的 "2025-08-01 10:33:05" 全部解析失败变 NaT，
再被 dropna 丢弃 —— 实测 1805 行只剩 151 行（丢 92%），
而桩号映射与河道长度全部基于这 8% 算出，且没有任何提示。

修复：改为显式格式 + 两遍解析（与 loaders.py 一致），并在丢点时告警。

二、编造的污染源被当作官方数据
------------------------------
pollution_permit.search_key_pollution_sources 曾内置三条占位条目
（"佛山市南海区某印染厂"等），被打上 `数据源: "重点排污单位名录"`
混入溯源结果，并在输出里描述为"地方生态环境局年度公开名录 — 官方监管数据"。
已全部删除：现在只读真实名录文件，读不到就返回空、由调用方跳过该源。

用法：
    python tests/数据完整性检查.py
"""
import logging
import os
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

SAMPLE = (sys.argv[1] if len(sys.argv) > 1 else
          r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


print("=" * 72)
print("数据完整性检查")
print("=" * 72)

# ---------- 一、时间列解析 ----------
print()
print("1. 时间列解析（此前静默丢弃 92% 的 GPS 点）")

import pandas as pd

from river_model.geo_mapping import _parse_time_column

# 混合格式：首个无秒、其余带秒 —— 正是样本数据的形态
mixed = pd.Series(["2025-08-01 10:33", "2025-08-01 10:33:05",
                   "2025-08-01 10:34", "2025-08-01 10:34:12"])
parsed = _parse_time_column(mixed)
check("混合格式全部可解析", parsed.notna().sum() == len(mixed),
      f"{parsed.notna().sum()}/{len(mixed)}")

# 对照：裸调用会失败 —— 这条是缺陷本身的可复现证据
naive = pd.to_datetime(mixed, errors="coerce")
check("裸 pd.to_datetime 确实会失败（缺陷可复现）", naive.notna().sum() < len(mixed),
      f"裸调用仅 {naive.notna().sum()}/{len(mixed)} 成功")

# 真实样本：必须一个点都不丢
if os.path.isfile(SAMPLE):
    from river_model.geo_mapping import build_chainage_gps_mapping

    raw_rows = len(pd.read_excel(SAMPLE))
    m = build_chainage_gps_mapping(SAMPLE, smoothing_sigma=3.0)
    check("真实样本的 GPS 点未被丢弃", m["point_count"] == raw_rows,
          f"point_count={m['point_count']}，原始 {raw_rows} 行")
    check("河道长度已按完整轨迹计算", m["total_length_m"] > 3500,
          f"total_length_m={m['total_length_m']:.1f} m（修复前仅 151 点时算出 3008 m）")
else:
    check("样本文件存在", False, SAMPLE)

# ---------- 二、不得编造污染源 ----------
print()
print("2. 污染源数据不得编造")

import re

src = open("water_quality_mcp/src/water_quality_mcp/pollution_permit.py",
           encoding="utf-8").read()
# 去掉 docstring 与注释后检查可执行代码
code_only = re.sub(r'""".*?"""', "", src, flags=re.S)
code_only = re.sub(r"#.*", "", code_only)
for token in ["某印染厂", "某电镀", "某食品加工", "_GUANGDONG_KEY_SOURCES"]:
    check(f"可执行代码中无 {token!r}", token not in code_only)

from water_quality_mcp.src.water_quality_mcp.pollution_permit import (
    search_key_pollution_sources,
)

r = search_key_pollution_sources("广东省", "佛山市")
check("无名录文件时返回空列表（宁可不报，不可编造）", r == [],
      f"返回 {len(r)} 条")
check("返回项中无占位式名称（含「某」字）",
      not any("某" in str(x.get("企业名称", "")) for x in r))

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
