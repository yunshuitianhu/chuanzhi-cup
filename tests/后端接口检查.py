# -*- coding: utf-8 -*-
"""后端接口回归检查

重点覆盖一个**真实发生过的缺陷**：后端不回传会话标识，导致前端每个请求
都新建会话 —— 表现为「数据上传成功，但状态始终是空的、按钮点不动」。
上传接口返回了 1805 条，紧接着查状态却是 0 条。

根因是 HTTP 无状态：会话靠前端在 X-Session-Id 请求头里带回来，
而后端从不告诉前端"这次用的是哪个会话"。修复见 backend/main.py 的
session_middleware。

用法：
    python tests/后端接口检查.py
"""
import logging
import os
import sys
import warnings

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJ)
sys.path.insert(0, PROJ)

from fastapi.testclient import TestClient

from backend.main import app

SAMPLE = (sys.argv[1] if len(sys.argv) > 1 else
          r"C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx")

results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


c = TestClient(app)

print("=" * 72)
print("后端接口检查")
print("=" * 72)

# ---------- 1. 每个响应都必须回传会话 id ----------
print()
print("1. 会话标识的回传（这是此前出问题的地方）")
sid_health = c.get("/api/health").headers.get("X-Session-Id")
check("/api/health 响应含 X-Session-Id", bool(sid_health),
      f"{sid_health[:8]}…" if sid_health else "缺失")

r = c.post("/api/config/target-class", json={"target_water_class": "V"})
check("POST 响应也含 X-Session-Id", bool(r.headers.get("X-Session-Id")))

r = c.get("/api/analysis/summary")           # 故意触发 400
check("错误响应也含 X-Session-Id", bool(r.headers.get("X-Session-Id")),
      f"HTTP {r.status_code}")

# ---------- 2. 会话跨请求保持 ----------
print()
print("2. 会话跨请求保持")
H = {"X-Session-Id": sid_health}

if not os.path.isfile(SAMPLE):
    check("样本文件存在", False, SAMPLE)
else:
    with open(SAMPLE, "rb") as f:
        up = c.post("/api/data/upload", headers=H,
                    files={"file": (os.path.basename(SAMPLE), f.read(),
                                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    rows = up.json().get("rows", 0)
    check("上传成功", up.status_code == 200 and rows > 0, f"{rows} 条")

    # 关键：**不带 session id** 地再查一次，应当拿不到数据 —— 证明会话确实靠 id 隔离
    st_no_id = c.get("/api/data/status").json()
    check("不带会话 id 时查不到数据（会话确实隔离）", not st_no_id["has_data"],
          f"has_data={st_no_id['has_data']}")

    st = c.get("/api/data/status", headers=H).json()
    check("带会话 id 时数据仍在", st["has_data"] and st["rows"] == rows,
          f"rows={st['rows']}")

    # 分析结果也要跟着会话走
    bs = c.post("/api/analysis/black-spots", headers=H).json()
    n = bs["result"].get("黑点总数", 0)
    check("黑臭检测在会话内可执行", n > 0, f"黑点 {n} 个")

    st2 = c.get("/api/data/status", headers=H).json()
    check("分析结果写回会话", st2["black_spot_count"] == n,
          f"状态里 {st2['black_spot_count']} vs 检测 {n}")

    # 聚类字段清单必须来自真实产出（前端据此渲染，避免硬编码字段名）
    cf = bs.get("cluster_fields") or []
    check("返回聚类字段清单且非空", len(cf) > 0, f"{len(cf)} 个字段")

# ---------- 3. 图表接口 ----------
print()
print("3. 图表接口（返回 JSON 数据而非图片）")
r = c.get("/api/charts/time-series", headers=H)
if r.status_code == 200:
    ts = r.json()
    check("时序数据含时间轴与面板", len(ts["time"]) > 0 and len(ts["panels"]) > 0,
          f"{len(ts['time'])} 点 / {len(ts['panels'])} 面板")
    check("黑点散点已附带", len(ts["black_spots"]) > 0, f"{len(ts['black_spots'])} 个")
    check("数值中无 NaN/Inf（JSON 不允许）",
          all(v is None or isinstance(v, (int, float))
              for p in ts["panels"] for v in p["values"][:200]))
else:
    check("时序接口可用", False, f"HTTP {r.status_code}")

r = c.get("/api/charts/trace-map", headers=H)
if r.status_code == 200:
    tm = r.json()
    check("地图数据含轨迹/聚类/Top5",
          len(tm["track"]) > 0 and len(tm["clusters"]) > 0 and len(tm["top5"]) > 0,
          f"轨迹 {len(tm['track'])} / 聚类 {len(tm['clusters'])} / Top5 {len(tm['top5'])}")
else:
    check("地图接口可用", False, f"HTTP {r.status_code}")

# ---------- 4. 参数校验 ----------
print()
print("4. 参数校验")
r = c.post("/api/config/target-class", headers=H, json={"target_water_class": "X"})
check("非法水质等级被拒绝", r.status_code == 200 and not r.json().get("ok"),
      str(r.json().get("error"))[:40])
r = c.post("/api/config/target-class", headers=H, json={"target_water_class": "III"})
check("切换等级会清空分析结果", r.json().get("analysis_reset") is True)
st3 = c.get("/api/data/status", headers=H).json()
check("切换后黑点已清除", st3["black_spot_count"] == 0,
      f"black_spot_count={st3['black_spot_count']}")
c.post("/api/config/target-class", headers=H, json={"target_water_class": "V"})

print()
print("=" * 72)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
print("=" * 72)
sys.exit(0 if n_fail == 0 else 1)
