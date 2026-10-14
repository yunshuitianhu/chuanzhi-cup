# HEC-RAS 7.0 实测笔记

> 环境：HEC-RAS **7.0 (April 2026)**，便携版安装于 `D:\my_HEC-RAS\`
> COM ProgID：`RAS70.HECRASController`（**已注册，实测可用**）
> 记录日期：2026-10-14
>
> 本文件只记录**实际跑出来的结果**，不写推测。凡未验证的一律标注"未验证"。

---

## 一、结论：能程序化驱动，但有一条硬约束

COM 全链路实测通过：

```
win32com.client.Dispatch("RAS70.HECRASController")   OK
rc.Compute_HideComputationWindow()                   OK
rc.Project_Open(prj_path)                            OK
rc.Schematic_XSCount()            -> 5               OK
rc.Plan_Names()                   -> (1, ('Yayao',), False)
rc.Plan_SetCurrent('Yayao')                          OK
rc.Compute_CurrentPlan()          # 不带参数！        OK
rc.Compute_Complete()             -> True            OK
rc.QuitRas()                                         OK
```

结果在 `*.p01.hdf`（HDF5），用 `h5py` 直接读。几何、边界、结果全程无需 GUI。

### ⚠️ 硬约束：模态对话框会永久阻塞 COM

输入不合法时 HEC-RAS 弹**模态对话框**，`Compute_CurrentPlan()` 会一直阻塞。
实测已卡死两次，每次都留下无法自行退出的 `Ras.exe` 进程，必须手动 `Stop-Process`。

**推论**：任何无人值守的 HEC-RAS 自动化都必须有
(a) 输入预校验，或 (b) 对话框监视与自动关闭，或 (c) 硬超时 + 进程清理。
三者至少要有一个。本目录的 `watch_dialogs.ps1` 是 (b) 的实现。

---

## 二、已确证的输入文件格式修正

对照物：`river_model/hecras_bridge.py` 生成的三个模板 + HEC-RAS 自身的报错。

### 2.1 `.prj` —— 关键字 `Unsteady Flow File=` 不存在

**证据**：HEC-RAS 打开工程后**自行重写了 `.prj`**，改写结果就是它的规范格式。
实测 diff：

| 原模板写的 | HEC-RAS 改写后 |
|---|---|
| `Unsteady Flow File=u01` | **删掉** |
| （无） | **新增 `Unsteady File=u01`** |
| `Current Geom=g01` | 删掉 |
| `Current Flow=f01` | 删掉 |
| （无） | 新增 `DSS File=dss` |
| `DSS Export Rating Curves= 0` | `DSS Export Rating Curves= 0 `（值后补空格） |

**结论**：正确关键字是 **`Unsteady File=`**，不是 `Unsteady Flow File=`。

### 2.2 `.p01` —— `Flow File=` 决定恒定/非恒定

**证据**：原 `.p01` 同时写了
```
Unsteady Flow File=u01
Flow File=f01          <-- 指向恒定流文件
```
结果 HEC-RAS 调用的是 **`RasSteady.exe`**（从 HDF 的 `Results/Summary/Compute Processes`
读到实际命令行），日志写 `PROCESS:RasSteady` / `Steady Flow Simulation`。

改为 `Flow File=u01` + `Run HTab=1` + `Run UNet=1` + `Run SedTran=0` 之后，
HEC-RAS **进入非恒定流分支**（弹出 "preparing unsteady flow data for simulation"）。

**结论**：`Flow File=` 指向哪个文件决定引擎；`Unsteady Flow File=` 这一行是无效的。

### 2.3 `.p01` —— 起止时间必须写在同一行

**证据**：原写法
```
Simulation Date=01Jan2024
Simulation Time=00:00:00
Simulation End Date=01Jan2024
Simulation End Time=03:00:00
```
HEC-RAS 报：
```
Simulation starting date/time (31Dec2023 2400) must be before ending time (31DEC0099 2400).
```
起始时间解析正确（31Dec2023 2400 = 2024-01-01 00:00），**结束时间退化为零日期**。

改为单行四值
```
Simulation Date=01JAN2024,0000,01JAN2024,0300
```
**该报错消失**。

**结论**：格式为 `Simulation Date=起日,起时,止日,止时`，同一行，月名大写，时刻为 `HHMM`。

### 2.4 `.u01` —— `Boundary Location=` 需要 9 个逗号分隔字段

**证据**：原写法 `Boundary Location=3200.000`，HEC-RAS 报：
```
Boundary at River: 3200.000  Reach:   RS: 
```
它把 `3200.000` **当成了河名**，河段名与断面号全空 → 上下游边界都识别不到。

**结论**：格式为
```
Boundary Location=<河名>,<河段名>,<断面号>,<下游断面号>,<蓄水区连接>,<蓄水区名>,<泵站名>,<保留>,<保留>
```
本项目为 `Boundary Location=Yayao,Main,3200,,,,,,`
（河名/河段名取自 `.g01` 的 `River Reach=Yayao             ,Main`）。

### 2.5 `.u01` —— 时间步关键字是 `Interval=`，不是 `Flow Hydrograph Time Step=`

**证据**：官方格式文档明确 "Timing comes from the preceding `Interval=` line"，
取值形如 `5MIN` / `15MIN` / `1HOUR`。
原文件写的是 `Flow Hydrograph Time Step=5min` + `Flow Hydrograph Date=01Jan2024`。

**状态**：⚠️ **未完全验证** —— 改成 `Interval=5MIN` 后尚未确认这一条是否必要
（同批还改了 2.4，两者效果未分离）。

### 2.6 数值表排版

官方格式：**每值 8 字符右对齐，每行 10 个**（80 列）。
本笔记生成时用 `f"{v:8.2f}"`，每 10 个换行。

---

## 二·补 「非恒定流不兼容」的说法是错的 —— 已跑通

改完 2.1~2.4 之后，HEC-RAS **成功完成非恒定流计算**。它自己的日志：

```
PROCESS:RasUnsteady
Performing Unsteady Flow Simulation  HEC-RAS 7.0 April 2026
Unsteady Input Summary:
     1D Unsteady Finite Difference Numerical Solution
     Number of cross sections:        5
...
Overall Volume Accounting Error in 1000 m^3:      0.0000182333
Overall Volume Accounting Error as percentage:      0.0000061926
Finished Unsteady Flow Simulation
```

### 质量守恒对比

| 求解器 | 质量守恒误差 |
|---|---|
| 纯 Python `pure_py_hydro.py`（本项目） | **103%** |
| **HEC-RAS 7.0 非恒定流** | **0.0000062%** |

差约 **170 亿倍**。HEC-RAS 用的是 `1D Unsteady Finite Difference`（即隐式有限差分，
Preissmann 类格式）—— **与本项目自研求解器是同一类格式**，但质量守恒差这么多个数量级。

> 这反过来印证了第五节的判断：问题不在"选了 Preissmann"，而在**离散写法**。
> 同一格式，HEC-RAS 做得到机器精度级别的守恒，说明写对了是可能的。

### 结果读取路径（已实测）

```
Results/Unsteady/Output/Output Blocks/Base Output/Unsteady Time Series/
    Time                    # ⚠️ 单位是【天】，不是小时
    Time Date Stamp         # 形如 b'01JAN2024 01:00:00'
    Time Step               # 秒
    Cross Sections/
        Cross Section Attributes   # (River, Reach, RS, Name)
        Water Surface              # (时间, 断面)
        Flow                       # (时间, 断面)
        Velocity Channel / Velocity Total
        Flow Volume Cumulative
```

**踩坑**：`Time` 数组给的是 `[0, 0.04166667, 0.08333333, 0.125]`，
一开始误读成"只有 7.5 分钟"，其实单位是天 → 0/1/2/3 小时，**3 小时窗口完整跑完**。
判断是否跑完要看 `Time Date Stamp`，不要只看 `Time` 的数值。

### 已验证的物理合理性（恒定边界工况）

断面顺序 RM 3200 → 0（上游 → 下游），边界为恒定流量 6.9 m³/s + 恒定水位 0.5 m：

| 断面 RM | 水位 (m) |
|---|---|
| 3200（上游） | 0.5187 |
| 2400 | 0.5155 |
| 1600 | 0.5115 |
| 800 | 0.5064 |
| 0（下游） | 0.4999 |

下游 ≈ 设定边界 0.5 m ✓；沿程因摩阻递减 ✓；水位随时间几乎不变（变幅 8e-5 m）
✓ —— 恒定边界下收敛到恒定状态，符合预期。

---

## 二·补二 潮汐工况实测（决定性验证）

边界：上游恒定流量 6.9 m³/s；下游**时变水位过程线**
（均值 0.5 m，振幅 0.8 m，周期 12.42 h 的 M2 半日潮）；
模拟 25 小时（约 2 个潮周期），计算步长 30 s。

### 结果：潮汐信号完整上传，且出现水流反向

| 断面 RM | 水位范围 (m) | 变幅 (m) | 相对下游 |
|---|---|---|---|
| 0（下游，边界） | −0.290 ~ 1.300 | 1.590 | 100% |
| 800 | −0.138 ~ 1.303 | 1.441 | 90.6% |
| 1600 | −0.099 ~ 1.304 | 1.403 | 88.2% |
| 2400 | −0.081 ~ 1.306 | 1.386 | 87.2% |
| 3200（上游） | −0.067 ~ 1.306 | 1.373 | **86.4%** |

**潮汐传到上游只衰减 13.6%，没有消失。** 上游低潮位被径流"填高"
（−0.067 vs 下游 −0.290），是感潮河道典型的涨潮主导特征。

### 流量反向 —— 纯 Python 求解器做不到的事

| 断面 RM | 流量范围 (m³/s) |
|---|---|
| 3200（上游） | 6.900 ~ 6.900（恒定，边界） |
| 2400 | 2.614 ~ 10.505 |
| 1600 | **−1.667** ~ 14.142 |
| 800 | −5.926 ~ **17.843** |
| 0（下游） | **−10.080** ~ **21.749** |

**负流量 = 涨潮时水流倒灌**，且反向幅度沿程递增（河口最大、上游消失）。
这是感潮河道的定义性特征。

### 质量守恒

| 工况 | 体积守恒误差 |
|---|---|
| 恒定边界 | 0.0000062% |
| 潮汐边界 | 0.01161% |
| （对照）纯 Python 求解器 | **103%** |

证据文件：`tidal_results.csv`、`tidal_propagation.png`

### ⚠️ 必须说清楚的局限

**几何是人工生成的规则梯形，不是实测断面。**

`Yayao.g01` 的 5 个断面除河底高程按 800 m 等差递减 0.1 m 外完全一致，
渠底 40 m 宽、边坡至 ±25 m、Manning n 0.028/0.033。
这与 `hecras_bridge.py:_build_geometry_file()` 的生成结果一致。

**所以**：上表证明的是**求解器和驱动链路正确**，**不代表这是雅瑶水道的真实模型**。
要让它有实际意义，必须换入真实断面数据——而本项目**没有雅瑶水道的实测断面**。
这是比求解器更根本的限制，不要在上表中读出超出其证据力的结论。

---

## 三、已确证的环境事实

| 事实 | 证据 |
|---|---|
| HEC-RAS 7.0 April 2026 | `computeMsgs` 里写 `HEC-RAS 7.0 April 2026` |
| 恒定流引擎是独立 exe | `D:\my_HEC-RAS\x64\RasSteady.exe` |
| COM 已注册 | 注册表 `HKCR\RAS70.HECRASController`，CLSID `{6EED89FF-61FA-4FDF-ADDF-1A634B07DA7D}` |
| `Ras.exe -c project.prj` **无效** | 实测 `.hdf` 大小与时间戳均未变化，只启动了 IPC 服务 |
| 已有的 Yayao 工程从未跑过非恒定流 | `.p01.hdf` 内 `Results` 仅有 `Steady` 分支，非恒定流路径数 = 0 |

---

## 四、待办

**已完成**
- [x] 分离 2.4 与 2.5 的效果 —— `Boundary Location=` 九字段格式是必需的（2.4 已确证）
- [x] 改为潮汐边界并验证沿程传播 —— 见「二·补二」，86.4% 上传 + 流量反向
- [x] 确认 `hecras_bridge.py` 的"7.0 非恒定流不兼容"结论**不成立**

**未完成**
- [ ] `Output Interval=30MIN` **未被采纳** —— 实测输出为逐小时。需查明正确的
      输出间隔关键字（可能不是 `Output Interval=`）
- [ ] `Interval=` 是否必需尚未与 2.4 分离验证（两者同批改动）
- [ ] 单位令牌大小写（`30SEC` vs `30sec`）是否敏感 —— 未验证
- [ ] 几何是人工梯形，需真实断面才有实际意义（见「二·补二」局限说明）
- [ ] 评估 `hecras_bridge.py` 的修改方案（属 **main 分支**工作，需单独决策）
- [ ] 补：`mkdtemp` 无 `rmtree` 的临时目录泄漏（既有缺陷，与本议题独立）

### 若要写回项目，需要改的四处

`hecras_bridge.py` 的三个模板各有一处错，加上 `.u01` 的边界行：

| 位置 | 现在 | 应为 |
|---|---|---|
| `_PRJ_FULL` | `Unsteady Flow File=u01` | `Unsteady File=u01` |
| `.p01` 生成 | `Unsteady Flow File=u01` + `Flow File=f01` | `Flow File=u01` + `Run UNet=1` |
| `.p01` 生成 | `Simulation Date=` / `Simulation Time=` 分行 | `Simulation Date=起日,起时,止日,止时` 单行 |
| `_build_unsteady_flow_file` | `Boundary Location=<单值>` | `Boundary Location=河名,河段名,断面号,,,,,,` |

**注意**：这属于 main 分支的改动，按项目铁律需先出方案、单独提交、逐项验证，
不在本次调研范围内擅自动手。

---

## 五、踩过的坑（工具层面）

1. **PowerShell 5.1 读 `.ps1` 用系统 ANSI 码页**，无 BOM 的 UTF-8 中文会被撕碎，
   甚至破坏字符串字面量导致**解析失败**。写 `.ps1` 一律只用 ASCII。
2. **Python 输出重定向到文件时是全缓冲的**，进程被 `timeout` 杀掉会丢掉全部输出。
   排查这类问题必须加 `python -u`。
3. **后台运行的 COM 调用被杀后会留下孤儿 `Ras.exe`**，它不响应也不退出，
   会干扰后续运行。每轮实验前后都要清。
