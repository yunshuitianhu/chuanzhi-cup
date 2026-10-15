# -*- coding: utf-8 -*-
"""采集河道周边的地形起伏样本（用于区域类型判定）。

为什么需要这个
--------------
`river_model/regional_defaults.py:classify_region` 原先只用经纬度方框判断
区域类型，实测**穷举中国全境网格 99.3% 判为 lowland**，`mountain` / `foothill`
一次都不会返回 —— 山区河道拿到平原坡降，错 20~33 倍。

地形起伏（周边高程的 max-min）是干净得多的判据。实测：

    雅瑶水道 (23.11,113.14)   均值    7 m   起伏   26 m   -> 三角洲平原
    岷江都江堰 (31.00,103.60) 均值  966 m   起伏  874 m   -> 山区
    金沙江虎跳峡(27.20,100.10) 均值 3151 m   起伏 2613 m   -> 高山峡谷

数据源
------
OpenTopoData 公共 API，数据集 srtm90m（NASA/USGS SRTM，**公有领域**）。
无需 API Key。限制：公开实例约 1000 次/天、1 次/秒。
每次请求可带多个坐标（用 | 分隔），因此**一条河只需 1 次调用**。

用法:
    python data/external/terrain/fetch_terrain.py            # 采集全部样本
    python data/external/terrain/fetch_terrain.py --check    # 只校验已有文件
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request

API = "https://api.opentopodata.org/v1/srtm90m"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terrain_samples.json")

# 采样半径（度）。约 0.09° ≈ 10 km —— 取的是河道周边一个中等流域尺度，
# 太近会只采到河床本身，太远会把远处的山算进来。
RADIUS_DEG = 0.09

# 待采样的河流。选取原则：覆盖中国主要地形类型，并包含本项目的样本河道。
#
# ⚠️ 第四列是**我的粗略先验，不是地面真值**。首次采集后有 9 条与它不符，
#    逐条核对下来多数是**先验写错了**而非分类错了（例如珠江广州段起伏 238 m
#    —— 广州有白云山，本就不是纯三角洲）。因此这一列只作参考，**不作为断言**。
RIVERS = [
    # 名称,            纬度,     经度,     粗略先验（仅供参考，不作断言）
    ("雅瑶水道",        23.11,  113.14,  "delta_plain"),
    ("北江清远段",      23.68,  113.03,  "foothill"),
    ("西江肇庆段",      23.05,  112.47,  "lowland"),
    ("长江南京段",      32.05,  118.80,  "lowland"),
    ("黄河兰州段",      36.06,  103.80,  "foothill"),
    ("岷江都江堰",      31.00,  103.60,  "mountain"),
    ("金沙江虎跳峡",    27.20,  100.10,  "mountain"),
    ("澜沧江云南段",    22.00,  100.80,  "mountain"),
    ("松花江哈尔滨段",  45.80,  126.60,  "lowland"),
    ("塔里木河",        41.00,   85.00,  "lowland"),
    ("珠江广州段",      23.10,  113.30,  "delta_plain"),
    ("汉江武汉段",      30.59,  114.30,  "lowland"),
    ("嘉陵江重庆段",    29.56,  106.55,  "foothill"),
    ("怒江六库段",      25.85,   98.85,  "mountain"),
    ("淮河蚌埠段",      32.92,  117.36,  "lowland"),
]


def sample_points(lat: float, lon: float):
    """返回中心点 + 周边 8 点。"""
    pts = [(lat, lon)]
    for dlat, dlon in [(1, 0), (-1, 0), (0, 1), (0, -1),
                       (0.8, 0.8), (0.8, -0.8), (-0.8, 0.8), (-0.8, -0.8)]:
        pts.append((round(lat + dlat * RADIUS_DEG, 4),
                    round(lon + dlon * RADIUS_DEG, 4)))
    return pts


def fetch(lat: float, lon: float, timeout: int = 30):
    """取一条河周边的 9 点高程。失败返回 None。"""
    pts = sample_points(lat, lon)
    locs = "|".join(f"{a},{b}" for a, b in pts)
    url = f"{API}?locations={locs}"
    req = urllib.request.Request(url, headers={"User-Agent": "river-terrain-fetch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        print(f"    请求失败: {type(e).__name__}: {e}")
        return None
    if data.get("status") != "OK":
        print(f"    API 返回异常: {data.get('status')} {data.get('error', '')}")
        return None
    els = [x.get("elevation") for x in data.get("results", [])]
    els = [e for e in els if e is not None]
    if not els:
        return None
    return {
        "lat": lat, "lon": lon,
        "radius_deg": RADIUS_DEG,
        "elevations": els,
        "mean_m": round(sum(els) / len(els), 1),
        "min_m": round(min(els), 1),
        "max_m": round(max(els), 1),
        "relief_m": round(max(els) - min(els), 1),
    }


# 阈值取自实测样本的**自然断点**，不是先定类别再凑数字。
# 15 条河按起伏排序后，最大的两个间隔出现在：
#     65 m (长江南京) -> 176 m (嘉陵江重庆)   间隔 111 m
#   324 m (西江肇庆) -> 466 m (澜沧江云南)   间隔 142 m
# 阈值就落在这两处。
RELIEF_HILL_MIN = 100.0     # 起伏 >= 100 m 视为丘陵/台地
RELIEF_MOUNTAIN_MIN = 400.0  # 起伏 >= 400 m 视为山地
DELTA_MEAN_MAX = 20.0        # 低平地中，高程均值 <= 20 m 归入三角洲平原


def classify(mean_m: float, relief_m: float) -> str:
    """按高程均值与起伏判定区域类型。

    判据以**起伏**为主、高程为辅 —— 高原上的平缓河谷起伏也小，
    只看高程会把塔里木河（均值 916 m、起伏仅 5 m）误判成山区。

    ⚠️ 本函数**尚未经独立验证**。阈值来自 15 条河的实测起伏自然断点，
    这是经验标定，不等于"分类正确"。真正验证需要各河的实际纵坡，
    而 SRTM 垂直精度约 ±5~10 m，量不出平原河流 3 km 内 0.3 m 的落差，
    因此无法用同一份 DEM 自证。详见同目录 README。
    """
    if relief_m >= RELIEF_MOUNTAIN_MIN:
        return "mountain"
    if relief_m >= RELIEF_HILL_MIN:
        return "foothill"
    return "delta_plain" if mean_m <= DELTA_MEAN_MAX else "lowland"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验已有文件，不联网")
    args = ap.parse_args()

    if args.check:
        if not os.path.exists(OUT):
            print(f"样本文件不存在: {OUT}")
            return 1
        d = json.load(open(OUT, encoding="utf-8"))
        rivers = d.get("rivers", {})
        print(f"样本文件: {OUT}")
        print(f"  采集时间: {d.get('generated')}")
        print(f"  数据来源: {d.get('source')}")
        print(f"  许可    : {d.get('license')}")
        print(f"  河流数  : {len(rivers)}")
        print()
        print("按地形起伏排序（真实 SRTM 数据，非人工标注）")
        print("=" * 74)
        rows = sorted(rivers.items(), key=lambda kv: kv[1]["relief_m"])
        prev = None
        for name, v in rows:
            r = v["relief_m"]
            mark = ""
            if prev is not None and r - prev >= 100:
                mark = f"   <-- 自然断点 (间隔 {r - prev:.0f} m)"
            print(f"  {name:<20}{v['mean_m']:>9.1f}{r:>9.1f}   "
                  f"-> {classify(v['mean_m'], r):<12}{mark}")
            prev = r
        print("=" * 74)
        print(f"  阈值: 起伏 {RELIEF_HILL_MIN:.0f} m 起为丘陵、"
              f"{RELIEF_MOUNTAIN_MIN:.0f} m 起为山地；"
              f"低平地中高程均值 <= {DELTA_MEAN_MAX:.0f} m 归三角洲")
        print()
        print("  ⚠️ 这是**经验标定**，不是独立验证。阈值取自上表自然断点，")
        print("     但各河真实纵坡未知，无法自证分类正确。")
        return 0

    print(f"采集 {len(RIVERS)} 条河的地形样本（每条 1 次 API 调用）")
    print(f"数据源: {API}  (SRTM 90m, NASA/USGS, 公有领域)")
    print()
    rivers = {}
    for i, (name, lat, lon, expected) in enumerate(RIVERS, 1):
        print(f"  [{i:2d}/{len(RIVERS)}] {name} ({lat}, {lon}) ...", end=" ", flush=True)
        v = fetch(lat, lon)
        if v is None:
            print("跳过")
        else:
            v["rough_prior"] = expected    # 仅记录我的先验，供事后比对，不作断言
            v["classified"] = classify(v["mean_m"], v["relief_m"])
            rivers[name] = v
            flag = "" if v["classified"] == expected else "   (先验不同)"
            print(f"均值{v['mean_m']:>7.1f} 起伏{v['relief_m']:>7.1f} "
                  f"-> {v['classified']}{flag}")
        time.sleep(1.2)   # 公开 API 限速 1 次/秒

    out = {
        "generated": time.strftime("%Y-%m-%d"),
        "source": "OpenTopoData public API, dataset=srtm90m",
        "source_url": "https://api.opentopodata.org",
        "license": "SRTM (NASA/USGS) 属公有领域；OpenTopoData 为免费公共服务",
        "note": "每条河取中心点 + 周边 8 点，用于计算地形起伏",
        "rivers": rivers,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print()
    print(f"已写出 {OUT}  ({os.path.getsize(OUT)} 字节, {len(rivers)} 条河)")
    # 注意字段名是 rough_prior 不是 expected —— 重命名时这里曾漏改，
    # 导致脚本联网跑完全程、文件也写成功了，却在最后一行 KeyError 崩掉。
    mism = [n for n, v in rivers.items()
            if v["classified"] != v.get("rough_prior")]
    if mism:
        print(f"与粗略先验不同的有 {len(mism)} 条（先验本身不作断言）: {mism}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
