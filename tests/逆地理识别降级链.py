# -*- coding: utf-8 -*-
"""河道识别 —— 高德逆地理降级链的回归测试

背景
----
parameter_estimator._identify_river 的最后一层降级是"高德逆地理编码 →
从地址里提取河道名称"。该层曾经整条失效：

    geo_result = reverse_geocode(...)          # 返回的是 JSON 字符串
    address = geo_result.get("address", "")    # 对字符串调 .get()

抛 AttributeError，又被紧随其后的裸 except: pass 吞掉，因此表现为
"静默降级到下一层"，多年无人察觉。且即便类型正确，键名也对不上
（函数返回的是中文键 地址/省份，调用方读的是英文 address/province）。

本测试用打桩替代真实高德调用，验证该层能正常工作。

用法：
    python tests/逆地理识别降级链.py
"""
import json
import logging
import os
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJ)

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


# ---------- 打桩：隔离外部依赖 ----------
# OSM 那两层要先失效，才能落到高德这一层
import river_model.osm_river_lookup as osm
osm.query_waterway_nearby = lambda *a, **k: None
osm.match_river_from_track = lambda *a, **k: None

import water_quality_mcp.src.water_quality_mcp.geo_tools as gt

FAKE_REPLY = json.dumps({
    "地址": "广东省佛山市南海区大沥镇雅瑶水道附近",
    "省份": "广东省",
    "城市": "佛山市",
    "区县": "南海区",
    "周边POI": [],
}, ensure_ascii=False)

gt.reverse_geocode = lambda lat, lon, key: FAKE_REPLY

from river_model.parameter_estimator import RiverParameterEstimator

print("=" * 72)
print("河道识别 — 高德逆地理降级链")
print("=" * 72)
print()
print("桩数据（模拟高德真实返回的 JSON 字符串）:")
print(f"  {FAKE_REPLY}")
print()

est = RiverParameterEstimator(amap_key="fake-key-for-test")
r = est._identify_river(None, 23.12, 113.13)

print("识别结果:")
for k in ("name", "name_source", "name_confidence",
          "location", "location_source", "location_confidence"):
    print(f"  {k:22s} = {r.get(k)!r}")
print()

check("从地址中提取出河道名称", r.get("name") == "雅瑶水道",
      f"name={r.get('name')!r}（修复前恒为 '未知河道'）")
check("名称来源标记为 amap_address", r.get("name_source") == "amap_address",
      f"name_source={r.get('name_source')!r}")
check("拼出 省+市+区 位置串", r.get("location") == "广东省佛山市南海区",
      f"location={r.get('location')!r}（修复前恒为空串）")
check("位置来源标记为 amap_reverse_geocode",
      r.get("location_source") == "amap_reverse_geocode",
      f"location_source={r.get('location_source')!r}")

# ---------- 边界：返回值不是 JSON 时应静默跳过，而非抛异常 ----------
print()
print("边界情况:")
for bad in ("逆地理编码失败", "逆地理编码异常", ""):
    gt.reverse_geocode = lambda lat, lon, key, _b=bad: _b
    est2 = RiverParameterEstimator(amap_key="fake-key-for-test")
    try:
        r2 = est2._identify_river(None, 23.12, 113.13)
        ok = isinstance(r2, dict)
        check(f"返回 {bad!r} 时不崩溃", ok, f"name={r2.get('name')!r}")
    except Exception as e:
        check(f"返回 {bad!r} 时不崩溃", False, f"抛出 {type(e).__name__}: {e}")

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
