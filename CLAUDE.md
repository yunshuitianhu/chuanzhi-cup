# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

备赛**传智杯全国高校 AI Web 开发挑战赛**（主题方向：AI 数据分析与可视化 / RAG 知识问答系统）。

Python AI 内核沿用作者此前参加苗圃杯的作品《水质监测溯源助手》（作者本人独立开发，见 README 的来源声明）。**本次参赛的新增工作**是：Vue 3 前端重写、FastAPI 后端层、以及对既有内核的缺陷修复。

**作品提交截止 2026-11-18。**

## 铁律

- **严禁造假。** 不得编造测试结果、不得伪造或补造 Git 提交历史、不得在文档中写入未经核实的数字。赛道规则明确要求"保留完整细粒度的 Git 提交历史，禁止一次性提交全部功能"，官网会核查原创性与开发过程可复现性。
- **改代码前必须有 90% 把握不引入新 bug 和不兼容。** 没把握的只提出来、标注风险，不动代码。
- 每改一处单独提交，并跑对应验证。改动后要能解释每一处输出差异；解释不了的差异就是引入的 bug。

## 常用命令

**后端 / 内核（Python）**

```bash
pip install -r requirements.txt

uvicorn backend.main:app --reload --port 8002   # FastAPI（接口文档 /docs）
                                                # 8000 与 8001 在本机都已有残留监听套接字，
                                                # 故改用 8002。详见下方坑 3
streamlit run app.py                            # 原 Streamlit 版（保留，用于快速自检）
python -m water_quality_mcp.server              # 以 MCP Server 方式运行（stdio）
python scripts/build_rag_index.py               # 重建 RAG 索引（首次需下 ~400MB 嵌入模型）
```

**前端（Vue）**

```bash
cd frontend
npm install
BACKEND_PORT=8002 npm run dev   # 开发服务器 :5173，/api 代理到 BACKEND_PORT
                                # 端口**必须显式给** —— vite.config.js 的默认值是 8001，而 8001 已废
npm run build      # 生产构建
npm run preview
```

> 前端只监听 **IPv6 `[::1]:5173`**，请用 `http://localhost:5173/`
> （`127.0.0.1` 连不上，见坑 3）。

**测试**（全部为**脚本式**，非 pytest；"跑单个测试"即单独运行对应脚本）

```bash
python tests/泛化能力检查.py           # 16 项 —— 换一条河还能不能用（改参数推导/边界/格式后必跑）
python tests/前端字段名检查.py         # 前端读的键名 vs 接口实际产出（改前端后必跑）
python tests/后端接口检查.py           # 16 项 —— 会话保持、接口契约、参数校验
python tests/字段名一致性检查.py       # 同上，但针对 app.py（旧界面）
python tests/数据完整性检查.py         # 10 项 —— 时间解析丢点、污染源不得编造
python tests/密钥泄漏检查.py           # 9 项 —— 密钥不入库 + .env 优先级
python tests/逆地理识别降级链.py       # 11 项
python tests/经纬度缺失值对齐.py       # 8 项
python tests/参数估计路径一致性.py     # 10 项
python tests/river_model_验收算例.py   # 15 通过 / 0 失败 / 1 项已知缺陷
python tests/前端演示_真实数据.py      # 驱动 app.py 跑完整流程（会真实调用 LLM，约 2 分钟）
python tests/前端截图.py               # Playwright 走完整流程并逐页截图（约 2 分钟）
python test_smoke.py                   # 见下方说明，当前不可信
```

`test_smoke.py:45` 有一个**已知的路径错误**：它查找 `提交文件/苗圃杯/.../河道巡航走航样本数据.xlsx`，而样本文件实际在上一级。后果是第 3 项（黑臭检测）与第 5 项（图表生成）被 `if df is not None` **静默跳过**，连计数都不进——"4 总计"是虚的。修复前不要把它当作可信的回归基线。

样本数据：`C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\无人船智能水质监测分析和因果溯源样本数据\河道巡航走航样本数据.xlsx`（1805 条，2025-08-01 10:33~13:05，雅瑶水道实际轨迹）。

## 架构

```
frontend/                  Vue 3 + Vite + Pinia + ECharts + Leaflet
   │  REST / SSE
backend/                   FastAPI —— 把内核包装成 HTTP 服务
   │
   ├─→ water_quality_mcp/  水质分析内核（25 个 MCP 工具）
   └─→ river_model/        一维水动力-水质数值模型
         └─ knowledge_base/ --(build_rag_index)--> .rag_index/  ChromaDB 向量索引

app.py                     原 Streamlit 版（等价能力，保留作对照与快速自检）

data/external/             外部数据源（第三方，公开许可，见该目录 README）
   └─ terrain/             DEM 地形样本 + 采集脚本
```

**`data/external/`** —— 为"泛化到任意河流"补数据的调研与落地。三个数据源里
**只有地形拿到了**：DEM 走 OpenTopoData 公共 API（SRTM，公有领域，无需密钥），
判据用**地形起伏**而非经纬度方框。潮汐振幅受阻于凭证申请，断面几何因
SWORD 亚洲区单文件 948 MB 而放弃。细节与许可见该目录 README。
**该目录下的数据尚未接入模型** —— 阈值未经独立验证，接入前先问。

> `data/` 顶层另有用途：`pollution_permit._find_key_sources_file` 在此查找各省
> 重点排污单位名录（xlsx）。该函数**不递归子目录**，与本目录不冲突。

### 另有一个独立分支 `research/hydro-wq-model`（**刻意不并入 main**）

水质-水动力开源方案的调研 + HEC-RAS 实测记录。

⚠️ **它的内容不在 main 上**，当前分支里看不到那个目录。要读需先取回：

```bash
git fetch origin research/hydro-wq-model
git show research/hydro-wq-model:research/hydro-wq-model/HEC-RAS实测笔记.md
# 或   git checkout research/hydro-wq-model
```

**其中最关键的一份是 `HEC-RAS实测笔记.md`** —— 记录了 HEC-RAS 7.0 非恒定流的
四处格式错误、实测跑通的潮汐工况与质量守恒数据、以及会阻塞 COM 的两类模态对话框、
还有可复用的探针与看门狗脚本。**要碰 HEC-RAS 相关代码，先读它。**

> 注意：`git ls-tree` 默认转义非 ASCII 路径（形如 `"research/...\345\256\236..."`），
> 用 `grep "^research/"` 会漏掉这些文件。加 `-c core.quotepath=false` 再筛。

**`backend/`**
- `session.py` — 按 session_id 隔离的会话状态，对应原 `st.session_state`
- `deps.py` + `main.py` 的 `session_middleware` — 会话的解析与回传（见下方坑 1）
- `routers/{system,data,analysis,charts,agent}.py` — 五组接口
- `agent/tools.py` — 工具定义与执行分发；**与 `app.py` 里的同名清单是两份**，改工具时两处都要改
- `agent/loop.py` — Function Calling 流式主循环（SSE）

**`frontend/src/`**
- `stores/session.js` — Pinia，会话状态集中管理（对应 `st.session_state`）
- `views/` — 四个视图，与 `app.py` 的四个标签页一一对应
- `components/EChart.vue` — ECharts 封装（处理尺寸为 0 时的空白图、卸载 dispose）
- `components/TraceMap.vue` — Leaflet 地图

### 前端视图 ↔ app.py 标签页对照

| 视图 | app.py | 状态 |
|---|---|---|
| `views/ChatView.vue` | tab1 智能对话 | ✅ 已通（SSE） |
| `views/ChartsView.vue` | tab2 分析图表 | ✅ 已通 |
| `views/RiverView.vue` | tab3 河道模型 | ⬜ 占位 |
| `views/TechView.vue` | tab4 技术展示 | 🟡 部分（黑臭检测/统计已通） |

### 关键领域概念

- **双标准黑臭检测**：GB3838-2002 提供连续的内梅罗评分（用于排序/聚类），《城市黑臭水体整治工作指南》(2015) 提供"是/否黑臭"定性判定（用于筛选）。两者分工明确，不要混用。
- **时空增强评分**：`增强评分 = 内梅罗 × 严重度因子 × (1 + 1.5×时间因子) × (1 + 1.5×空间因子)`。孤立单点超标被抑制，持续成片超标被放大。输出同时含 `black_score`（增强）与 `nemerow_raw`（原始）。
- **引擎选择**：HEC-RAS COM 分支**仅支持恒定流**。非恒定流（潮汐）工况必须走纯 Python 求解器——`hydro_engine.is_nonconstant()` 负责判定，不要绕过它。
- **图表改为「后端出数据、前端渲染」**：`backend/routers/charts.py` **不 import matplotlib**，只返回 JSON；ECharts / Leaflet 在前端画。这是赛题"前端工程能力 + 数据分析与可视化"的得分点，不要改回出 PNG。
- **数据源权威性**：政府公示数据 > 商业地图数据。每个外部数据源都做了独立降级（缺 Key / 网络失败 / API 改版都不应阻断整体流程）。

## 必须知道的坑

**动手前先读 `docs/` 下两份审计文档**，它们记录了已确认的缺陷、证据等级与修改方案：

- `docs/代码审计与修改方案.md` — 第一轮，含修复进展表
- `docs/代码审计-第二轮发现.md` — 第二轮（全文通读后）

**1. 会话靠 `X-Session-Id` 请求头传递 —— 后端必须回传**

HTTP 是无状态的。前端在请求头带 `X-Session-Id`，**后端必须在响应头回传**，否则前端永远不知道自己在用哪个会话，于是每个请求都新建会话。曾因此出现「上传成功 1805 条，但状态始终为空、按钮点不动」。修复见 `backend/main.py` 的 `session_middleware`（放在中间件层而非依赖里，才能覆盖包括错误响应在内的全部出口）。

写后端测试时**不要手动替客户端带 `X-Session-Id`** 就把用例写完 —— 那会掩盖客户端侧的问题。`tests/后端接口检查.py` 里专门保留了一条「不带 id 时应查不到数据」的用例守这个语义。

**2. 导入必须用全路径，否则会加载到 `D:\aiRAG` 的另一份代码**

本机存在一个指向 `D:\aiRAG\water_quality_mcp\src\water_quality_mcp` 的 **editable 安装**。因此：

| 写法 | 实际加载 |
|---|---|
| `from water_quality_mcp.src.water_quality_mcp.X import ...` + 项目根在 `sys.path` | ✅ 本项目 |
| `import water_quality_mcp` + `src` 在 `sys.path` | ✅ 本项目 |
| `from water_quality_mcp.X import ...` + 只有项目根在 `sys.path` | ❌ **`D:\aiRAG`** |

`backend/__init__.py` 已处理 sys.path 并统一走全路径。**写新代码时请沿用**，不要图省事写短路径。

项目并未真正 pip 安装，全靠 `sys.path` 操作。`.rag_index` 由 `rag_engine.py` 以 `../../../` 相对 `__file__` 解析；多处输出文件（`trace_map.html`、`_uploaded_data.xlsx`、`river_*.png`）是 **CWD 相对**路径。

**3. 本机环境的三个特性（会干扰测试与调试）**

- **Vite 只监听 IPv6 `[::1]:5173`**，用 `127.0.0.1` 访问不到
- **curl / urllib 默认走系统代理**（Clash，`127.0.0.1:7890`），访问本地服务返回 502。
  命令行加 `--noproxy '*'`；Python 用
  `urllib.request.build_opener(urllib.request.ProxyHandler({}))`
- **用过的后端端口会堆积无法回收的残留监听套接字**：`netstat` 显示有 PID 在
  LISTENING，但该 PID 的进程已不存在，`taskkill` 报"找不到进程"。表现为
  **改了后端代码却不生效** —— 请求被残留套接字背后的旧实例接走了，且不影响
  HTTP 200 的返回，极具迷惑性。**8000 与 8001 上都已实测复现**（2026-10-14），
  所以「换到 8001」这个旧建议已失效，**现在用 8002**。

  排查：`Get-NetTCPConnection -LocalPort <port> -State Listen` 后
  `Get-Process -Id <pid>`，若进程不存在即为残留。Git Bash 的 `netstat`
  在此场景下视图不可靠，以 PowerShell 的 `Get-NetTCPConnection` 为准。
  **换端口是最快的解法**，别去纠缠怎么杀掉它。

- **已验证服务跑的是哪版代码，不能靠"应该生效了"**：因为上一条，旧实例会安静地
  继续应答。比对本机接口的**行为**才算数 —— 例如上传一份别名列名的 CSV，
  返回 `rows=1` 才说明跑的是含列名兼容修复的版本。

**4. 字段名不匹配是系统性问题，不是孤例 —— 有两个检查器，别只跑一个**

调用层曾按英文 `snake_case` 键名读取**中文键名**的产出，`.get()` 取不到时静默返回默认值、不报错。已修复 11 处（详见第二轮审计文档 A 节）。

**两个检查器分工不同，改哪边就跑哪个：**

| 改了 | 跑 |
|---|---|
| `app.py`（旧界面） | `tests/字段名一致性检查.py` |
| `frontend/`（新前端） | `tests/前端字段名检查.py` |
| 后端接口的返回字段 | 两个都要跑 |

新前端曾因**没有对应的检查器**而重蹈覆辙：`CrossValidation.vue` 读了
`alignment`/`agreement`/`correlations`/`findings` 四个英文键，后端一个都没有，
四个指标卡全空白、接口却返回 200。加检查器之后才系统性地堵住。

写这类检查器时注意**避免假阳性**（假阳性会直接毁掉工具的可信度）：
- 必须取该组件依赖的**全部**接口的键之并集。RiverView 用四个接口，
  只拿一个比会误报十几个键
- 只在条件分支出现的键、组件里有 `||` 兜底的键、以及本地常量
  （如 Leaflet 图层名），要显式豁免并注明原因

**5. 按钮禁用必须说明原因**

前端出现过一处 `:disabled="!query.trim()"` —— 输入框为空时按钮禁用，但
没有任提示，用户只看到"按钮点不动"。原 app.py 的行为是
`query = tech_rag_query or "黑臭水体治理"`，**空输入走默认查询**。

结论：需要前置条件的按钮，要么给默认值兜底、要么附一行说明；
**一个看不出原因的死按钮，比一个用默认值兜底的按钮差得多。**
改动了前端全部 16 处 `:disabled` 逐个审过，其余都有合理理由
（loading 态、缺数据但有提示、聊天发送键的通用惯例）。

**6. 已知未修复的缺陷，不要"顺手"改**

- `pure_py_hydro.py` 连续性方程遗漏水面宽 `B_mid`，非恒定工况质量不守恒（残差达蓄水率 103%）。补上 `B_mid` 的实验已回滚——残差不变且破坏 Newton 收敛。**需要重新推导 Preissmann 离散，非外科修复可解。**
- `analysis_config.py` 有两个独立单例（`get_config()` 管理的 `_DEFAULT_CONFIG` 与模块级 `DEFAULT_CONFIG`）。全库无人调用 `set_config`，属潜伏隐患。**改动零收益、风险非零，建议不动。**
- `TIDAL_AMPLITUDE_BY_REGION` 的 8 个具体河口键当前不可达（只取 `"default"`）。原因与影响已写在 `regional_defaults.py` 表旁。

**7. 不要在作品说明中美化能力**

按**本仓库实际代码**核对过：溯源是 **4 个加权源**（不是文档说的 6 源）；ESA 土地利用与降雨模块在本仓库中**不存在**；视频分析只有整帧 Laplacian + HSV（不是文档说的 5 维 ROI/光流/水色/纹理/漂浮物）。

**河道模型的潮汐工况，准确说法是**（2026-10-14 实测后修订）：

- 潮汐工况目前由**纯 Python 求解器**处理，实测**质量不守恒**（体积守恒残差达蓄水率的 103%）。
  这**不是"选了 Preissmann 格式"的错** —— HEC-RAS 用的是同一类格式（1D Unsteady
  Finite Difference），质量守恒误差仅 **0.0116%**。差别在离散写法。
- **HEC-RAS 的非恒定流其实可用**。`hecras_bridge.py` 注释所称的"7.0 格式不兼容"
  经实测推翻：真实原因是生成的输入文件有四处格式错误，HEC-RAS 每次都判为恒定流方案
  并静默调用 `RasSteady.exe`。四处错误与修法见 `research/hydro-wq-model/HEC-RAS实测笔记.md`。
- 但 `hydro_engine.py:87` **强制**把非恒定流推给纯 Python
  （`engine = "pure_python" if is_nonconstant(...) else detect_engine()`），
  所以**修好格式也不会改变任何潮汐输出**。要切换得单独决策 ——
  代价是改变全部潮汐结果、HEC-RAS 成为必需依赖、以及模态对话框会挂住 FastAPI 请求。
- **几何是人工规则梯形，不是实测断面**。即便切换引擎，结果也只是"数值上正确"，
  不是"雅瑶水道的真实模型"。

> 注：`D:\aiRAG`（见坑 2）里**有** `land_use.py` 与 `precipitation.py`，但那是另一份、未纳入本仓库的代码。写说明文档时以本仓库为准。

**8. 泛化的实质：靠一张 9 条河的手工表撑着**

本项目的目标不是"只服务雅瑶水道"，而是"喂任意河流的数据都能跑"。但审计实测：

- 真实数据上 **10 个参数里 9 个来自 `KNOWN_RIVERS_GD`** —— 一张只有 9 条
  **广东**河流的手工表。出了表就退化到区域默认值。
- `classify_region` 穷举中国全境网格，**99.3% 判为 lowland**，
  `foothill`/`mountain`/`coastal` **一次都没返回过**（尽管 docstring 声称会）
  → 山区河道坡降错 20~33 倍。
- 数据入口原先只认一种列命名，别名/英文列名一律 KeyError 崩栈。

已修复的部分见 `tests/泛化能力检查.py`（16 项）。**未修复的根本瓶颈是断面几何**：
HEC-RAS 工程用的是人工规则梯形，不是实测断面 —— 这是数据问题，不是代码问题。

> **改这类代码时的设计约束**：修复应做到"对本项目样本数据输出不变，
> 只在换河时才不同"。第 1/2/3/6 项修复都是这样设计的（桩号跟着 `length_m`、
> 参数回落到 config 而其默认值恰等于原硬编码常数），这样不会打乱既有演示结果。

**9. 列映射这类"两份实现"必须合并**

`app.py` 与 `loaders.py` 曾各有一份**独立的**列名映射与时间解析。修复列名兼容时
只改了 `loaders`，`app.py` 仍是旧的 —— 因为两边是两套代码。
现已统一走 `loaders.normalize_columns()`。
**发现同类重复实现时，先合并再改，否则必然漏改一边。**

**10. `.rag_index/chroma.sqlite3` 每查一次 RAG 就会被改动**

该文件（1.8 MB 二进制）已被 git 跟踪，但**每次检索都会写它** ——
实测同一次查询前后 md5 不同。后果：**任何人查一次 RAG，工作区就变脏**。

处理方式：跑完 RAG 相关测试后，提交前先 `git checkout -- .rag_index/chroma.sqlite3`。
**若后端正在运行，该文件被占用，回退会报 `unable to unlink ... Invalid argument`**
—— 必须先停掉后端进程再回退。

> 已向用户提出过这个取舍但**尚未决定**：继续跟踪 = 评委开箱能跑 RAG，代价是工作区
> 常脏；移出跟踪 + gitignore = 工作区干净，代价是评委需自行跑
> `scripts/build_rag_index.py`（要下载约 400 MB 嵌入模型）。改动前先问。

**11. 验证"修复是否生效"要用行为，不要用进程状态**

本机同时存在：残留监听套接字（坑 3）、`--reload` 的自动重载、以及多个端口上
跑着不同版本的实例。**"服务起来了"不等于"跑的是新代码"。**
要证明新代码生效，必须观察**只有新代码才有的行为**。

## 与苗圃杯原项目的关系

基线代码来自 `C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\提交文件\`（**无 Git**，冻结的只读参照）。其中 `提交文件/` 与 `1/提交文件/` 的 **Python 源码完全相同**，`提交文件/` 是唯一权威版本。

原仓库 `yunshuitianhu/water-quality-monitoring` **不要使用**：它只有 5 次提交、首次即 `Initial commit` 提交全部功能，正是赛道禁止的形态，且缺失最终版的大量模块。本仓库首次提交已显式标注了代码来源，**绝不补造提交历史**。

本机还有 `D:\aiRAG\`（时间戳 2026-06-18，晚于苗圃杯提交的 06-03）。它是同一项目的后续开发版本，多出 `land_use.py`、`precipitation.py`、真实 ESA 数据与名录，但**同样存在本仓库已修复的全部缺陷**。**尚未决定是否并入**——改动前先与用户确认。
