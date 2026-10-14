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

uvicorn backend.main:app --reload --port 8001   # FastAPI（接口文档 /docs）
                                                # 端口 8000 在本机有残留套接字问题，见下方坑 3
streamlit run app.py                            # 原 Streamlit 版（保留，用于快速自检）
python -m water_quality_mcp.server              # 以 MCP Server 方式运行（stdio）
python scripts/build_rag_index.py               # 重建 RAG 索引（首次需下 ~400MB 嵌入模型）
```

**前端（Vue）**

```bash
cd frontend
npm install
npm run dev        # 开发服务器 :5173，已配 /api 代理到 :8000
npm run build      # 生产构建
npm run preview
```

**测试**（全部为**脚本式**，非 pytest；"跑单个测试"即单独运行对应脚本）

```bash
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
```

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
- **8000 端口上会堆积无法回收的残留监听套接字**：`netstat` 显示有 PID 在 LISTENING，
  但该 PID 的进程已不存在，`taskkill` 报"找不到进程"。表现为**改了后端代码却不生效**
  —— 请求被残留套接字背后的旧实例接走了，且不影响 HTTP 200 的返回，极具迷惑性。
  排查方法：`Get-NetTCPConnection -LocalPort <port> -State Listen` 后
  `Get-Process -Id <pid>`，若进程不存在即为残留。**换端口是最快的解法**
  （`BACKEND_PORT=8001 npm run dev`）。Git Bash 的 `netstat` 在此场景下视图不可靠，
  以 PowerShell 的 `Get-NetTCPConnection` 为准。

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

按**本仓库实际代码**核对过：溯源是 **4 个加权源**（不是文档说的 6 源）；ESA 土地利用与降雨模块在本仓库中**不存在**；视频分析只有整帧 Laplacian + HSV（不是文档说的 5 维 ROI/光流/水色/纹理/漂浮物）；河道模型的水动力部分**不可用于潮汐工况**。

> 注：`D:\aiRAG`（见坑 2）里**有** `land_use.py` 与 `precipitation.py`，但那是另一份、未纳入本仓库的代码。写说明文档时以本仓库为准。

## 与苗圃杯原项目的关系

基线代码来自 `C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\提交文件\`（**无 Git**，冻结的只读参照）。其中 `提交文件/` 与 `1/提交文件/` 的 **Python 源码完全相同**，`提交文件/` 是唯一权威版本。

原仓库 `yunshuitianhu/water-quality-monitoring` **不要使用**：它只有 5 次提交、首次即 `Initial commit` 提交全部功能，正是赛道禁止的形态，且缺失最终版的大量模块。本仓库首次提交已显式标注了代码来源，**绝不补造提交历史**。

本机还有 `D:\aiRAG\`（时间戳 2026-06-18，晚于苗圃杯提交的 06-03）。它是同一项目的后续开发版本，多出 `land_use.py`、`precipitation.py`、真实 ESA 数据与名录，但**同样存在本仓库已修复的全部缺陷**。**尚未决定是否并入**——改动前先与用户确认。
