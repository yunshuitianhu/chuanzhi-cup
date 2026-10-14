# -*- coding: utf-8 -*-
"""经纬度缺失值对齐检查

背景
----
parameter_estimator._get_coords 曾经对纬度和经度**各自独立** dropna()：

    lats = pd.to_numeric(...["lat"]...).dropna()...
    lons = pd.to_numeric(...["lon"]...).dropna()...

若纬度列第 3 行缺失、经度列第 7 行缺失，两次 dropna 会得到
长度不同、且行与行不再对应的两个数组。后果分两种：

  - 长度不同：Haversine 广播失败抛 ValueError，被上层 except 捕获，
    表现为"自动检测失败"，全部参数静默退回默认值
  - 长度恰好相同（更危险）：不报错，但河长、中心点、OSM 轨迹匹配
    全部基于错位的点对

本测试用**可验证的配对关系**（lat=20+i, lon=100+i）构造数据，
使"行是否对应"可以被直接检验，而不是只看长度。

用法：
    python tests/经纬度缺失值对齐.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

import numpy as np
import pandas as pd

from river_model.parameter_estimator import RiverParameterEstimator

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


print("=" * 72)
print("经纬度缺失值对齐检查")
print("=" * 72)

# ---------- 构造：lat 与 lon 在不同的行缺失 ----------
# 配对关系 lat[i] = 20 + i, lon[i] = 100 + i —— 可据此还原原始行号
n = 20
raw_lat = [20.0 + i for i in range(n)]
raw_lon = [100.0 + i for i in range(n)]

LAT_NAN_ROWS = {2, 3}
LON_NAN_ROWS = {7, 11}

lat_col = [None if i in LAT_NAN_ROWS else raw_lat[i] for i in range(n)]
lon_col = [None if i in LON_NAN_ROWS else raw_lon[i] for i in range(n)]

df = pd.DataFrame({"纬度": lat_col, "经度": lon_col})

print()
print(f"构造数据: {n} 行")
print(f"  纬度缺失行: {sorted(LAT_NAN_ROWS)}")
print(f"  经度缺失行: {sorted(LON_NAN_ROWS)}")
print(f"  配对关系: lat = 20+i, lon = 100+i（可据差值还原原始行号）")
print()

# ---------- 修复后的行为 ----------
lats, lons = RiverParameterEstimator._get_coords(df)

print(f"_get_coords 返回: {len(lats)} 个纬度, {len(lons)} 个经度")
check("经纬度数量相同", len(lats) == len(lons),
      f"{len(lats)} vs {len(lons)}")

expect = sorted(set(range(n)) - LAT_NAN_ROWS - LON_NAN_ROWS)
check("剔除的行数正确（两侧缺失的并集）", len(lats) == len(expect),
      f"实际保留 {len(lats)} 行，应为 {len(expect)} 行")

# 逐行验证配对关系：lon - lat 应恒为 80
if len(lats) == len(lons) and len(lats) > 0:
    diffs = lons - lats
    aligned = np.allclose(diffs, 80.0)
    check("逐行配对关系正确（lon-lat 恒为 80）", aligned,
          f"差值范围 [{diffs.min():.1f}, {diffs.max():.1f}]，应恒为 80.0")

    # 还原原始行号，确认保留的正是两侧都有效的行。
    # 注意用纬度还原：lat = 20 + i，故 i = lat - 20。
    recovered = sorted(int(round(v)) - 20 for v in lats)
    print(f"       还原出的原始行号: {recovered}")
    check("还原出的行号与预期一致", recovered == expect,
          f"预期 {expect}")
else:
    check("逐行配对关系正确（lon-lat 恒为 80）", False, "数组长度不一致，无法配对")
    check("还原出的行号与预期一致", False, "同上")

# ---------- 对照：旧写法的问题 ----------
print()
print("对照 —— 旧写法（各自独立 dropna）会怎样：")
old_lats = pd.to_numeric(df["纬度"], errors="coerce").dropna().to_numpy(dtype=float)
old_lons = pd.to_numeric(df["经度"], errors="coerce").dropna().to_numpy(dtype=float)
print(f"  lats 长度 {len(old_lats)}, lons 长度 {len(old_lons)}")
if len(old_lats) == len(old_lons):
    old_diffs = old_lons - old_lats
    print(f"  lon-lat 差值范围 [{old_diffs.min():.1f}, {old_diffs.max():.1f}]（应恒为 80）")
    print("  >>> 长度恰好相同，不会报错，但配对已错位")
    check("旧写法确实存在错位（已不再是当前行为）", not np.allclose(old_diffs, 80.0),
          "本例中旧写法产生错位，新写法已修正")
else:
    print("  >>> 长度不同，Haversine 会抛广播错误")
    check("旧写法确实存在错位（已不再是当前行为）", True,
          "本例中旧写法长度不同，会抛错")

# ---------- 边界 ----------
print()
print("边界情况:")
for label, d in [
    ("全部有效", pd.DataFrame({"纬度": [23.1, 23.2], "经度": [113.1, 113.2]})),
    ("全部缺失", pd.DataFrame({"纬度": [None, None], "经度": [None, None]})),
    ("缺列", pd.DataFrame({"其他": [1, 2]})),
]:
    la, lo = RiverParameterEstimator._get_coords(d)
    check(f"{label} -> 不崩溃且两侧等长", len(la) == len(lo),
          f"返回 {len(la)} 对")

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
