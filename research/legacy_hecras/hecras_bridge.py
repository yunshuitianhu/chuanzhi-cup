r"""HEC-RAS 桥接模块 — 生成输入文件 + COM 驱动计算 + 提取结果。

HEC-RAS 7.0 安装于 D:\my_HEC-RAS。COM 需管理员运行注册脚本后可用。
接口: win32com.client.Dispatch("RAS70.HECRASController")
几何文件格式精确匹配 HEC-RAS 7.0 原生输出。
"""

import gc
import logging
import os
import shutil
import tempfile
import threading
import time
from datetime import datetime, timedelta
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

from river_model.config import YayaoConfig
from river_model.cross_sections import CrossSection
from river_model.state import HydroResult


# HEC-RAS 工程内的河名/河段名。几何文件、非恒定流边界文件、稳态流文件
# 三处必须完全一致，否则 HEC-RAS 找不到边界所属的河道。
# 原先这三处各自硬编码字面量，改名时极易漏改其中一处。
_RIVER_NAME = "Yayao"
_REACH_NAME = "Main"
_PROJECT_STEM = "Yayao"   # 文件名前缀（.prj/.g01/.u01/.p01/.f01/.p01.hdf）

# `QuitRas()` 之后留给对话框守护的宽限期（秒）。
# 见 `run_hecras_simulation` 里该处的注释：保存询问常常在 QuitRas 返回之后才弹，
# 立刻退出守护就会漏掉它。
_QUIT_GRACE_SEC = 3.0


def _rmtree_with_retry(path: str, attempts: int = 5, delay: float = 1.0) -> bool:
    """删除目录，失败则重试。返回是否成功。

    **为什么要重试**：`ras.QuitRas()` 返回时 HEC-RAS 进程可能尚未完全退出，
    文件句柄仍被占用，此时 `shutil.rmtree` 会失败。

    实测教训：第一版直接写 `shutil.rmtree(path, ignore_errors=True)`，
    结果**目录照样残留** —— `ignore_errors=True` 把"文件被占用"这个失败
    静默吞掉了，看起来像修好了，实际没有。所以这里失败必须留痕。
    """
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except OSError as e:
            if i == attempts - 1:
                # 不抛出 —— 清理失败不该掩盖真正的业务错误；
                # 但也绝不静默 —— 否则就成了上面那个教训的重演。
                logger.warning("临时目录清理失败（将残留）: %s: %s", path, e)
                return False
            time.sleep(delay)
    return False


def _flow_table(values, fmt: str = "{:8.2f}") -> str:
    """HEC-RAS 数值表排版：每值 8 字符右对齐，每行 10 个（80 列）。

    这是 HEC-RAS 原生写出的格式，实测按其排版才能被正确解析。
    """
    lines = []
    for i in range(0, len(values), 10):
        lines.append("".join(fmt.format(v) for v in values[i:i + 10]))
    return "\n".join(lines)


def _boundary_location(rm) -> str:
    """HEC-RAS 非恒定流边界行的 9 字段格式。

    格式: 河名,河段名,断面号,下游断面号,蓄水区连接,蓄水区名,泵站名,保留,保留

    实测教训：只写一个断面号（如 `Boundary Location=3200.000`）会被
    HEC-RAS 当成**河名**，河段名与断面号留空，于是上下游边界全部识别不到，
    报 "needs a upstream/downstream boundary condition"。
    """
    return (f"Boundary Location={_RIVER_NAME},{_REACH_NAME},{rm}"
            ",,,,,,")


def _get_hecras_com():
    """获取 HEC-RAS COM 控制器实例。"""
    import win32com.client
    return win32com.client.Dispatch("RAS70.HECRASController")


def is_hecras_available() -> bool:
    path = r"D:\my_HEC-RAS\Ras.exe"
    return os.path.exists(path)


# ---- HEC-RAS 输入文件生成 ----

_PRJ_GEOM_ONLY = """Proj Title={title}
Current Geom=g01
Default Exp/Contr=0.3,0.1
SI Units
Geom File=g01
Y Axis Title=Elevation
X Axis Title(PF)=Main Channel Distance
X Axis Title(XS)=Station
BEGIN DESCRIPTION:

END DESCRIPTION:
DSS Start Date=
DSS Start Time=
DSS End Date=
DSS End Time=
DSS Export Filename=
DSS Export Rating Curves= 0
DSS Export Rating Curve Sorted= 0
DSS Export Volume Flow Curves= 0
DXF Filename=
DXF OffsetX= 0
DXF OffsetY= 0
DXF ScaleX= 1
DXF ScaleY= 10
GIS Export Profiles= 0
"""

_PRJ_FULL = """Proj Title={title}
Current Plan=p01
Current Geom=g01
Current Flow=f01
Default Exp/Contr=0.3,0.1
SI Units
Geom File=g01
Unsteady File=u01
Plan File=p01
Flow File=f01
Y Axis Title=Elevation
X Axis Title(PF)=Main Channel Distance
X Axis Title(XS)=Station
BEGIN DESCRIPTION:

END DESCRIPTION:
DSS Start Date=
DSS Start Time=
DSS End Date=
DSS End Time=
DSS Export Filename=
DSS Export Rating Curves= 0
DSS Export Rating Curve Sorted= 0
DSS Export Volume Flow Curves= 0
DXF Filename=
DXF OffsetX= 0
DXF OffsetY= 0
DXF ScaleX= 1
DXF ScaleY= 10
GIS Export Profiles= 0
"""


def _build_project_file(work_dir: str, title: str = _PROJECT_STEM,
                        include_flow_plan: bool = False) -> str:
    path = os.path.join(work_dir, f"{_PROJECT_STEM}.prj")
    tmpl = _PRJ_FULL if include_flow_plan else _PRJ_GEOM_ONLY
    with open(path, "w", encoding="utf-8") as f:
        f.write(tmpl.format(title=title))
    return path


def _build_geometry_file(work_dir: str, config: YayaoConfig,
                         cross_sections: List[CrossSection]) -> str:
    """生成 HEC-RAS 7.0 几何文件 (.g01) — 精确匹配 HEC-RAS 7.0 原生格式。"""
    n = len(cross_sections)

    lines = [
        f"Geom Title={_RIVER_NAME}",
        "Program Version=7.00",
        "Viewing Rectangle= 0 , 1 , 1 , 0 ",
        "",
        # 河名/河段名必须与 .u01 的 Boundary Location 完全一致，
        # 否则 HEC-RAS 找不到边界所属河道（统一取模块常量）
        f"River Reach={_RIVER_NAME:<18},{_REACH_NAME:<16}",
        "Reach XY= 2 ",
        "-0.20228215767630.726141078838170.567427385892120.56224066390042",
        "Rch Text X Y=-0.0098548,0.685166",
        "Reverse River Text= 0 ",
        "",
    ]

    for i, cs in enumerate(cross_sections):
        # HEC-RAS River Station = 上游大, 下游小
        station = int(config.length_m - cs.chainage_m)
        station = max(station, 0)

        if i < n - 1:
            dx = int(round(cross_sections[i + 1].chainage_m - cs.chainage_m))
        else:
            dx = 50

        # Type RM line — EXACT HEC-RAS 7.0 format
        lines.append(f"Type RM Length L Ch R = 1 ,{station:<8d},{dx},{dx},{dx}")
        lines.append(f"Node Last Edited Time=May/27/2026 00:00:00")

        s = cs.stations
        n_pts = len(s)
        lines.append(f"#Sta/Elev= {n_pts} ")

        # Sta/Elev: 8-char fixed-width, sta without decimals, el with 1 decimal
        vals = ""
        for j in range(n_pts):
            vals += f"{float(s[j, 0]):8.0f}{float(s[j, 1]):8.1f}"
        lines.append(vals)

        # Manning n — 3 triplets of (station, n_value, 0), n without leading zero
        lines.append("#Mann= 3 ,0,0")
        left_edge = int(round(float(s[0, 0])))
        left_bank = int(round(float(s[1, 0]))) if n_pts >= 4 else left_edge + 5
        right_bank = int(round(float(s[-2, 0]))) if n_pts >= 4 else -left_edge - 5
        def _fmt_n(v):
            s = f"{v:.3f}".lstrip('0')
            return f"{s:>8}"
        lines.append(
            f"{left_edge:8d}" + _fmt_n(cs.mannings_n_left) + f"{0:8d}"
            + f"{left_bank:8d}" + _fmt_n(cs.mannings_n_channel) + f"{0:8d}"
            + f"{right_bank:8d}" + _fmt_n(cs.mannings_n_right) + f"{0:8d}"
        )

        lines.append(f"Bank Sta={left_bank},{right_bank}")
        lines.append("XS Rating Curve= 0 ,0")
        bed_el = float(s[1, 1])
        lines.append(f"XS HTab Starting El and Incr={bed_el + 0.5:.1f},0.15, 20 ")
        lines.append("XS HTab Horizontal Distribution= 5 , 5 , 5 ")
        lines.append("Exp/Cntr=0.3,0.1")
        lines.append("")

    # Footer — exact HEC-RAS 7.0 format
    lines.append("LCMann Time=Dec/30/1899 00:00:00")
    lines.append("LCMann Region Time=Dec/30/1899 00:00:00")
    lines.append("LCMann Table=0")
    lines.append("Chan Stop Cuts=-1 ")
    lines.append("")
    lines.append("")
    lines.append("Use User Specified Reach Order=0")
    lines.append("GIS Ratio Cuts To Invert=-1")
    lines.append("GIS Limit At Bridges=0")
    lines.append("Composite Channel Slope=5")
    lines.append("")

    # 文件名必须是 <ProjectName>.g01 以匹配 PRJ 中的 Geom File=g01
    path = os.path.join(work_dir, f"{_PROJECT_STEM}.g01")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _build_unsteady_flow_file(work_dir: str, config: YayaoConfig,
                               upstream_flow_m3s: float, downstream_stage_m: float,
                               tidal_amplitude_m: float, tidal_period_h: float,
                               duration_h: float, dt_s: float) -> str:
    """生成非恒定流边界条件文件 (.u01)。

    格式要点（均为 HEC-RAS 7.0 实测确认，详见
    research/hydro-wq-model/HEC-RAS实测笔记.md）：
      - `Boundary Location=` 必须是 9 个逗号分隔字段。原先只写一个断面号，
        被 HEC-RAS 当成**河名** → 上下游边界全部识别不到
      - 时间步关键字是 `Interval=`，不是 `Flow Hydrograph Time Step=`
      - 数值表每值 8 字符右对齐、每行 10 个（80 列）
      - 边界桩号取自 config.length_m，不再写死 3200
    """
    T_tide = tidal_period_h * 3600.0

    # HEC-RAS 的 Interval 令牌只支持**整分钟及以上**（1MIN/5MIN/1HOUR…），没有秒级令牌。
    # 因此过程线步长必须取整分钟 —— 这里把请求步长四舍五入到整分钟，
    # 并**按取整后的实际步长重新生成数值**。
    #
    # 为什么不能只改标签：`int(dt_min)` 截断曾导致
    #   dt_s=30 -> 令牌 "0MIN"（非法）
    #   dt_s=90 -> 令牌 "1MIN" 但表里有 81 个值（按 1min 读只有 81 分钟，
    #              而 .p01 写的是 2 小时结束）—— 数值个数与时间跨度对不上。
    dt_min_eff = max(1, int(round(dt_s / 60.0)))
    dt_eff_s = dt_min_eff * 60.0

    total_minutes = int(duration_h * 60)
    n_steps = int(np.ceil(duration_h * 3600 / dt_eff_s)) + 1

    if dt_min_eff < 60:
        interval_tok = f"{dt_min_eff}MIN"
    else:
        interval_tok = f"{dt_min_eff // 60}HOUR"

    # 边界桩号必须落在几何文件实际生成的断面上：
    # 首断面桩号 = length_m（上游），末断面桩号 = 0（下游）
    rm_up = int(config.length_m)
    rm_dn = 0

    # 请求给出恒定上游流量时，不人为叠加没有观测依据的 5% 周期扰动。
    flows = [upstream_flow_m3s for _ in range(n_steps)]
    stages = [downstream_stage_m + tidal_amplitude_m * np.sin(2 * np.pi * m * dt_eff_s / T_tide)
              for m in range(n_steps)]

    if dt_min_eff != dt_s / 60.0:
        logger.info("过程线步长由 %ss 取整为 %d 分钟 —— HEC-RAS 的 Interval "
                    "令牌不支持秒级/非整分钟（数值已按取整后的步长重新生成）",
                    dt_s, dt_min_eff)

    lines = [
        f"Flow Title={_RIVER_NAME}",
        "Program Version=7.00",
        "",
        _boundary_location(rm_up),
        f"Interval={interval_tok}",
        f"Flow Hydrograph= {n_steps} ",
        _flow_table(flows),
        "",
        _boundary_location(rm_dn),
        f"Interval={interval_tok}",
        f"Stage Hydrograph= {n_steps} ",
        _flow_table(stages),
        "",
        f"Initial Flow={upstream_flow_m3s:.4f}",
        f"Initial Stage={downstream_stage_m:.4f}",
    ]

    path = os.path.join(work_dir, f"{_PROJECT_STEM}.u01")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _build_plan_file(work_dir: str, duration_h: float, dt_s: float,
                     output_interval_min: float, unsteady: bool = False) -> str:
    """生成方案；非恒定流显式启用，默认保留原恒定流选择。"""
    def token(seconds):
        allowed_seconds = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30)
        allowed_minutes = (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30, 60)
        if seconds in allowed_seconds:
            return f'{int(seconds)}SEC'
        if seconds / 60 in allowed_minutes:
            return f'{int(seconds / 60)}MIN' if seconds < 3600 else '1HOUR'
        raise ValueError('HEC-RAS 时间间隔不是当前适配器支持的标准令牌')
    if (not np.isfinite(duration_h) or duration_h <= 0
            or abs(duration_h*60-round(duration_h*60)) > 1e-7):
        raise ValueError('HEC-RAS 模拟时长须为正，且结束时间精确到整分钟')
    computation, mapping = token(dt_s), token(output_interval_min * 60)
    if output_interval_min*60 < dt_s or (output_interval_min*60 / dt_s) % 1:
        raise ValueError('HEC-RAS 输出间隔须为计算步长的整数倍')
    start = datetime(2024, 1, 1)
    end = start + timedelta(hours=duration_h)
    def stamp(value):
        months = ('JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC')
        return f'{value.day:02d}{months[value.month-1]}{value.year:04d},{value:%H%M}'
    lines = [
        "Plan Title=Yayao Simulation",
        "Program Version=7.00",
        "",
        "Geom File=g01",
        "Flow File=u01" if unsteady else "Flow File=f01",
        "",
        f"Simulation Date={stamp(start)},{stamp(end)}",
        f"Computation Interval={computation}",
        "Computation Level Option=1",
        f"Output Interval={mapping}",
        f"Mapping Interval={mapping}",
        f"DSS Interval={mapping}",
        "Run HTab=1",
        f"Run UNet={1 if unsteady else 0}",
        "Run SedTran=0",
        "Mixed Flow Regime=0",
        "Unsteady Flow Computational Scheme=1",
    ]
    path = os.path.join(work_dir, f"{_PROJECT_STEM}.p01")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _build_f01_file(work_dir: str, config: YayaoConfig,
                    upstream_flow_m3s: float,
                    downstream_stage_m: float) -> str:
    """生成稳态流数据文件 (.f01) — 精确匹配 HEC-RAS 7.0 格式。

    RM 取自 config.length_m（= 上游首断面桩号），不再写死 3200。
    字段宽度 18/16/8 与 HEC-RAS 原生写出的一致，只让桩号跟随河长。
    """
    rm_up = int(config.length_m)
    content = f"Flow Title=Flow 01\nProgram Version=7.00\n"
    content += f"Number of Profiles= 1 \nProfile Names=PF 1\n"
    content += (f"River Rch & RM={_RIVER_NAME:<18},{_REACH_NAME:<16},{rm_up:<8d}\n"
                f"     {upstream_flow_m3s}\n")
    content += (f"Boundary for River Rch & Prof#="
                f"{_RIVER_NAME:<18},{_REACH_NAME:<16}, 1 \n")
    content += f"Up Type= 0 \nDn Type= 1 \nDn Known WS={downstream_stage_m}\n"
    content += f"DSS Import StartDate=\nDSS Import StartTime=\n"
    content += f"DSS Import EndDate=\nDSS Import EndTime=\n"
    content += f"DSS Import GetInterval= 0 \nDSS Import Interval=\n"
    content += f"DSS Import GetPeak= 0 \nDSS Import FillOption= 0 \n"
    path = os.path.join(work_dir, f"{_PROJECT_STEM}.f01")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


# ---- HEC-RAS 模态对话框守护 ----

def _pid_is_ras(pid: int):
    """判断某 PID 是不是 HEC-RAS 进程。返回 (bool|None, 说明)。

    pywin32 的模块在此处惰性导入 —— 本模块需能在非 Windows 环境下被 import
    （只是 HEC-RAS 相关功能不可用而已）。
    """
    try:
        import win32api
        import win32process
    except ImportError:
        return None, "未安装 pywin32"
    h = None
    try:
        # QUERY_INFORMATION(0x0400) 取模块路径需要 VM_READ(0x0010)
        h = win32api.OpenProcess(0x0400 | 0x0010, False, pid)
        exe = win32process.GetModuleFileNameEx(h, 0)
        return os.path.basename(exe).lower().startswith("ras"), exe
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        if h:
            try:
                win32api.CloseHandle(h)
            except Exception:
                pass


class _HecRasDialogGuard:
    """HEC-RAS 模态对话框守护（上下文管理器）。

    为什么必须要有
    --------------
    HEC-RAS 在两类情况下会弹**模态**对话框，而 COM 调用会一直阻塞在那里：

      ① 输入不合法（实测：日期格式错误 → "must be before ending time"）
      ② **退出时询问是否保存工程**（`QuitRas()` 触发）

    第 ② 类尤其阴险：`QuitRas()` 调用**本身会正常返回**，看起来一切正常，
    但 Ras.exe 会停在保存询问上不退 —— 表现为"脚本跑完了，但每跑一次多一个
    Ras.exe 进程"。在命令行里实测卡死过两次。

    在 Web 服务里这意味着**一个请求永久挂起**，所以必须处理。

    它做什么
    --------
    后台守护线程轮询顶层窗口，只认 **Ras.exe 拥有的 `#32770`（Win32 对话框类）**，
    抓到就把标题与全部子控件文字记进日志（**错误信息不能丢**），再发 WM_CLOSE。

    降级
    ----
    非 Windows 或缺 pywin32 时自动不启用，不影响其余逻辑。
    """

    def __init__(self, interval: float = 1.0):
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        self.captured = []          # [(pid, title, [texts])]，供调用方与测试查证

    def __enter__(self):
        try:
            import win32gui  # noqa: F401
        except ImportError:
            logger.info("未安装 pywin32，不启用 HEC-RAS 对话框守护")
            return self
        self._thread = threading.Thread(target=self._run,
                                        name="hecras-dialog-guard", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        return False                # 不吞异常

    def _run(self):
        while not self._stop.wait(self._interval):
            try:
                self._sweep()
            except Exception:
                # 窗口枚举在窗口销毁过程中会失败，属正常现象，忽略继续
                pass

    def _sweep(self):
        import win32con
        import win32gui
        import win32process

        pids = {}

        def is_ras(pid):
            if pid not in pids:
                pids[pid] = bool(_pid_is_ras(pid)[0])
            return pids[pid]

        targets = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return True
            if win32gui.GetClassName(hwnd) != "#32770":   # Win32 对话框
                return True
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if is_ras(pid):
                targets.append((hwnd, pid))
            return True

        win32gui.EnumWindows(cb, None)

        for hwnd, pid in targets:
            title = win32gui.GetWindowText(hwnd)
            texts = []

            def ccb(h, _):
                t = win32gui.GetWindowText(h)
                if t and t.strip():
                    texts.append(t.strip())
                return True

            win32gui.EnumChildWindows(hwnd, ccb, None)
            self.captured.append((pid, title, texts))
            logger.warning(
                "捕获 HEC-RAS 对话框并关闭 —— pid=%s 标题=%r 内容=%s "
                "（若不处理，COM 调用会在此永久阻塞）", pid, title, texts)
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception as e:
                logger.warning("关闭 HEC-RAS 对话框失败: %s: %s", type(e).__name__, e)


# ---- COM 驱动的水动力模拟 ----

def run_hecras_simulation(
    config: YayaoConfig,
    cross_sections: List[CrossSection],
    upstream_flow_m3s: float = 6.9,
    downstream_stage_m: float = 0.83,
    tidal_amplitude_m: float = 0.8,
    tidal_period_h: float = 12.42,
    duration_h: float = 24.0,
    dt_s: float = 60.0,
    output_interval_min: float = 30.0,
    keep_work_dir: bool = False,
    unsteady: bool = False,
) -> HydroResult:
    """通过 HEC-RAS COM 运行水动力模拟。

    unsteady=True 为显式非恒定流适配路径；尚未改变 Web 默认引擎策略。
    此路径必须读到真实非恒定流时间序列，不允许用恒定流结果冒充。

    Args:
        keep_work_dir: 是否保留临时工作目录。**默认 False（跑完即删）**。
            置 True 可留下生成的 .prj/.g01/.u01/.p01/.f01 与输出 HDF，
            用于排查 HEC-RAS 的输入问题。

    ⚠️ 临时目录清理是**修过的缺陷**：此前 `mkdtemp` 无对应 `rmtree`，
    每调用一次就永久留下一个完整工程目录（实测在系统 temp 下累积了 5 个）。
    """
    if not is_hecras_available():
        raise RuntimeError("HEC-RAS 不可用: Ras.exe 未找到")
    if tidal_amplitude_m and not unsteady:
        raise ValueError('非零潮汐边界必须显式启用 HEC-RAS 非恒定流方案')

    work_dir = tempfile.mkdtemp(prefix="hecras_yayao_")
    ras = None          # 在 try 外声明：finally 里要用它来释放 COM

    try:
        # 1. 生成所有输入文件
        prj_path = _build_project_file(work_dir, include_flow_plan=True)
        _build_geometry_file(work_dir, config, cross_sections)
        _build_unsteady_flow_file(work_dir, config,
                                   upstream_flow_m3s, downstream_stage_m,
                                   tidal_amplitude_m, tidal_period_h,
                                   duration_h, dt_s)
        _build_plan_file(work_dir, duration_h, dt_s, output_interval_min, unsteady=unsteady)
        _build_f01_file(work_dir, config, upstream_flow_m3s, downstream_stage_m)

        # 2~3. COM 打开项目并计算。
        # 整段包在对话框守护里 —— HEC-RAS 在输入不合法时会弹模态框，
        # 而 QuitRas() 本身也会弹"是否保存工程"的询问（且该调用会正常返回，
        # 极其隐蔽）。不处理的话 COM 会永久阻塞，Web 服务里就是一个请求挂死。
        with _HecRasDialogGuard() as guard:
            ras = _get_hecras_com()
            ras.Project_Open(prj_path)
            time.sleep(1.0)

            n_xs = ras.Schematic_XSCount()
            if n_xs == 0:
                raise RuntimeError(
                    "HEC-RAS 无法解析几何文件 (Schematic_XSCount=0)"
                )

            ras.Compute_HideComputationWindow()
            plan_names = ras.Plan_Names()
            if not plan_names or plan_names[0] == 0:
                raise RuntimeError("HEC-RAS 项目中没有找到计算方案")

            ras.Plan_SetCurrent(plan_names[1][0])
            ras.Compute_CurrentPlan()

            timeout = 300
            for i in range(timeout):
                time.sleep(1.0)
                if ras.Compute_Complete():
                    break
            else:
                ras.Compute_Cancel()
                raise RuntimeError(f"HEC-RAS 计算超时 ({timeout}s)")

            ras.QuitRas()

            # ⚠️ 退出宽限期 —— **必须在 with 内**。
            # `QuitRas()` 会弹"是否保存工程"的模态询问，而**该调用本身正常返回**，
            # 询问可能在返回之后才出现。守护线程的轮询周期是 1 s，若立刻退出
            # with，`__exit__` 会把 `_stop` 置位、线程随即结束，那个询问就永远
            # 没人关 —— Ras.exe 成为孤儿进程并占住工程目录，
            # 连带 `_rmtree_with_retry` 重试 5 次后失败，目录也残留。
            # 即"每跑一次多一个目录/进程"的老症状会在最需要守护的时候复现。
            time.sleep(_QUIT_GRACE_SEC)

        if guard.captured:
            logger.warning(
                "本次 HEC-RAS 运行期间捕获并关闭了 %d 个对话框 —— "
                "说明输入或退出流程存在问题，请查上方日志确认被关闭的是什么",
                len(guard.captured))

        # 4. 检查计算结果 — 如果是纯稳态流, 使用此结果; 如果非恒定流已跑, 提取时间序列
        hdf_path = os.path.join(work_dir, f"{_PROJECT_STEM}.p01.hdf")
        if not os.path.exists(hdf_path):
            raise RuntimeError("HEC-RAS 未生成输出 HDF5 文件")

        result = _extract_hecras_results(hdf_path, config, upstream_flow_m3s,
                                          downstream_stage_m, tidal_amplitude_m,
                                          tidal_period_h, duration_h, dt_s, work_dir,
                                          cross_sections=cross_sections, require_unsteady=unsteady)

        # 结果里带着 work_dir 路径；若即将在 finally 里删掉它，
        # 必须把这个字段改掉 —— 否则返回的是一个指向不存在目录的路径，
        # 比留空更有误导性。
        if not keep_work_dir and isinstance(result.params, dict):
            result.params["work_dir"] = None
            result.params["work_dir_cleaned"] = True

        return result

    except Exception as e:
        hint = (f"工作目录: {work_dir}" if keep_work_dir else
                "临时工作目录已清理（如需保留以排查输入文件问题，"
                "传 keep_work_dir=True）")
        raise RuntimeError(f"HEC-RAS 模拟失败: {e}. {hint}")

    finally:
        # 无论成功、失败还是抛异常，都要清掉临时目录。
        if not keep_work_dir:
            # ⚠️ 必须**先释放 COM 引用再删目录**。
            # `ras` 是局部变量，若不在 finally 里显式置 None，它会一直存活到
            # 函数返回 —— 而 HEC-RAS 以工程目录作为自己的工作目录，
            # COM 对象未释放时该目录句柄仍被占用。
            # 实测症状很隐蔽：文件都能删掉，只有**目录本身**删不掉，
            # 于是每跑一次残留一个空目录。首版用 ignore_errors=True
            # 把这个失败静默吞了，看起来像修好了，实际没有。
            ras = None
            gc.collect()
            _rmtree_with_retry(work_dir)


def _extract_hecras_results(hdf_path: str, config: YayaoConfig,
                             upstream_flow_m3s: float, downstream_stage_m: float,
                             tidal_amplitude_m: float, tidal_period_h: float,
                             duration_h: float, dt_s: float,
                             work_dir: str, cross_sections=None,
                             require_unsteady: bool = False) -> HydroResult:
    """从 HEC-RAS 7.0 HDF5 输出文件提取水动力结果。"""
    import h5py

    with h5py.File(hdf_path, "r") as h5:
        results = h5["Results"]
        has_unsteady = "Unsteady" in results
        if require_unsteady and not has_unsteady:
            raise ValueError('HEC-RAS 未返回非恒定流结果，不接受恒定流替代')

        if has_unsteady:
            # 非恒定流
            #
            # 路径与单位均为 HEC-RAS 7.0 实测确认，原先两处都写错了：
            #   1. Cross Sections 挂在 "Unsteady Time Series" 之下，
            #      不是直接挂在 Base Output 下（原写法会 KeyError）
            #   2. Time 的单位是【天】不是秒 —— 0.0416667 表示 1 小时。
            #      原写法除以 3600 会把 25 小时算成 0.0007 小时
            #   3. 该组下没有裸 "Velocity"，只有 Velocity Channel / Velocity Total
            # 参考: research/hydro-wq-model/HEC-RAS实测笔记.md
            base_path = ("Results/Unsteady/Output/Output Blocks/Base Output"
                         "/Unsteady Time Series")
            xs = h5[base_path + "/Cross Sections"]
            wl = np.array(xs["Water Surface"])
            q = np.array(xs["Flow"])
            vkey = next((k for k in ("Velocity Total", "Velocity Channel")
                         if k in xs), None)
            vel = np.array(xs[vkey]) if vkey else np.zeros_like(wl)
            area = np.array(xs['Flow Area']) if 'Flow Area' in xs else None

            if "Time" in h5[base_path]:
                t_hours = np.array(h5[base_path]["Time"], dtype=float).reshape(-1) * 24.0
            else:
                raise ValueError('HEC-RAS 缺少实际时间轴，不能合成时间轴')

            engine = "hecras_7.0_unsteady"
        else:
            # 稳态流
            base_path = "Results/Steady/Output/Output Blocks/Base Output/Steady Profiles"
            xs = h5[base_path + "/Cross Sections"]
            wl = np.array(xs["Water Surface"])
            q = np.array(xs["Flow"])
            # 流速在 Additional Variables/Velocity Total
            av = xs.get("Additional Variables")
            if av is not None and "Velocity Total" in av:
                vel = np.array(av["Velocity Total"])
            else:
                vel = np.zeros_like(wl)
            area = np.array(xs['Flow Area']) if 'Flow Area' in xs else None

            t_hours = np.array([0.0])
            engine = "hecras_7.0_steady"

        # 桩号 — Geometry/Cross Sections/Attributes 是结构化 Dataset
        n_nodes = wl.shape[1]
        geo_attrs = h5["Geometry/Cross Sections/Attributes"]
        if hasattr(geo_attrs, "dtype") and geo_attrs.dtype.names is not None:
            # 结构化 dtype, 按字段名提取
            field_names = list(geo_attrs.dtype.names)
            # 优先匹配 RS (River Station), 然后是 Station, 最后是 River
            rs_field = None
            for fn in field_names:
                if fn == "RS" or fn == "River Station":
                    rs_field = fn
                    break
            if rs_field is None:
                for fn in field_names:
                    if "Station" in fn and "River" not in fn:
                        rs_field = fn
                        break
            if rs_field:
                raw = np.array(geo_attrs[rs_field]).flatten()
                # HEC-RAS 可能存储为字符串或数值
                if raw.dtype.kind in ("S", "U"):
                    stations = np.array([float(s.decode() if isinstance(s, bytes) else s) for s in raw])
                else:
                    stations = raw.astype(float)
            else:
                raise ValueError('HEC-RAS 结果缺少断面桩号，不能补造等距断面')
        else:
            raise ValueError('HEC-RAS 结果缺少断面属性')

        chainage = config.length_m - stations
        sort_idx = np.argsort(chainage)
        chainage = chainage[sort_idx]
        wl = wl[:, sort_idx]
        q = q[:, sort_idx]
        area_source = 'hecras_hdf_flow_area'
        if area is not None:
            area = area[:, sort_idx]
        else:
            if not cross_sections:
                raise ValueError('HEC-RAS 未输出过水面积，且未提供对应断面几何')
            section_x = np.array([cs.chainage_m for cs in cross_sections])
            selected = [int(np.argmin(np.abs(section_x-x))) for x in chainage]
            if any(abs(section_x[i]-x) > 1.01 for x,i in zip(chainage, selected)):
                raise ValueError('HEC-RAS 结果桩号与提供的断面几何不匹配')
            area = np.array([[cross_sections[j].flow_area(float(stage))
                              for j,stage in zip(selected,row)] for row in wl])
            area_source = 'derived_from_supplied_cross_sections'
        if (wl.shape != q.shape or wl.shape != area.shape or wl.shape[0] != len(t_hours)
                or len(chainage) != wl.shape[1] or np.any(np.diff(chainage) <= 0)
                or any(np.any(~np.isfinite(v)) for v in (wl, q, area, t_hours))
                or np.any(area <= 0)):
            raise ValueError('HEC-RAS 时间/断面/过水面积包含非法值或形状不匹配')
        if has_unsteady and (len(t_hours) < 2 or np.any(np.diff(t_hours) <= 0)
                             or abs(t_hours[0]) > 1e-5 or t_hours[-1] < duration_h-1e-5):
            raise ValueError('HEC-RAS 实际时间轴未覆盖所请求的模拟区间')
        vel = q / area  # 体平均速度与总流量、总过水面积一致，保留反向流符号

    return HydroResult(
        t=t_hours,
        chainage=chainage,
        water_level=wl,
        discharge=q,
        velocity=vel,
        area=area,
        params={
            "upstream_flow_m3s": upstream_flow_m3s,
            "downstream_stage_m": downstream_stage_m,
            "tidal_amplitude_m": tidal_amplitude_m,
            "tidal_period_h": tidal_period_h,
            "duration_h": duration_h,
            "dt_s": dt_s,
            "engine": engine,
            "area_source": area_source,
            "velocity_source": "bulk_discharge_divided_by_area",
            "work_dir": work_dir,
        },
    )
