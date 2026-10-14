# -*- coding: utf-8 -*-
"""河道识别 —— 高德逆地理降级链的回归测试

背景
----
parameter_estimator._identify_river 的最后一层降级是"高德逆地理编码 →
从地址里提取河道名称"。该层曾经整条失效：

    geo_result = reverse_geocode(...)          # 返回的是 JSON 字符串
    address = geo_result.get("address", "")    # 对字符串调 .get()

抛 AttributeError，又被紧随其后的裸 except: pass 吞掉，因此表现为
"静默降级到下一层"，长期无人察觉。且即便类型正确，键名也对不上
（函数返回的是中文键 地址/省份，调用方读的是英文 address/province）。

测试数据的来源（重要）
----------------------
本文件中的 REAL_* 常量是**2026-10-14 用真实高德 API 抓取的真实返回**，
坐标为雅瑶水道实际走航区域，未做任何修改。

之所以强调这一点：本测试最初用的是一份**手工编造的**桩数据
（地址写成"…大沥镇雅瑶水道附近"）。那份桩恰好含有"雅瑶水道"字样，
于是"从地址提取河道名"看起来完全正常 —— 而真实高德返回的是
街道地址与场所名，其中"江/河"等字多是"江南""河畔"的一部分，
原实现会从中抠出并不存在的水体名（实测误报：从
"…狮山镇唐苑居江南园艺花场"抠出「唐苑居江」）。
**编造的测试数据掩盖了真实的缺陷。**

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


# ---------- 真实高德返回（2026-10-14 实际抓取，未修改）----------
REAL_START = {          # (23.1585, 113.1201) 实测走航起点
    "地址": "广东省佛山市南海区狮山镇唐苑居江南园艺花场",
    "省份": "广东省", "城市": "佛山市", "区县": "南海区",
    "周边POI": [],
}
REAL_CENTER = {         # (23.14, 113.13) 雅瑶水道 bbox 中心
    "地址": "广东省佛山市南海区大沥镇桂和路80号",
    "省份": "广东省", "城市": "佛山市", "区县": "南海区",
    "周边POI": [],
}

# 合成数据：仅用于验证"地址里真有水体名时能否提取"这条正向路径。
# 真实高德的 regeo 不返回水体名，所以这条路径无法用真实数据覆盖。
SYNTH_WITH_RIVER = {
    "地址": "广东省佛山市南海区大沥镇雅瑶水道北岸",
    "省份": "广东省", "城市": "佛山市", "区县": "南海区",
    "周边POI": [],
}

# ---------- 打桩：隔离 OSM，高德用上面的固定返回 ----------
import river_model.osm_river_lookup as osm
osm.query_waterway_nearby = lambda *a, **k: None
osm.match_river_from_track = lambda *a, **k: None

import water_quality_mcp.src.water_quality_mcp.geo_tools as gt

_current = {"reply": json.dumps(REAL_START, ensure_ascii=False)}
gt.reverse_geocode = lambda lat, lon, key: _current["reply"]

from river_model.parameter_estimator import RiverParameterEstimator


def identify(payload, lat=23.1585, lon=113.1201):
    _current["reply"] = json.dumps(payload, ensure_ascii=False) if isinstance(payload, dict) else payload
    est = RiverParameterEstimator(amap_key="amap-key-for-test")
    return est._identify_river(None, lat, lon)


print("=" * 72)
print("河道识别 — 高德逆地理降级链")
print("=" * 72)

# ---------- 1. 真实返回：位置应正确提取 ----------
print()
print("1. 真实高德返回（实测抓取）")
print(f"   地址: {REAL_START['地址']}")
r = identify(REAL_START)
print(f"   → location = {r.get('location')!r}  来源={r.get('location_source')}")
print(f"   → name     = {r.get('name')!r}  来源={r.get('name_source')}")
print()
check("拼出 省+市+区 位置串", r.get("location") == "广东省佛山市南海区",
      f"location={r.get('location')!r}（修复前恒为空串）")
check("位置来源标记为 amap_reverse_geocode",
      r.get("location_source") == "amap_reverse_geocode",
      f"location_source={r.get('location_source')!r}")

# ---------- 2. 真实返回：不得产生水体名误报 ----------
print()
print("2. 真实地址中的假阳性（关键回归项）")
print("   地址含「唐苑居江南园艺花场」—— 其中的「江」属于「江南」，")
print("   不是水体名。原实现会抠出并不存在的「唐苑居江」。")
print()
check("未从场所名中编造水体名", r.get("name") != "唐苑居江",
      f"name={r.get('name')!r}")
check("无法确定水体时诚实返回「未知河道」", r.get("name") == "未知河道",
      f"name={r.get('name')!r}（宁可为空，不可编造）")

r2 = identify(REAL_CENTER, lat=23.14, lon=113.13)
check("另一条真实地址同样不误报", r2.get("name") == "未知河道",
      f"地址={REAL_CENTER['地址'][:20]}… → name={r2.get('name')!r}")
check("另一条真实地址的位置仍正确",
      r2.get("location") == "广东省佛山市南海区",
      f"location={r2.get('location')!r}")

# ---------- 3. 正向路径：地址中确有水体名时应能提取 ----------
print()
print("3. 正向路径（合成数据，真实高德不返回水体名，故无法用真实数据覆盖）")
print(f"   地址: {SYNTH_WITH_RIVER['地址']}")
r3 = identify(SYNTH_WITH_RIVER)
check("地址含真实水体名时能提取", r3.get("name") == "雅瑶水道",
      f"name={r3.get('name')!r}")
check("名称来源标记为 amap_address", r3.get("name_source") == "amap_address",
      f"name_source={r3.get('name_source')!r}")

# ---------- 4. 边界：非 JSON 返回值 ----------
print()
print("4. 边界情况（降级链不应因此中断）")
for bad in ("逆地理编码失败", "逆地理编码异常", ""):
    try:
        rb = identify(bad)
        check(f"返回 {bad!r} 时不崩溃", isinstance(rb, dict),
              f"name={rb.get('name')!r}")
    except Exception as e:
        check(f"返回 {bad!r} 时不崩溃", False, f"抛出 {type(e).__name__}: {e}")

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
