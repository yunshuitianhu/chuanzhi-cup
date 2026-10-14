# -*- coding: utf-8 -*-
"""HEC-RAS COM 探针 —— 验证能否驱动 HEC-RAS 完成一次【非恒定流】计算。

背景
----
`river_model/hecras_bridge.py:300` 的文档字符串写着：
    "当前 HEC-RAS 7.0 非恒定流 plan 格式未完全兼容 — 只跑稳态流初始化。
     非恒定流结果由 hydro_engine.run_hydrodynamic() 降级到纯 Python 求解。"

也就是说：非恒定流从未在 HEC-RAS 里跑起来过，全部降级给了那个质量不守恒
（残差 103%）的纯 Python 求解器。本探针要回答的问题是：
**这个"不兼容"是真的做不到，还是只是 .p01 文件少了几个关键字？**

调用签名照抄 hecras_bridge.py 的可用写法（`Compute_CurrentPlan()` 不带参数；
带参数会报 SAFEARRAY 错误）。

只操作**副本**目录，不触碰任何原始工程。

用法:
    python probe_hecras_com.py [工程目录]
"""
import os
import sys
import time

PROJ_DIR = sys.argv[1] if len(sys.argv) > 1 else r"D:\hec-ras_1\_chuanzhi_probe"
PRJ = os.path.join(PROJ_DIR, "Yayao.prj")
HDF = os.path.join(PROJ_DIR, "Yayao.p01.hdf")

STEP_TIMEOUT = 90          # 单步 COM 调用超时预估
COMPUTE_TIMEOUT = 300      # 计算轮询超时


def scan_hdf(path):
    """返回 (是否有非恒定流结果, 分组统计)。"""
    if not os.path.exists(path):
        return None, {}
    try:
        import h5py
    except ImportError:
        return None, {}
    with h5py.File(path, "r") as f:
        groups = set()

        def walk(name, obj):
            if isinstance(obj, h5py.Dataset):
                parts = name.split("/")
                for i in range(1, len(parts)):
                    groups.add("/".join(parts[:i]))
        f.visititems(walk)
        has_un = any("nsteady" in g for g in groups)
        ste = sorted(g for g in groups if "teady" in g and g.count("/") <= 2)
        return has_un, {"unsteady_paths": sorted(g for g in groups if "nsteady" in g)[:8],
                        "results_branches": sorted(g for g in groups
                                                   if g.startswith("Results/")
                                                   and g.count("/") <= 2)}


def main():
    import win32com.client

    print("=" * 72)
    print("HEC-RAS COM 探针 —— 目标：跑通非恒定流")
    print("=" * 72)
    print(f"工程目录 : {PROJ_DIR}")
    print(f"工程文件 : {PRJ}  存在={os.path.exists(PRJ)}")
    if not os.path.exists(PRJ):
        return 1

    before_mtime = os.stat(HDF).st_mtime if os.path.exists(HDF) else None
    has_un_before, info_before = scan_hdf(HDF)
    print(f"\n计算前 HDF:")
    print(f"  含非恒定流结果: {has_un_before}")
    print(f"  Results 分支  : {info_before.get('results_branches')}")

    print("\n[1] 实例化 RAS70.HECRASController ...")
    rc = win32com.client.Dispatch("RAS70.HECRASController")
    print("    OK")

    # 隐藏计算窗口（照抄 hecras_bridge.py 的可用写法）
    for meth in ("Compute_HideComputationWindow",):
        try:
            getattr(rc, meth)()
            print(f"    {meth}() OK")
        except Exception as e:
            print(f"    {meth}() 失败: {type(e).__name__}: {e}")

    print(f"\n[2] Project_Open ...")
    t0 = time.time()
    rc.Project_Open(PRJ)
    print(f"    OK ({time.time() - t0:.1f}s)")

    print("\n[3] 探查几何与方案 ...")
    try:
        n_xs = rc.Schematic_XSCount()
        print(f"    断面数 Schematic_XSCount() = {n_xs}")
    except Exception as e:
        print(f"    Schematic_XSCount() 失败: {e}")
        n_xs = None

    plan_names = None
    try:
        plan_names = rc.Plan_Names()
        print(f"    Plan_Names() = {plan_names!r}")
    except Exception as e:
        print(f"    Plan_Names() 失败: {type(e).__name__}: {e}")

    if plan_names:
        try:
            cur = plan_names[1][0] if len(plan_names) > 1 and plan_names[1] else None
            print(f"    取第一个方案名: {cur!r}")
            if cur:
                rc.Plan_SetCurrent(cur)
                print(f"    Plan_SetCurrent OK")
        except Exception as e:
            print(f"    Plan_SetCurrent 失败: {type(e).__name__}: {e}")

    print("\n[4] Compute_CurrentPlan()  —— 注意：不带参数")
    t0 = time.time()
    try:
        rc.Compute_CurrentPlan()
        print(f"    调用返回 ({time.time() - t0:.1f}s)")
    except Exception as e:
        print(f"    抛错: {type(e).__name__}: {e}")
        _cleanup(rc)
        return 3

    print(f"\n[5] 轮询 Compute_Complete()，上限 {COMPUTE_TIMEOUT}s ...")
    done = False
    for i in range(COMPUTE_TIMEOUT):
        time.sleep(1.0)
        try:
            if rc.Compute_Complete():
                done = True
                print(f"    完成，实际耗时 {i + 1}s")
                break
        except Exception as e:
            print(f"    轮询出错: {type(e).__name__}: {e}")
            break
        if i and i % 30 == 0:
            print(f"    ... 已等待 {i}s")
    if not done:
        print(f"    >>> 未在 {COMPUTE_TIMEOUT}s 内完成")
        try:
            rc.Compute_Cancel()
            print("    已 Compute_Cancel()")
        except Exception:
            pass

    _cleanup(rc)

    after_mtime = os.stat(HDF).st_mtime if os.path.exists(HDF) else None
    has_un_after, info_after = scan_hdf(HDF)
    print(f"\n计算后 HDF:")
    print(f"  含非恒定流结果: {has_un_after}")
    print(f"  Results 分支  : {info_after.get('results_branches')}")
    if info_after.get("unsteady_paths"):
        for g in info_after["unsteady_paths"]:
            print(f"      {g}")
    print(f"  文件时间戳变化: {before_mtime != after_mtime}")

    print("\n" + "=" * 72)
    if has_un_after and not has_un_before:
        print("结论: 非恒定流【跑通了】—— 之前的'不兼容'判断可能是 .p01 缺关键字")
    elif has_un_after:
        print("结论: 非恒定流结果存在（计算前就有）")
    else:
        print("结论: 仍未产生非恒定流结果 —— 需要检查 .p01 / .u01 格式")
    print("=" * 72)
    return 0


def _cleanup(rc):
    print("\n[6] QuitRas() ...")
    try:
        rc.QuitRas()
        print("    OK")
    except Exception as e:
        print(f"    失败: {type(e).__name__}: {e}")


if __name__ == "__main__":
    sys.exit(main())
