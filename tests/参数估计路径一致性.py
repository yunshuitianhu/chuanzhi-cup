# -*- coding: utf-8 -*-
"""河道参数估计 —— 两条入口路径的一致性检查

背景
----
parameter_estimator 有两个入口，输入信息等价时**应当给出同一套参数**：

    estimate_config_from_data(df)      走完整 DataFrame
    estimate_config_from_gps(gps_mapping)  只走 gps_mapping

修复前后者是个半成品，三处问题叠加：

  1. 调 _identify_river 时传 df=None，取不到 GPS 点 →
     「已知河道坐标库」(置信度 0.95) 与「OSM 轨迹匹配」两层整层被跳过，
     河道识别恒为「未知河道」
  2. 即便识别出了河道名，也只有"宽度"这一项查了已知河道库；
     糙率/坡降/水深/流量/潮汐全走通用默认值
  3. param_sources 写死为区域默认值、且缺 4 个键 →
     UI 上的置信度色标恒显示低置信度，部分字段来源显示为空白

后果：同样一段 GPS 轨迹，用哪条路径进去会得到两套差别很大的河道参数，
且没有任何提示。

本测试用落在「北江」bbox 内的坐标验证两条路径完全一致。

用法：
    python tests/参数估计路径一致性.py
"""
import logging
import os
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

# ---------- 打桩隔离网络（OSM / 高德）----------
import river_model.osm_river_lookup as osm

osm.query_waterway_nearby = lambda *a, **k: None
osm.match_river_from_track = lambda *a, **k: None

import water_quality_mcp.src.water_quality_mcp.geo_tools as gt

gt.reverse_geocode = lambda *a, **k: "逆地理编码失败"

import numpy as np
import pandas as pd

from river_model.parameter_estimator import (
    estimate_config_from_data,
    estimate_config_from_gps,
)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def make_inputs(lon0, lon1, lat0, lat1, n=30):
    lon = np.linspace(lon0, lon1, n)
    lat = np.linspace(lat0, lat1, n)
    df = pd.DataFrame({
        "datetime": pd.date_range("2024-01-01", periods=n, freq="10s"),
        "经度": lon, "纬度": lat,
        "氨氮(mg/L)": np.full(n, 3.0),
        "溶解氧(mg/L)": np.full(n, 4.0),
        "化学需氧量(mg/L)": np.full(n, 30.0),
    })
    mapping = {
        "total_length_m": 5000.0,
        "lat_raw": lat, "lon_raw": lon, "point_count": n,
    }
    return df, mapping


COMPARE_FIELDS = [
    "river_name", "depth_avg_m", "mannings_n",
    "longitudinal_slope", "avg_discharge_m3s", "tidal_range_m",
]

print("=" * 72)
print("河道参数估计 —— 两条路径一致性")
print("=" * 72)

# ---------- 场景 1：落在已知河道 bbox 内 ----------
print()
print("场景 1：坐标落在「北江」bbox 内（已知河道库应命中）")
df, mp = make_inputs(113.30, 113.35, 23.60, 23.65)
c1 = estimate_config_from_data(df)
c2 = estimate_config_from_gps(mp)

print(f"  {'参数':<22}{'数据路径':>16}{'GPS 路径':>16}")
print("  " + "-" * 54)
mismatch = []
for f in COMPARE_FIELDS:
    a, b = getattr(c1, f), getattr(c2, f)
    same = (a == b) or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and abs(a - b) < 1e-6)
    if not same:
        mismatch.append(f)
    print(f"  {f:<22}{str(a):>16}{str(b):>16}  {'✓' if same else '✗'}")
print()

check("两条路径参数完全一致", not mismatch, f"不一致项: {mismatch}" if mismatch else "6 项全部一致")
check("识别出已知河道", c1.river_name == c2.river_name == "北江",
      f"data={c1.river_name!r} gps={c2.river_name!r}")
check("地名命中不等于糙率已率定",
      c2.param_confidence.get("mannings_n", 0) < 0.8,
      f"mannings_n 置信度 = {c2.param_confidence.get('mannings_n')}")

# ---------- 场景 2：落在所有已知河道 bbox 之外 ----------
print()
print("场景 2：坐标在湖南（不在任何已知河道 bbox 内）")
df2, mp2 = make_inputs(112.90, 112.95, 28.15, 28.20)
c3 = estimate_config_from_data(df2)
c4 = estimate_config_from_gps(mp2)

print(f"  data 路径: river={c3.river_name!r} discharge={c3.avg_discharge_m3s} tidal_range={c3.tidal_range_m}")
print(f"  gps  路径: river={c4.river_name!r} discharge={c4.avg_discharge_m3s} tidal_range={c4.tidal_range_m}")
print()
check("未知河道时两路径仍一致",
      all(abs(getattr(c3, f) - getattr(c4, f)) < 1e-6
          for f in COMPARE_FIELDS if isinstance(getattr(c3, f), (int, float))),
      "数值型字段全部一致")
area = c3.width_avg_m * c3.depth_avg_m
radius = area / (c3.width_avg_m + 2*c3.depth_avg_m)
expected_flow = area * radius**(2/3) * np.sqrt(c3.longitudinal_slope) / c3.mannings_n
check("未知河道流量由本河段几何估计", abs(c3.avg_discharge_m3s-expected_flow) < max(.2,.01*expected_flow),
      f"flow={c3.avg_discharge_m3s}, independent Manning={expected_flow}")

# ---------- param_sources 完整性 ----------
print()
print("场景 3：param_sources / param_confidence 的完整性")
EXPECT_KEYS = {"river_name", "location", "length_m", "width_avg_m",
               "depth_avg_m", "mannings_n", "longitudinal_slope",
               "avg_discharge_m3s", "tidal_range_m"}
for label, cfg in (("数据路径", c1), ("GPS 路径", c2)):
    miss_s = sorted(EXPECT_KEYS - set(cfg.param_sources))
    # 未配准的GPS航程不能提供真实河长的95%置信度；此字段应明确保留为空。
    miss_c = sorted((EXPECT_KEYS - {'length_m'}) - set(cfg.param_confidence))
    check(f"{label} param_sources 键完整", not miss_s, f"缺失 {miss_s}" if miss_s else "9 项齐全")
    check(f"{label} 不给未配准河长编造置信度", not miss_c and 'length_m' not in cfg.param_confidence
          and cfg.param_sources.get('length_m') == 'gps_track_unregistered',
          f"其它字段缺失 {miss_c}" if miss_c else "其余8项齐全，河长来源明确为未配准航程建议")

# 命中已知库时来源标签不应再写死为区域默认值
check("地名识别不自动套用参考物理参数",
      c2.param_sources.get("mannings_n") != "known_river_database",
      f"mannings_n 来源 = {c2.param_sources.get('mannings_n')!r}")

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
