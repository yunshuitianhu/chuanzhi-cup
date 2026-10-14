# 水质-水动力模型：开源方案调研

> **调研日期**：2026-10-14
> **分支**：`research/hydro-wq-model` —— **独立线，不并入 `main`**
> **起因**：`river_model/pure_py_hydro.py` 的连续性方程遗漏水面宽 `B_mid`，非恒定工况
> 质量不守恒（残差达蓄水率的 103%）。此前的结论是"需重新推导 Preissmann 离散，
> 非外科修复可解"，因此暂挂。本文件是对外部参考实现的调研，供后续决定路线用。

---

## 零、先看这一节：本次调研的两条自我更正

写报告前先记下两处**我自己的错误**，避免后人重蹈：

**① 搜索结果摘要把 HydroClaude 的结论说反了。**
搜索摘要称该项目"实测 36% 质量守恒误差后放弃 Preissmann、改用 Godunov-FVM"。
抓取原始文档后核实，**事实相反**：原文写的是
> "Preissmann是唯一稳定且精度可接受的求解器"

被淘汰的是另外三个求解器（FVM 溢出为 NaN、MOC 误差 2482.7%、高阶格式不适用）。
HydroClaude **保留**了 Preissmann，并把 36.3% 误差接受为"当前最佳可达精度"。

**教训**：AI 生成的搜索摘要会编造因果。任何要写进决策的结论，必须回到原始文档核实。

**② "32 位 hex = 高德 Key"式的宽泛正则会造成假阳性。**
（详见仓库 `CLAUDE.md` 同类记录）判断类结论一律用**具体值精确匹配**，不用格式模式。

**③ 我一度把两个不同的误差指标并排比较了。**
搜索摘要称 HydroClaude 的 36.3% 是"质量守恒误差"，我未加核实就填进了
"质量守恒误差"这一列，而它与本项目的指标**并不等价**（原文量的是水位变幅 Δh）。
**这正是①的同一个错误，只是换了个地方犯。** 已在第五节标注，不再并排比较。

> 三次更正指向同一条规则：**搜索摘要给出的"结论"和"命名"，都当假设看，不当事实用。**

---

## 一、调研判据

按本项目 `river_model/` 的实际需求，一条合格的参考实现应尽量满足：

| 维度 | 需求 | 本项目现状 |
|---|---|---|
| 维度 | **一维**（河道沿程） | 一维 |
| 方程 | Saint-Venant 圣维南（非恒定） | Preissmann 四点隐式，**有缺陷** |
| 几何 | 不规则断面、变水面宽 | 有（`cross_sections.py`） |
| 阻力 | Manning 摩阻 | 有 |
| 边界 | **下游潮汐边界**（感潮河道） | 有，已修好 |
| 水质 | 对流-扩散-反应（ADR） | 有（`wq_engine.py`，算子分裂+RK4） |
| 语言 | Python（需嵌入现有栈） | Python |
| 许可 | 允许使用/参考 | — |

**关键背景**：采样河段是**雅瑶水道**，属珠江感潮河网，下游受潮汐顶托。
"能算潮汐"不是加分项而是硬需求，这也正是恒定流求解器不够用的原因。

---

## 二、结论摘要（TL;DR）

1. **Python 生态里不存在成熟的「一维河道水动力 + 水质」耦合库。**
   GitHub `saint-venant` 主题下的 Python 仓库**总共只有 4 个**，star 数 1~4，
   全部是个人练习或课程项目级。想 `pip install` 一个工业级一维河道模型，没有。

2. **工业级的一维感潮河道水质开源模型只有一个明确答案：DSM2**（加州水资源部）。
   它明确写着 "dynamic simulation of **tidal** hydraulics, water quality"，
   2196 次提交，是**在产**的业务模型（管理加州三角洲）。但它是 C/C++/Fortran，非 Python。

3. **和本项目最"像"的兄弟项目是 HydroClaude**（中国开发者、Python、一维水力学、MIT）。
   最有价值的不是它的代码，而是它的**调试记录**：Preissmann 水位变幅误差 36.3%，
   扫了 80 组参数后判定"无法进一步改善"并接受，而非解决。见第五节
   （⚠️ 该误差指标与我们的质量守恒残差**不等价**，第五节已详述）。

4. **amerta 的价值不在模型，在它的验证算例。**
   它本身是无摩擦平底理想模型，算不了真实河道；但它带**解析解基准**
   （Ritter / Stoker 溃坝等 Riemann 问题 + L1 误差范数），
   正好补上本项目 `tests/river_model_验收算例.py` 缺的那一环：**没有解析解就没法证明求解器对**。

5. **HEC-RAS 自动化路线有比本项目现方案更成熟的选择：ras-commander**
   （MIT，86 star，1769 提交，支持 Linux + Windows 无头运行非恒定流）。
   本项目 `hecras_bridge.py` 是手写 COM 调用，且已知有临时目录泄漏缺陷。

---

## 三、分类清单

标注说明：**✅ 已核实** = 我直接抓取了仓库/文档页面确认；**⚠️ 未核实** = 仅见于搜索摘要，采用前需自行确认。

### A. 一维水动力求解器（Saint-Venant）

| 项目 | 方案 | 语言 | 许可 | star | 核实 |
|---|---|---|---|---|---|
| [cve-mohd/flow-sim](https://github.com/cve-mohd/flow-sim) | **Preissmann** + Lax-Friedrichs 有限差分 | Python 3.8+ / numpy / scipy | MIT | 4 | ✅ |
| [leixiaohui-1974/HydroClaude](https://github.com/leixiaohui-1974/HydroClaude) | **Preissmann 四点隐式** + Godunov 有限体积 | Python 3.12+ / FastAPI | MIT | 4 | ✅ |
| [sandyherho/amerta](https://github.com/sandyherho/amerta) | MUSCL-HLLC 有限体积 + SSP-RK2 | Python / numba | MIT | 2 | ✅ |
| [DanielCortild/SWE-Solver](https://github.com/DanielCortild/SWE-Solver) | well-balanced 一维 | Python | — | 2 | ⚠️ |
| [slebaleur/Shallow-water](https://github.com/slebaleur/Shallow-water) | HLL / Rusanov 通量 | Python | — | 1 | ⚠️ |
| [ECRIPPEL/culvert-solver](https://github.com/ECRIPPEL/culvert-solver) | 涵洞专用（broken-back culvert） | Python | — | 1 | ⚠️ |
| [benrhodges/SvePy](https://github.com/benrhodges/SvePy) | 交错网格 | Python | — | ~23 | ⚠️ 停更约 6 年 |

> `github.com/topics/saint-venant?l=python` 全量就是上表前 4 个（✅ 已核实该主题页）。
> **这个生态的贫瘠程度本身就是结论**。

#### A-1. `cve-mohd/flow-sim` —— 唯一找到的「Preissmann + Python」可读实现

- 仓库结构确有 `src/hydromodel/`，内含 **`preissmann.py`**、`lax.py`、`solver.py`、
  `boundary.py`、`cross_section.py`、`rating_curve.py`、`hydrograph.py`、`channel.py`
  （✅ 已核实文件列表；**文件内容未读**，实现质量待评估）
- 221 次提交，含实测率定曲线与 shapefile 河道数据
- ⚠️ **质量存疑**：README 的 Overview / Features / Usage 三节**全是 "TBA" 占位符**；
  安装说明里的 clone 地址指向另一个仓库名（`flood-wave-propagation`）对不上；
  致谢写着 "Special thanks to me for making this project possible"。
  **建议只当参考文献读，不要当依赖引入。**

#### A-2. `leixiaohui-1974/HydroClaude` —— 最值得读的对照项目

- 定位与本项目高度重合：**中国开发者、Python、一维明渠水力学、MIT、大量中文文档**
- 一维非恒定用有限体积（Godunov），恒定用 Newton-Raphson
- 还包含闸/堰/孔口/泵/水轮机、管网 Hardy-Cross、水锤 MOC
- README 自称"流量误差 0.000000%、收敛成功率 100%""比 HEC-RAS/MIKE 11 快 5-10 倍"
  —— ⚠️ **这是自报的营销数字，无第三方验证，不要采信**
- **真正的价值在它的调试文档**（见第五节）

### B. 一维感潮河道 + 水质（本项目最贴近的场景）

| 项目 | 说明 | 语言 | 许可 | 核实 |
|---|---|---|---|---|
| [CADWRDeltaModeling/dsm2](https://github.com/CADWRDeltaModeling/dsm2) | 加州水资源部官方。一维**感潮**水动力 + 水质 + 粒子追踪 | C/C++/Fortran | 见仓库 LICENSE.txt | ✅ |

**DSM2 细节**（✅ 除语言与许可外均已核实）：
- 自我描述："one-dimensional mathematical model for dynamic simulation of
  **tidal hydraulics**, water quality, and particle tracking"
- 三个组件：`HYDRO`（水动力，隐式有限差分，质量+动量方程联立求解）、
  `QUAL`（拉格朗日法，源自 BLTM）、`GTM`（半隐式有限体积，多组分）
- 另有 `HYDRO-GTM` 联立求解水动力与水质，支持"带水质约束的调度规则"
- 水质组分：盐度、**水温、溶解氧 DO、溶解有机碳 DOC**（未提营养盐）
- 2196 次提交，6 star —— star 低但**是业务系统**，不代表质量低
- **对本项目的意义**：如果要把潮汐河道水动力做到工业级可信，这是唯一
  可直接对照的开源实现。但**语言不通**，只能读算法不能直接用。

### C. 水质/生化反应（不含水动力）

| 项目 | 说明 | 语言 | 许可 | 核实 |
|---|---|---|---|---|
| [EcohydrologyTeam/ClearWater-riverine](https://github.com/EcohydrologyTeam/ClearWater-riverine) | 美国陆军 ERDC。**二维**水质输运，守恒型对流+扩散，非结构网格 | Python | MIT | ✅ |
| [AquaticEcoDynamics/glm-aed](https://github.com/AquaticEcoDynamics/glm-aed) | GLM 一维**垂向**水动力 + AED 水质库动态耦合 | C/Fortran + Python | — | ⚠️ |
| [ImperialCollegeLondon/wsi](https://github.com/ImperialCollegeLondon/wsi) | WSIMOD，水循环集成建模，节点-弧段 | Python | BSD-3 | ✅ |
| [thibhlln/torrentpy](https://github.com/thibhlln-torrentpy/torrentpy) | SMART 汇流 + INCA 水质，流域尺度集总式 | Python | GPL-3.0 | ⚠️ |
| mogestpy | 含 `hydrodynamic/sihqual.py`（SihQual 一维水动力水质） | Python | — | ⚠️ |
| pyRIVE / unified RIVE | 淡水生物地球化学（有机质降解、浮游植物、营养盐、氧） | Python / C | EPL 2.0 | ⚠️ |

> **ClearWater-riverine 的定位要说清楚**：它是**二维**（depth-averaged），
> 且需要 **HEC-RAS 2D 的输出**作为流场驱动。跟本项目"一维 + 自算水动力"不是一条路。
> 但它的反应模块（TSM 温度 / NSM 营养盐-藻类-溶解氧-有机质）设计值得参考。

### D. HEC-RAS 自动化（本项目已用 HEC-RAS）

| 项目 | 说明 | 许可 | star | 核实 |
|---|---|---|---|---|
| [gpt-cmdr/ras-commander](https://github.com/gpt-cmdr/ras-commander) | HEC-RAS 6.x Python API + HDF 读取 | MIT | 86 | ✅ |
| [psu-efd/pyHMT2D](https://github.com/psu-efd/pyHMT2D) | HEC-RAS / SRH-2D 输入输出与运行控制（COM） | — | ~122 | ⚠️ |
| [gpt-cmdr/HEC-Commander](https://github.com/gpt-cmdr/HEC-Commander) | ras-commander 的前身，Jupyter 笔记本集 | — | — | ⚠️ |
| `rashdf` / `pydsstools` / `mcat-ras` / `ras2fim` | HDF / DSS 读写与批量分析 | — | — | ⚠️ |

**ras-commander 关键能力**（✅ 已核实）：
- `RasCmdr.compute_plan()` / `compute_parallel()` / `compute_test_mode()` —— 无头批量运行
- `RasUnsteady` 类管理非恒定流文件；示例含非恒定边界条件的提取与修改
- `Hdf*` 系列类读取断面、结构物、管道、泵站、边界条件结果
- **支持 Linux**：Docker + Wine 预处理，镜像覆盖 HEC-RAS 6.5 / 6.6 / 7.0.1
- 1769 次提交，v0.88.0

> 对比本项目 `river_model/hecras_bridge.py`（手写 COM，465 行，**已知 `mkdtemp` 无
> `rmtree` 导致临时目录泄漏且已实测复现**）。若继续走 HEC-RAS 路线，ras-commander
> 是更成熟的替代；但引入新依赖会改变现有行为，需按"90% 把握"原则评估。

### E. 参考推导与文献（写求解器时真正要看的）

| 资料 | 内容 |
|---|---|
| **USGS WRI 97-4016** | 四点隐式法（Preissmann 1961）完整推导：积分形式离散、Taylor 线性化、逐河段带状方程组组装 |
| 塞尔维亚会议论文（zbornik.gf.uns.ac.rs） | **最直接可用**：Newton-Raphson 线性化 → `-C·ΔZᵢ - D·ΔQᵢ + A·ΔZᵢ₊₁ + B·ΔQᵢ₊₁ = G`，含 **Thomas 双扫描（double sweep）递推式** E/F/L/M/N 与上下游边界处理 |
| Aalto 大学学位论文 | 标准线性系统 `A1·Qⱼ + B1·yⱼ + C1·Qⱼ₊₁ + D1·yⱼ₊₁ = E1` 的系数表达式 |
| 法国论文（ROUX 2004 / Ouarit 2004） | 权重 θ 取值（Szymkiewicz / Liggett-Cunge 推荐 **θ = 2/3**）；**摩阻项 hJ(h,Q) 必须整体离散**的告诫 |
| 韩国渔业学报 (1981) | 实用经验：Δt/Δx ≈ 1；稳定区间 0.6 < θ < 1.0 |

> 注：HydroClaude 用的是 θ=0.6 并称"默认参数已经是最优的"。而法国文献推荐 2/3。
> 本项目当前用的 θ 值需在动手前先确认。

---

## 四、缺口分析：本项目真正缺什么

调研下来，外部**没有一个可以直接拿来用的替代品**。原因不是没找到，而是：

| 需求 | 外部现状 |
|---|---|
| Python + 一维 + Saint-Venant + 不规则断面 + 摩阻 | 只有 toy 项目 |
| 加上潮汐边界 | 无 |
| 加上水质 ADR 耦合 | 无 |
| 工业级验证 | 只有 DSM2（非 Python） |
| 可用的解析解测试基准 | **amerta 有，可直接借** |

**结论：求解器这块没有捷径，只能自己修。但"怎么证明修对了"有现成答案。**

---

## 五、对已知缺陷的技术判断

这是本次调研最有价值的一条。

**现象**：两个**互相独立**的 Python 实现，在同一类工况下都算不出应有的水位变化：

| 实现 | 工况 | 误差指标 | 数值 |
|---|---|---|---|
| 本项目 `pure_py_hydro.py` | 非恒定工况 | **质量守恒残差**（占蓄水率） | **103%** |
| HydroClaude | 棱柱形渠道，51 断面，dt=10s，θ=0.6 | **水位变幅 Δh 误差** | **36.3%**（理论 0.1000 m，实得 0.0637 m） |

> ⚠️ **这两个数字不是同一个指标，不能直接并排比较。**
> 98 行的搜索摘要把 HydroClaude 的 36.3% 称作"质量守恒误差"，
> 但原文档标题是 **`CANAL_SOLVER_PRECISION_REPORT`（精度报告）**，正文量的是
> **水位变幅 Δh**。**我没有核实它是否等同于质量守恒误差** —— 两者相关但不等价
> （不守恒必然导致水位算错，反之则未必）。
> 我一度直接把"质量守恒误差"填进了上表，**这与我本节开头警告的错误是同一类**。
> 要坐实这一点，需读该报告全文（见第九节待办）。

HydroClaude 的原文结论（✅ 已核实原文档）：
> "Preissmann是唯一稳定且精度可接受的求解器"
> "默认参数已经是最优的"
> "36.3%误差是当前最佳可达精度"

且它做过 80 组参数扫描（n_sections × dt × theta），结论是**加密网格或减小时间步反而更差或直接不稳定**。

**技术判断**（基于本项目已确证的 103% 质量守恒残差，HydroClaude 仅作旁证）：

两个独立实现都难以复现解析预期的水位变化，**方向上指向同一个结论** ——
这不像笔误，更像 **Preissmann 离散的固有陷阱**：

1. **Preissmann 是非守恒形式的离散。** 用 `h`（水位）作未知量的经典写法，
   其连续性方程 `∂A/∂t + ∂Q/∂x = 0` 里 `A = ∫B dh`。
   若把 `∂A/∂t` 近似成 `B·∂h/∂t`，在**变水面宽**河道中这个近似本身就引入了误差，
   且离散后**不再守恒**。

2. **本项目遗漏 `B_mid` 正是这一类的具体表现。** 此前补上 `B_mid` 的实验
   "残差不变且破坏 Newton 收敛"并已回滚 —— 这与上面的判断一致：
   **单点补一个系数解决不了，因为问题出在离散形式本身，不在某个系数。**

3. **正确方向**应是：连续性方程按**控制体**离散（对 `A` 而非对 `h` 离散），
   或直接改用守恒型有限体积格式。

4. **但要注意 HydroClaude 的反面证据**：它的 FVM 分支实测"溢出为 NaN，不可用于生产环境"，
   MOC 误差 2482.7%。**换格式不是免费的**，守恒型 FV 的干湿处理与源项平衡
   （well-balanced 性质）本身是难点。

---

## 六、建议路线（待决策，尚未动手）

按**风险从低到高**：

**路线 C（最低风险）—— 水动力交给 HEC-RAS，自己只做水质耦合**
- 非恒定潮汐工况由 HEC-RAS 非恒定流算，本项目只做 ADR 水质
- 优点：绕开已知缺陷，工期可控；`wq_engine.py`（算子分裂+RK4）本身没坏
- 缺点：需确认 HEC-RAS 授权与本机部署；`hecras_bridge.py` 的临时目录泄漏要一并修
- 待评估：ras-commander 替代手写 COM 的收益/风险

**路线 A（中风险）—— 重写连续性方程为守恒形式**
- 按控制体形式重新推导 Preissmann 离散，参考塞尔维亚论文的双扫描递推式
- **前置条件（不可跳过）**：先建解析解基准算例，否则无法证明修对了
- 借 amerta 的 Ritter / Stoker 解析解与 L1 误差范数做验证
- 风险：HydroClaude 扫了 80 组参数都没解决，不能低估

**路线 B（高风险）—— 换守恒型有限体积**
- 有 amerta 的 MUSCL-HLLC + SSP-RK2 作参考实现
- 风险：HydroClaude 实测 FVM 溢出 NaN；干湿边界与 well-balanced 源项是硬骨头
- **不建议在 11-18 截止前做**

---

## 七、赛程相关的提醒

**距作品提交截止（2026-11-18）还有 35 天。**

河道模型在本项目里是**四个标签页之一**，不是作品主线（主线是 Vue 前端 + RAG + Agent）。
按赛道评分，前端工程能力与 AI 应用完整度的权重高于数值模型的精度。

因此建议：**除非主线已完全定稿，否则不要现在投入路线 A/B。**
本调研的价值在于"万一要做，知道从哪开始、坑在哪"，而不是"现在就该做"。

---

## 八、核实状态说明

**已直接抓取原页面核实（✅）**：
`flow-sim`、`HydroClaude`（含其 `CANAL_SOLVER_PRECISION_REPORT.md` 原文）、
`amerta`、`ClearWater-riverine`、`dsm2`、`wsi`、`ras-commander`、
`github.com/topics/saint-venant?l=python` 主题页全量。

**仅来自搜索摘要，采用前需自行确认（⚠️）**：
`SWE-Solver`、`culvert-solver`、`Shallow-water`、`SvePy`、`pyHMT2D`、
`HEC-Commander`、`glm-aed`、`torrentpy`、`mogestpy`、`pyRIVE`、`AFAR-WQS`、
`Pamhyr2`、`iFlow`、`HydroMT` 及各文献的具体页码内容。

**star 数与提交数为抓取当日快照**，会变动，引用时请注明日期。

---

## 九、下一步待办

- [ ] 通读 `cve-mohd/flow-sim` 的 `src/hydromodel/preissmann.py`，评估实现质量
- [ ] 通读 HydroClaude 的 `docs/CANAL_SOLVER_PRECISION_REPORT.md` 全文（目前只抓了摘要）
- [ ] 下载 USGS WRI 97-4016，核对本项目的 Preissmann 离散与标准形式的差异
- [ ] 确认本项目当前使用的 θ 权重值
- [ ] 评估 amerta 的解析解算例能否移植为 `tests/` 下的基准
- [ ] 决策路线 A / B / C

---

*本文件是调研记录，不是结论。任何"已核实"标记只代表我抓取过该页面，
不代表我读过或验证过其源码实现。*
