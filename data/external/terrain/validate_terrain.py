# -*- coding: utf-8 -*-
"""用独立数据验证地形分类的坡降是否合理。

为什么要做这个
--------------
`fetch_terrain.py` 用 SRTM 的地形起伏把河道分成 delta_plain/lowland/foothill/
mountain，再查 `SLOPE_TABLE` 得到坡降。**阈值是从 15 个样本的自然断点取的，
属于经验标定** —— 而"分类正确"和"坡降对得上"是两件事，必须独立验证。

验证方法
--------
不用地形起伏（那是被验证对象本身），改用**沿河道中心线的高程梯度**：

  1. 从 HydroRIVERS 取河道中心线（独立的第三方数据集，非本仓库产物）
  2. 沿中心线等距采样高程
  3. 对（累计距离, 高程）做线性回归，斜率即纵坡
  4. 与分类器给出的坡降对比

**回归而非首尾两点相减**：单点高程噪声 ±5~10 m，两点相减误差被距离除以后
仍然可观；回归用上全部采样点，噪声被摊薄。

数据源
------
- 河道中心线: HydroRIVERS v1.0 (HydroSHEDS / WWF, CC-BY 4.0)
  亚洲区 shapefile 约 90 MB，**不在仓库内**，需先下载：
      curl -L -o HydroRIVERS_v10_as_shp.zip \
        https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip
- 高程: OpenTopoData 公共 API（SRTM 90m 与 ETOPO1 两套，互为交叉核对）

⚠️ 已知局限（必须在结论中如实标注）
------------------------------------
1. **平原河道量不出来**。SRTM 垂直精度约 ±5~10 m，而平原河 50 km 的落差
   可能只有 5~10 m —— 信噪比小于 1。因此本脚本**只能验证山区/丘陵类**，
   平原类会因噪声过大被标为"不可判"。
2. **起伏（分类器输入）与坡降（验证量）同源于 SRTM**，只是量的东西不同
   （横向谷深 vs 纵向梯度）。为缓解这一点，额外用 ETOPO1（NOAA，独立数据源）
   复算一遍，两者一致才采信。

用法:
    python validate_terrain.py --shp <HydroRIVERS_asia.shp> [--max-km 60]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_terrain import API, RIVERS, classify  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "terrain_validation.json")

# 分类器给出的坡降（与 river_model/regional_defaults.SLOPE_TABLE 的 river 行一致）
SLOPE_BY_CLASS = {
    "delta_plain": 0.00010,
    "lowland": 0.00015,
    "foothill": 0.00050,
    "mountain": 0.00300,
}

SRTM = API                      # .../v1/srtm90m
ETOPO = "https://api.opentopodata.org/v1/etopo1"


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_elevations(points, dataset_url, chunk=100, pause=1.3):
    """批量取高程。points 为 [(lat, lon), ...]，返回等长列表（失败处为 None）。"""
    out = []
    for i in range(0, len(points), chunk):
        part = points[i:i + chunk]
        locs = "|".join(f"{a},{b}" for a, b in part)
        req = urllib.request.Request(f"{dataset_url}?locations={locs}",
                                     headers={"User-Agent": "terrain-validate/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode())
            out.extend([x.get("elevation") for x in d.get("results", [])])
        except Exception as e:
            print(f"      高程请求失败: {type(e).__name__}: {e}")
            out.extend([None] * len(part))
        time.sleep(pause)   # 公开 API 限速 1 次/秒
    return out


def linreg_slope(dist_km, elev_m):
    """对（距离, 高程）做最小二乘，返回 (斜率, R²)。长度按 km，斜率即 m/km。"""
    n = len(dist_km)
    if n < 3:
        return None, None
    mx = sum(dist_km) / n
    my = sum(elev_m) / n
    sxx = sum((x - mx) ** 2 for x in dist_km)
    sxy = sum((x - mx) * (y - my) for x, y in zip(dist_km, elev_m))
    if sxx <= 0:
        return None, None
    slope = sxy / sxx
    ss_tot = sum((y - my) ** 2 for y in elev_m)
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(dist_km, elev_m))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return slope / 1000.0, r2     # 转成 m/m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shp", required=True, help="HydroRIVERS 亚洲区 shapefile 路径")
    ap.add_argument("--max-km", type=float, default=60.0, help="沿河累计长度上限")
    ap.add_argument("--step-km", type=float, default=4.0, help="采样间距")
    args = ap.parse_args()

    try:
        import geopandas as gpd
    except ImportError:
        print("需要 geopandas 才能读 HydroRIVERS shapefile")
        return 1

    print(f"读取 HydroRIVERS: {args.shp}")
    gdf = gpd.read_file(args.shp)
    print(f"  共 {len(gdf)} 条河段")
    by_id = {int(r.HYRIV_ID): r for r in gdf.itertuples()}

    # 取河道中心线的代表点：用几何中点
    def geom_points(geom):
        if geom is None or geom.is_empty:
            return []
        if geom.geom_type == "LineString":
            return [(y, x) for x, y in geom.coords]
        if geom.geom_type == "MultiLineString":
            pts = []
            for g in geom.geoms:
                pts.extend([(y, x) for x, y in g.coords])
            return pts
        return []

    results = {}
    for name, lat, lon, prior in RIVERS:
        print(f"\n--- {name} ({lat}, {lon}) ---")

        # 1. 找最近的河段，取 Strahler 级数最高者（主河道）。
        #    用空间索引按 bbox 取候选 —— 亚洲区有上百万条河段，
        #    对全表算 centroid 会慢到不可用。
        half = 0.3   # 约 33 km 的经纬度窗口
        cand = gdf.cx[lon - half:lon + half, lat - half:lat + half]
        if cand.empty:
            print(f"    窗口内无河段，跳过")
            continue
        cen = cand.geometry.centroid
        d2 = (cen.y - lat) ** 2 + ((cen.x - lon) * math.cos(math.radians(lat))) ** 2
        cand = cand.assign(_d2=d2.values)
        best = cand.sort_values(["ORD_STRA", "_d2"], ascending=[False, True]).iloc[0]
        off_km = math.sqrt(float(best["_d2"])) * 111.0
        print(f"    最近主河道: HYRIV_ID={int(best.HYRIV_ID)} "
              f"级数={int(best.ORD_STRA)} 距采样点 {off_km:.1f} km "
              f"长度 {best.LENGTH_KM:.1f} km")
        if off_km > 30:
            print("    距河道过远（>30 km），跳过")
            continue

        # 2. 沿 NEXT_DOWN 向下游串成链，累计到 max_km
        chain, cur, total = [], int(best.HYRIV_ID), 0.0
        seen = set()
        while cur in by_id and cur not in seen and total < args.max_km:
            seen.add(cur)
            row = by_id[cur]
            chain.append(row)
            total += float(row.LENGTH_KM)
            nxt = int(row.NEXT_DOWN)
            if nxt <= 0:
                break
            cur = nxt
        print(f"    串联 {len(chain)} 条河段，累计 {total:.1f} km")

        # 3. 沿链等距取点
        pts, dk = [], []
        acc = 0.0
        for row in chain:
            gp = geom_points(row.geometry)
            if not gp:
                continue
            # 该段的起止（HydroRIVERS 的坐标顺序即流向，从上游到下游）
            seg_len = float(row.LENGTH_KM)
            n_sub = max(1, int(seg_len / args.step_km))
            for k in range(n_sub):
                f = k / n_sub
                i = min(int(f * (len(gp) - 1)), len(gp) - 1)
                la, lo = gp[i]
                pts.append((round(la, 5), round(lo, 5)))
                dk.append(acc + f * seg_len)
            acc += seg_len
        if len(pts) < 5:
            print("    采样点不足，跳过")
            continue
        print(f"    采样 {len(pts)} 点，跨度 {dk[-1]:.1f} km")

        # 4. 取高程并回归
        e_srtm = fetch_elevations(pts, SRTM)
        e_etopo = fetch_elevations(pts, ETOPO)
        valid = [(d, a, b) for d, a, b in zip(dk, e_srtm, e_etopo)
                 if a is not None and b is not None]
        if len(valid) < 5:
            print("    有效高程不足，跳过")
            continue
        d_v = [x[0] for x in valid]
        s_srtm, r2_s = linreg_slope(d_v, [x[1] for x in valid])
        s_etopo, r2_e = linreg_slope(d_v, [x[2] for x in valid])

        results[name] = {
            "lat": lat, "lon": lon, "prior": prior,
            "reaches": len(chain), "span_km": round(dk[-1], 1),
            "n_samples": len(valid),
            "slope_srtm": None if s_srtm is None else round(s_srtm, 6),
            "slope_etopo": None if s_etopo is None else round(s_etopo, 6),
            "r2_srtm": None if r2_s is None else round(r2_s, 3),
            "r2_etopo": None if r2_e is None else round(r2_e, 3),
            "drop_m_srtm": round(valid[0][1] - valid[-1][1], 1),
        }
        # ⚠️ 符号约定：x=累计距离(km)，y=高程(m)，高程沿程下降 ⇒ 回归斜率为负。
        #    河道**坡降**取其绝对值。首版报告里没取绝对值，一度看起来像"往高处流"。
        A, B = abs(s_srtm), abs(s_etopo)
        hi, lo = max(A, B), min(A, B)
        agree = (lo > 0) and (hi / lo <= 2.0)
        print(f"    SRTM  纵坡={A:.6f}  R²={r2_s:.3f}  首尾落差={valid[0][1]-valid[-1][1]:.1f} m")
        print(f"    ETOPO 纵坡={B:.6f}  R²={r2_e:.3f}   两源同量级: {'是' if agree else '否'}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"generated": time.strftime("%Y-%m-%d"),
                   "source_centerline": "HydroRIVERS v1.0 (CC-BY 4.0)",
                   "source_elevation": "OpenTopoData srtm90m / etopo1",
                   "max_km": args.max_km, "rivers": results},
                  f, ensure_ascii=False, indent=2)
    print(f"\n已写出 {OUT}（{len(results)} 条河）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
