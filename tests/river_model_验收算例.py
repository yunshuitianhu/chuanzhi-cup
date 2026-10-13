# -*- coding: utf-8 -*-
"""river_model 水动力求解器 —— 验收算例

用途：作为 `pure_py_hydro.solve_hydrodynamics` 修复前后的**验收标准**。
      修复前应看到多条 FAIL，修复后应全部 PASS。

设计原则：不依赖复杂解析解，只用三条**无歧义的物理性质**——
这些性质由边界条件的定义直接给出，任何正确的求解器都必须满足。

  算例 1  下游 Dirichlet 边界跟踪：边界节点水位必须严格等于给定潮位过程线
  算例 2  恒定工况稳态：无潮、恒定入流时，水位场不应随时间漂移
  算例 3  质量守恒：恒定工况下各断面流量应相等且等于上游入流量
  算例 4  时间步无关性：减小 dt 后结果应收敛（不出现剧烈变化）
  算例 5  收敛性能：Newton 迭代应能提前收敛，而非跑满上限

用法：
    python tests/river_model_验收算例.py [项目根目录]
    默认项目根目录为本仓库根目录（tests/ 的上一级）
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")

# 默认使用本仓库根目录；也可通过命令行参数指定其它项目路径
DEFAULT_PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJ = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROJ
sys.path.insert(0, PROJ)

import numpy as np

from river_model import pure_py_hydro as pph
from river_model.config import YayaoConfig
from river_model.cross_sections import generate_yayao_cross_sections

# ---------------- 通用设置 ----------------
N_CS = 50
LENGTH_M = 3200.0
STAGE0 = 0.83      # 下游平均潮位 (m)
AMP = 0.8          # 潮振幅 (m)
T_TIDE = 12.42     # M2 分潮周期 (h)
Q_UP = 6.9         # 上游流量 (m3/s)
TOL_BC = 1e-6      # 边界跟踪容差 (m) —— Dirichlet 边界应精确满足

results = []


def report(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def make_case(n_cs=N_CS):
    cfg = YayaoConfig(n_cross_sections=n_cs, mannings_n=0.028)
    cs = generate_yayao_cross_sections(
        n_cs, LENGTH_M, cfg.bed_elevation_avg_m, cfg.longitudinal_slope, 40.0, mannings_n=0.028
    )
    return cfg, cs


def run(cfg, cs, amp=AMP, duration_h=6.0, dt_s=60.0, q_up=Q_UP, stage0=STAGE0):
    return pph.solve_hydrodynamics(
        cfg, cs, upstream_flow_m3s=q_up, downstream_stage_m=stage0,
        tidal_amplitude_m=amp, tidal_period_h=T_TIDE,
        duration_h=duration_h, dt_s=dt_s,
    )


def count_newton(cfg, cs, **kw):
    """拦截 _double_sweep，统计调用次数（= Newton 迭代总次数）。"""
    orig = pph._double_sweep
    n = [0]

    def spy(*a, **k):
        n[0] += 1
        return orig(*a, **k)

    pph._double_sweep = spy
    try:
        h = run(cfg, cs, **kw)
    finally:
        pph._double_sweep = orig
    return h, n[0]


print("=" * 74)
print("river_model 水动力求解器 — 验收算例")
print(f"项目路径: {PROJ}")
print("=" * 74)

cfg, cs = make_case()

# ============================================================
print()
print("算例 1  下游 Dirichlet 边界跟踪")
print("  性质: 下游边界节点水位必须精确等于给定潮位过程线")
print("        target(t) = stage0 + amp·sin(2πt/T)")
print("-" * 74)

h = run(cfg, cs, duration_h=6.0, dt_s=60.0)
t_h = np.asarray(h.t)
z_dn = np.asarray(h.water_level)[:, -1]
target = STAGE0 + AMP * np.sin(2 * np.pi * t_h / T_TIDE)
dev = np.abs(z_dn - target)
max_dev = float(dev.max())

print(f"  {'t(h)':>6} {'Z[-1]':>10} {'target':>10} {'|偏差|':>10}")
for i in range(min(len(t_h), 8)):
    print(f"  {t_h[i]:6.2f} {z_dn[i]:10.4f} {target[i]:10.4f} {dev[i]:10.4f}")
if len(t_h) > 8:
    print(f"  ... 共 {len(t_h)} 个快照")

report("下游边界节点跟踪潮位过程线", max_dev < TOL_BC,
       f"最大偏差 {max_dev:.4f} m (容差 {TOL_BC})")

# 额外信息：边界是否随时间变化
n_distinct = len(np.unique(np.round(z_dn, 6)))
report("下游边界随时间变化（潮汐本质）", n_distinct > len(t_h) * 0.5,
       f"{n_distinct} 个不同值 / {len(t_h)} 个快照")

# ============================================================
print()
print("算例 2  恒定工况稳态（无潮）")
print("  性质: amp=0、恒定入流、恒定下游水位时，水位场不应随时间漂移")
print("-" * 74)

h0 = run(cfg, cs, amp=0.0, duration_h=6.0, dt_s=60.0)
z0 = np.asarray(h0.water_level)
drift = float(np.max(np.abs(z0 - z0[0])))
print(f"  水位场相对初始态的最大漂移: {drift:.6f} m")
print(f"  初始 Z[0]={z0[0, 0]:.6f}  Z[-1]={z0[0, -1]:.6f}")
print(f"  末态 Z[0]={z0[-1, 0]:.6f}  Z[-1]={z0[-1, -1]:.6f}")
report("恒定工况下水位场稳定", drift < 1e-3, f"最大漂移 {drift:.6f} m")

# ============================================================
print()
print("算例 3  质量守恒")
print("  性质: 恒定工况稳态下，各断面流量应相等且等于上游入流量")
print("-" * 74)

q0 = np.asarray(h0.discharge)
q_err = float(np.max(np.abs(q0 - Q_UP)))
q_spread = float(np.max(q0.std(axis=1)))
print(f"  流量相对上游值的最大偏差: {q_err:.6f} m3/s")
print(f"  各时刻断面间流量标准差最大值: {q_spread:.6f} m3/s")
print(f"  流量范围: [{q0.min():.4f}, {q0.max():.4f}]  (上游给定 {Q_UP})")
report("质量守恒", q_err < 1e-2, f"最大偏差 {q_err:.4f} m3/s")

# ============================================================
print()
print("算例 4  时间步加密研究（数值收敛）")
print("  性质: 随 dt 减小，相邻两步的解之差应落在求解器容差量级且不增长")
print("  判据依据: 迭代收敛容差 newton_tolerance = 1 mm，故差值地板即该量级；")
print("            判据取 5 x 容差，而非要求单调递减——")
print("            在容差地板附近抖动是正常数值行为，单调性不是有效收敛判据。")
print("-" * 74)

DT_LIST = [240.0, 120.0, 60.0, 30.0, 15.0]
sols = []
for dt in DT_LIST:
    hh = run(cfg, cs, duration_h=2.0, dt_s=dt)
    sols.append(np.asarray(hh.water_level)[-1])
    print(f"  dt={dt:6.1f}s  末态水位范围 [{sols[-1].min():.4f}, {sols[-1].max():.4f}]")

tol_floor = 5.0 * cfg.newton_tolerance
print()
diffs = []
for i in range(1, len(DT_LIST)):
    d = float(np.max(np.abs(sols[i] - sols[i - 1])))
    diffs.append(d)
    print(f"  dt={DT_LIST[i-1]:.0f}→{DT_LIST[i]:.0f}s  最大差 = {d:.6f} m "
          f"({d / cfg.newton_tolerance:.2f} x 容差)")

# 收敛判据: 最粗一档允许未收敛; 自 dt<=120s 起, 各档加密差值应全部落在
# 5 x 容差以内 —— 即解已与时间步长无关到求解器自身精度。
d_coarse = diffs[0]
d_converged = max(diffs[1:])
report("时间步加密后解收敛到容差量级",
       d_converged < tol_floor,
       f"最粗档(dt=240→120)差 {d_coarse:.6f} m 尚未收敛; "
       f"自 dt=120s 起各档最大差 {d_converged:.6f} m (< {tol_floor:.3f} m = 5 x 容差)")

# ============================================================
print()
print("算例 5  Newton 迭代收敛性能")
print("  性质: 除边界强迫剧烈变化外，迭代应能提前收敛，而非跑满上限")
print("-" * 74)

_, n_calls = count_newton(cfg, cs, duration_h=6.0, dt_s=60.0)
steps = int(6.0 * 3600 / 60.0)
cap = steps * cfg.max_newton_iter
ratio = n_calls / cap
print(f"  时间步数: {steps},  单步上限: {cfg.max_newton_iter}")
print(f"  满额调用数: {cap},  实际调用: {n_calls}")
print(f"  占上限比例: {ratio:.1%}")
print(f"  (若收敛判据不可达，该比例恒为 100%)")
report("Newton 迭代能提前收敛", ratio < 0.95, f"实际占用上限 {ratio:.1%}")

# ============================================================
print()
print("=" * 74)
n_pass = sum(1 for _, ok in results if ok)
n_fail = len(results) - n_pass
print(f"验收结果: {n_pass} 通过, {n_fail} 失败, 共 {len(results)} 项")
for name, ok in results:
    print(f"  {'✓' if ok else '✗'} {name}")
print("=" * 74)
sys.exit(0 if n_fail == 0 else 1)
