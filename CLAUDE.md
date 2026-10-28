# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

备赛**传智杯全国高校 AI Web 开发挑战赛**（主题方向：AI 数据分析与可视化 / RAG 知识问答系统）。

Python AI 内核沿用作者此前参加苗圃杯的作品《水质监测溯源助手》（作者本人独立开发，见 README 的来源声明）。
**本次参赛的新增工作**是：Vue 3 前端重写、FastAPI 后端层、以及既有内核的缺陷修复与示例/交付能力。

**作品提交截止 2026-11-18。**

## 铁律

- **严禁造假。** 不得编造测试结果、不得伪造或补造 Git 提交历史、不得在文档中写入未经核实的数字。
  赛道规则要求保留完整、细粒度的 Git 提交历史，禁止一次性提交全部功能；官网会核查原创性与开发过程可复现性。
- **改代码前必须有 90% 把握不引入新 bug 和不兼容。** 没把握的只提出来、标注风险，不动代码。
- 每改一处单独提交，并跑对应验证。改动后要能解释每一处输出差异；解释不了的差异就是引入的 bug。
- 不得把仿真数据写成现场实测，不得把热点数量写成污染源数量，不得把参数估计写成概率或责任认定。

## 当前状态与入口

2026-10-28 的交付状态（以实际命令和 `docs/` 记录为准，不要只引用本节）：

- N01—N03 内置六组仿真示例、一键载入、契约/打包已完成本机受控验收（N03 的 S-T13/S-T21/S-T26 未单独自动化）；
- N04/N05 配置说明与 Windows 环境为部分完成；N06 河道引导、N07 字号为部分/浏览器验收；N08 只完成少量低风险项；
- 最近记录：622 项 Python（干净 CI venv）、361 项前端、22 份真实响应契约、`check_ci` 通过、交付副本 6 例/1801 行/`synthetic`；
- 外部核验 R01—R03 已修复：项目发布失败回滚、示例资源 503 边界、打包未跟踪文件门禁；
- 新交付 ZIP 为 `work/delivery_20261028_r2.zip`：revision `4cc2a1a`、580 文件、SHA-256 `7577e578…`；旧包 revision `b075bd0` 仅作历史记录；`work/` 被忽略，ZIP 需单独发送。

**先读**：

| 文档 | 用途 |
| --- | --- |
| `docs/交付说明_20261028.md` | 交付物、安装运行、能力边界、验收清单、未验证项 |
| `docs/任务队列.md` | Q01—Q17 与 N01—N08 的逐项状态、日期、限制 |
| `docs/内置仿真示例与一键载入实施记录_20261027.md` | 示例/载入/契约/打包的实现范围、命令、提交号 |
| `docs/Windows安装与运行环境验证_20261028.md` | 实际 Python/Node、干净 venv、启动器、带空格路径 |
| `docs/代码审计与修改方案.md`、`docs/代码审计-第二轮发现.md` | 已确认缺陷、证据等级、修改方案 |

> 根目录的 `AGENTS.md` 是已被 `.gitignore` 忽略的旧副本，不作为本项目指令；以本文件和 `docs/` 为准。

## 常用命令

### 环境与安装

本机实际验证环境：Windows 11、Python **3.14.0**、Node **24.15.0**。`pyproject.toml` 声明 `requires-python >=3.11`；
3.12 是早期锁定环境记录，当前机器没有该解释器，不要写“当前使用 3.12 验证”。

```bash
python -m pip install -r requirements-lock.txt   # 完整运行（含 Streamlit、ChromaDB、嵌入模型）
python -m pip install -r requirements-ci.txt     # 离线核心回归/示例/手工模型（不含 RAG 可选依赖）
```

产品锁用 `opencv-python`，CI 用 `opencv-python-headless`；同一环境只装一种，不要两者都装。

### 后端 / 内核

```bash
python scripts/serve_web.py --port 5174          # 推荐：网页 + API 同一端口（默认 5174）
python scripts/serve_web.py --check --port 5199  # 只做启动预检，不启动服务
uvicorn backend.main:app --reload --port 8002    # 仅 API 开发（接口文档 /docs）
streamlit run app.py                             # 原 Streamlit 版（保留作对照/快速自检）
python -m water_quality_mcp.server               # MCP Server（stdio）
python scripts/build_rag_index.py                # 重建 RAG 索引（缺模型缓存时可能下载数百 MB）
python scripts/prepare_sample_data.py            # 从 仿真数据/ 重新生成示例 CSV；默认不覆盖不同内容
```

Windows 入口：`start_web.cmd` / `start_web.cmd --check --port 5199`。指定解释器：

```bat
set "WQ_PYTHON=C:\实际安装目录\python.exe"
start_web.cmd
```

```powershell
$env:WQ_PYTHON = 'C:\实际安装目录\python.exe'
& '.\start_web.cmd'
```

### 前端

```bash
cd frontend
npm install
BACKEND_PORT=8002 npm run dev    # 开发服务器 :5173，/api 代理到 8002
npm run build                    # 生产构建到 frontend/dist
npm run check                    # typecheck + node --test + build（推荐门禁）
```

端口**必须显式给** `BACKEND_PORT`：`vite.config.js` 默认值是已废弃的 8001。
开发服务器只监听 IPv6 `[::1]:5173`，请用 `http://localhost:5173/`；生产 `serve_web.py` 监听 `127.0.0.1`。

### 测试与门禁

```bash
# Python 全量（当前 622 项；tests 不是 package，不要用 python -m unittest tests.xxx）
python -m unittest discover -s tests -p "test_*.py"

# 单个 unittest 文件
python -m unittest discover -s tests -p "test_sample_catalog_regressions.py" -v

# 旧脚本式检查（非 pytest；改哪块跑哪块，项数以实际输出为准）
python tests/泛化能力检查.py          # 换一条河还能不能用
python tests/前端字段名检查.py        # 前端读的键名 vs 接口实际产出
python tests/后端接口检查.py          # 会话保持、接口契约、参数校验
python tests/字段名一致性检查.py      # app.py（旧界面）
python tests/数据完整性检查.py        # 时间解析丢点、污染源不得编造
python tests/密钥泄漏检查.py          # 密钥不入库 + .env 优先级
python tests/river_model_验收算例.py  # 含 1 项已知缺陷（潮汐质量不守恒）

# 前端
npm --prefix frontend run check       # 361 项 + TS/SFC + 构建
cd frontend && node --test tests/keyState.test.mjs   # 单个前端测试

# 跨平台离线门禁（含全量 unittest、字段/泛化/后端/密钥检查）
python scripts/check_ci.py

# 从真实 TestClient 响应生成 TS 契约（输出被 gitignore）
python scripts/export_frontend_contracts.py
```

`test_smoke.py:45` 有已知路径错误：黑臭检测与图表生成会被 `if df is not None` 静默跳过，
修复前不要把“4 总计”当作可信回归基线。

### 交付包

```bash
# 必须：源码全部提交、工作区无未提交源码、frontend/dist 已构建
python scripts/package_delivery.py --output work/delivery_<日期>.zip

# 解压到新的空目录（无 .env/.mcp.json）后，在该副本内运行
python scripts/verify_delivery.py --output-dir <新的空目录>
```

打包器使用 `git archive HEAD`，拒绝未提交源码、未跟踪文件（`work/` 等运行目录除外）和被跟踪的本机配置；
不包含 `.env`、用户项目、模型缓存、`.rag_index/`、原始 `仿真数据/`。包内 `delivery-manifest.json` 记录实际 revision。

### 浏览器验收

先启动一个**隔离项目目录**的服务，避免把演示项目写进用户项目库：

```bash
WQ_PROJECT_HOME=work/browser_projects python scripts/serve_web.py --port 5183
python tests/browser_sample_acceptance.py --url http://127.0.0.1:5183/chat
python tests/browser_readability_acceptance.py --url http://127.0.0.1:5183/chat \
  --out docs/validation/readability_<日期> --report browser_readability_<日期>.json
python tests/browser_river_guide_acceptance.py --url http://127.0.0.1:5183/river
```

浏览器脚本需要 Playwright/Chromium；不要把 5180/用户默认项目目录当作隔离环境。

## 架构

```text
frontend/                  Vue 3 + Vite + Pinia + ECharts + Leaflet
   │  REST / SSE
backend/                   FastAPI —— 会话、项目、数据/示例、Agent、报告
   │
   ├─→ water_quality_mcp/  水质分析内核（MCP 工具、加载、检测、RAG、报告）
   └─→ river_model/        一维水动力-水质数值模型
         └─ knowledge_base/ --(build_rag_index)--> .rag_index/  ChromaDB 向量索引

app.py                     原 Streamlit 版（等价能力，保留作对照）
scripts/serve_web.py       生产入口：静态前端 + /api，同一端口
backend/web.py             内存快照 index/assets；四页入口；未知路径 404
```

`data/external/` 目前只有 DEM 地形样本（OpenTopoData/SRTM），**尚未接入模型**；阈值未独立验证，接入前先问。
`data/` 顶层另有用途：`pollution_permit._find_key_sources_file` 在此查找各省重点排污单位名录（不递归子目录）。

### 后端主干

- `backend/main.py` — 注册全部 `/api` 路由；`session_middleware` 解析并回传 `X-Session-Id`。
- `backend/session.py` — `Session` 持有数据、分析结果、消息、项目状态、`operation_lock`、revision；
  `STORE` 是按 session_id 的内存字典（进程内，未做 TTL/容量，见 N08.2）。
- `backend/deps.py` — 会话依赖；`get_exclusive_session` 用非阻塞 `operation_lock` 防止并发覆盖。
- `backend/data_service.py` — 上传与示例共用的 `parse_monitoring` / `apply_monitoring`；
  候选数据先解析，成功后才应用到会话。
- `backend/sample_catalog.py` + `data/samples/` — 只按 `manifest.json` 白名单读取示例；
  校验文件名、CSV 后缀、“仿真”、实际字节数、SHA-256、路径逃逸；清单/CSV 走 `_read_bounded`
  （2MB/20MB 上限），权限/消失/超限统一为 503 资源错误，不返回空列表。
- `backend/routers/data.py` — 上传/视频/状态 + `GET /api/data/samples`、`POST /api/data/samples/{id}/load`；
  载入先保存当前项目，再以候选方式创建独立演示项目（不提前改绑定）；`detect=true` 的筛查失败不回滚已载入数据；
  `df.attrs['sample_meta']` 随项目保存。
- `backend/projects.py` + `backend/project_codec.py` — SQLite 项目索引 + 版本化 `.wqproj` 快照；
  当前 `schema_version=4`，包含 `media_assets`/`media/<asset_id>`；schema 1—3 可读并补空附件。
  新项目发布走 `write(..., bind_session=False)` → `activate_new` → `prepare_snapshot`/`bind`/`publish_snapshot`；
  失败恢复旧绑定，`discard_candidate` 只删本次未绑定候选。
- `backend/media_assets.py` + `backend/routers/media.py` — 图片真实解码、规范化、哈希、按会话隔离；
  `backend/vision.py` 把选帧/附件加载成多模态 `image_url`，图片字节不写入 SSE/历史/`full_messages`。
- `backend/project_data.py` — 只读业务数据目录/分页/统计，供 `read_project_data` 等工具使用。
- `backend/agent/loop.py` — DeepSeek Function Calling + SSE 主循环；每轮工具结果全部返回后才追加图片消息；
  `error_text.py` 把传输/服务异常转成可操作中文提示，但保留 Agent 自身的中文校验错误；`response_style.py` 共享表达规范。
- `backend/agent/tools.py` — 工具定义与执行分发；与 `app.py` 的同名清单是两份，改工具时两处都要改。
- `backend/side_chat.py` + `backend/routers/side.py` — 侧边提问/解释线程；当前不执行数据工具，不改主报告/任务。
- `backend/routers/rag.py` — 知识库状态与检索；状态返回 `stage`（依赖/索引读取/索引构建/模型加载/实际检索），
  不返回本机绝对路径；缺依赖/索引/检索失败分别显示。
- `backend/routers/system.py` — `/api/config` 返回 `keys` 与 `key_state`（configured/verified/notice），
  只说明“是否填写”，不返回值。

### 前端主干

- `frontend/src/stores/session.js` — Pinia 中央状态：会话、数据、项目、示例目录、问题上下文、图片上传、
  侧边线程、草稿；所有异步动作做 workspace/project/revision 版本检查，丢弃迟到响应。
- `frontend/src/api/client.js` — 统一请求封装，带 `X-Session-Id`；SSE 单独处理。
- `frontend/src/types/contracts.ts` — 后端真实响应的 TS 类型；`scripts/export_frontend_contracts.py` 生成赋值检查。
- `frontend/src/views/` — `ChatView`（SSE、图片上下文、可折叠分析区）、`ChartsView`、`RiverView`（恒定流分步引导/播放）、`TechView`（RAG、国控、模型验证）。
- `frontend/src/components/SafeMarkdown.vue` — 报告/回答渲染与选文；`EChart.vue`、`TraceMap.vue`、`PollutionPlayback.vue`。
- `frontend/src/utils/questionContext.js`、`agentStream.js`、`keyState.js`、`ragStage.js` — 上下文、事件解码、配置/阶段文案。

### 数据与模型链路

- 上传/示例 → `load_data` 生产加载器 → `Session.df`（含观测掩码/插值标记、`data_identity`、`sample_meta`）。
- 分析 → `find_black_spots` / 交叉印证 → `Session.black_spots` 等 → 图表由后端出 JSON、前端 ECharts 渲染。
- Agent → 工具结果 + 证据包 → SSE → `full_messages`/`messages`；报告正文由服务端构造事实，模型只写解释。
- 河道模型 → `RiverConfig`/手工参数 → `river_model` 一维恒定流 → 项目 `river_state`/结果；潮汐仍走纯 Python，
  质量不守恒是已知缺陷，不要开放为正式 Web 路径。
- RAG → 可选依赖 + 嵌入模型 + `.rag_index` → 检索片段 + 引用 ID；相似度不是结论概率。

### 前端视图 ↔ app.py 标签页

| 视图 | app.py | 状态 |
|---|---|---|
| `views/ChatView.vue` | tab1 智能对话 | ✅ SSE、图片上下文、可折叠分析区 |
| `views/ChartsView.vue` | tab2 分析图表 | ✅ 图表/质控/统计 |
| `views/RiverView.vue` | tab3 河道模型 | ✅ 恒定流分步引导、播放、参数来源 |
| `views/TechView.vue` | tab4 技术展示 | ✅ RAG、国控查询、模型验证/技术说明 |

### 关键领域概念

- **双标准黑臭检测**：GB3838-2002 提供连续的内梅罗评分（排序/聚类）；《城市黑臭水体整治工作指南》(2015)
  提供“是/否黑臭”定性判定（筛选）。两者分工明确，不要混用。
- **时空增强评分**：`增强评分 = 内梅罗 × 严重度因子 × (1 + 1.5×时间因子) × (1 + 1.5×空间因子)`。
  输出同时含 `black_score`（增强）与 `nemerow_raw`（原始）；热点数量不是污染源数量。
- **引擎选择**：`hydro_engine.is_nonconstant()` 负责判定；HEC-RAS COM 分支仅支持恒定流，
  非恒定流强制走纯 Python；不要绕过判定。HEC-RAS 实测笔记在 `research/hydro-wq-model` 分支。
- **图表后端出数据、前端渲染**：`backend/routers/charts.py` 不 import matplotlib，只返回 JSON；
  这是赛题可视化得分点，不要改回 PNG。
- **数据源权威性**：政府公示数据 > 商业地图数据；每个外部数据源独立降级（缺 Key/网络失败/API 改版不阻断整体流程）。

## 必须知道的坑

**1. 会话靠 `X-Session-Id` 请求头传递 —— 后端必须回传**

HTTP 无状态。前端在请求头带 `X-Session-Id`，后端必须在响应头回传，否则每个请求都新建会话。
修复在 `backend/main.py` 的 `session_middleware`（中间件层，覆盖错误响应）。写测试时不要手动替客户端带 id
就把用例写完——`tests/后端接口检查.py` 保留“不带 id 应查不到数据”的用例守这个语义。

**2. 导入必须用全路径，否则会加载到 `D:\aiRAG` 的另一份代码**

本机存在指向 `D:\aiRAG\water_quality_mcp\src\water_quality_mcp` 的 editable 安装：

| 写法 | 实际加载 |
|---|---|
| `from water_quality_mcp.src.water_quality_mcp.X import ...` + 项目根在 `sys.path` | ✅ 本项目 |
| `import water_quality_mcp` + `src` 在 `sys.path` | ✅ 本项目 |
| `from water_quality_mcp.X import ...` + 只有项目根在 `sys.path` | ❌ `D:\aiRAG` |

`backend/__init__.py` 已处理 sys.path 并统一走全路径；新代码沿用。项目未真正 pip 安装，`.rag_index` 由
`rag_engine.py` 以相对 `__file__` 解析；`trace_map.html`、`_uploaded_data.xlsx` 等输出是 CWD 相对路径。

**3. 本机环境的特性（会干扰测试与调试）**

- 开发 Vite 只监听 IPv6 `[::1]:5173`，用 `127.0.0.1` 访问不到；生产 `serve_web.py` 监听 `127.0.0.1`。
- curl/urllib 默认走系统代理（Clash `127.0.0.1:7890`），访问本地服务返回 502；加 `--noproxy '*'` 或禁用代理。
- 用过的后端端口会堆积无法回收的残留监听套接字：`netstat` 显示 LISTENING 但 PID 进程不存在，
  改了后端代码却不生效。`Get-NetTCPConnection -LocalPort <port> -State Listen` + `Get-Process -Id <pid>` 核对；
  **换端口是最快的解法**，不要纠缠杀进程。8000/8001 已实测残留，现在 API 开发用 8002、生产入口用 5174。
- **验证“修复是否生效”要用行为，不要用进程状态**：观察只有新代码才有的行为，不能只看 HTTP 200 或“服务起来了”。

**4. 字段名不匹配是系统性问题 —— 两个检查器别只跑一个**

| 改了 | 跑 |
|---|---|
| `app.py`（旧界面） | `tests/字段名一致性检查.py` |
| `frontend/`（新前端） | `tests/前端字段名检查.py` |
| 后端接口返回字段 | 两个都跑 |

写检查器避免假阳性：必须取组件依赖的**全部**接口键之并集；条件分支键、`||` 兜底键、本地常量要显式豁免并注明原因。

**5. 按钮禁用必须说明原因**

需要前置条件的按钮，要么给默认值兜底、要么附一行说明；一个看不出原因的死按钮，比默认查询的按钮差得多。
原 `app.py` 空输入走默认查询 `"黑臭水体治理"`；前端聊天发送键的通用惯例除外。

**6. 已知未修复的缺陷，不要“顺手”改**

- `pure_py_hydro.py` 连续性方程遗漏水面宽 `B_mid`，非恒定工况质量不守恒（残差达蓄水率 103%）。
  补 `B_mid` 的实验已回滚（残差不变且破坏 Newton 收敛），需要重新推导 Preissmann 离散。
- `analysis_config.py` 有两个独立单例（`get_config()` 的 `_DEFAULT_CONFIG` 与模块级 `DEFAULT_CONFIG`），
  全库无人调用 `set_config`；改动零收益、风险非零，建议不动。
- `TIDAL_AMPLITUDE_BY_REGION` 的 8 个具体河口键当前不可达（只取 `"default"`），原因写在 `regional_defaults.py` 表旁。

**7. 不要在作品说明中美化能力**

按本仓库实际代码核对：溯源是 **4 个加权源**（不是 6 源）；ESA 土地利用与降雨模块在本仓库**不存在**；
视频分析只有整帧 Laplacian + HSV（不是 5 维 ROI/光流/水色/纹理/漂浮物）。

河道模型准确说法：潮汐由纯 Python 求解器处理，实测质量不守恒（体积残差达蓄水率 103%）；
HEC-RAS 非恒定流其实可用，`hecras_bridge.py` 注释的“7.0 格式不兼容”经实测推翻，真实原因是输入文件四处格式错误；
但 `hydro_engine.is_nonconstant()` 的分支强制非恒定流走纯 Python，修好格式也不会改变潮汐输出；几何是人工规则梯形，不是实测断面。
即便切换引擎，结果只是“数值上正确”，不是“雅瑶水道的真实模型”。

**8. 泛化的实质：靠一张 9 条河的手工表撑着**

真实数据上 10 个参数里 9 个来自 `KNOWN_RIVERS_GD`（只有 9 条广东河流）；`classify_region` 穷举中国网格，
99.3% 判为 lowland，foothill/mountain/coastal 一次都没返回过 → 山区坡降错 20~33 倍。
数据入口原先只认一种列命名，别名/英文列名会 KeyError。已修复部分见 `tests/泛化能力检查.py`（16 项）。
**根本瓶颈是断面几何**（HEC-RAS 工程用人工规则梯形），是数据问题不是代码问题。

改这类代码的设计约束：**对本项目样本数据输出不变，只在换河时才不同**（桩号跟 `length_m`、参数回落 config
且默认值等于原硬编码常数）。

**9. 列映射这类“两份实现”必须合并**

`app.py` 与 `loaders.py` 曾各有一份列名映射/时间解析；修复时只改 `loaders`，`app.py` 仍是旧的。
现已统一走 `loaders.normalize_columns()`。发现同类重复实现时，先合并再改，否则必然漏改一边。

**10. `.rag_index/chroma.sqlite3` 每查一次 RAG 就会被改动**

该文件被 Git 跟踪，但每次检索都会写它；跑完 RAG 相关测试后，提交前先
`git checkout -- .rag_index/chroma.sqlite3`。若后端正在运行，文件被占用会报 `unable to unlink ... Invalid argument`
——必须先停后端再回退。是否移出 Git 尚未决策，见 N08.8；不要擅自删除或改跟踪状态。

**11. 示例 CSV 与 manifest 是字节级契约，不能用加载器结果重新生成**

`data/samples/*.csv` 必须从原始 Excel 的监测工作表直接导出，**不能先 `load_data` 再导出**——加载器会插值、
清洗、补坐标，抹掉 S06 的缺测。manifest 记录实际字节的 SHA-256/大小/行数；`.gitattributes` 将 CSV 标记为 binary。
不要手改 CSV 或 manifest；需要重生成时跑 `scripts/prepare_sample_data.py`，它默认不覆盖不同内容。
示例身份固定为 `synthetic`，`sample_meta` 随项目保存，不要改客户端传入的身份。

**12. 示例载入/项目发布：候选写入不能提前改绑定**

`POST /api/data/samples/{id}/load` 先解析候选 CSV 并保存当前项目，再用
`write(..., new=True, bind_session=False)` 创建候选；`activate_new` 回读并物化快照后才 `bind` + 发布内存。
任何一步失败都要恢复旧绑定，并只清理本次创建且未被绑定的候选（`discard_candidate`）。不要改回
“`write(new=True)` 直接绑定、之后再读快照/调 `apply_snapshot`”——那样失败后内存仍是旧项目，重启却会打开新项目。
空项目创建（`from_current=false`）、导入和打开共用同一发布路径；筛查失败只标记“数据已载入、检测未完成”，
不回滚数据；S01 零热点是有效结果。载入/检测不要暗中触发收费 AI、实时 POI 或联网分析。

**13. 交付打包只认已提交源码**

`scripts/package_delivery.py` 用 `git archive HEAD`，拒绝未提交源码、未跟踪文件（`work/`、用户项目、
`.rag_index/`、`仿真数据/` 等运行目录除外）和被跟踪的本机配置；`work/` 在 `.gitignore` 中，ZIP 需单独发送。包内 `delivery-manifest.json` 记录 revision；ZIP 哈希不写入包内文件
（会自引用）。解压到新的空目录、确认无 `.env`/`.mcp.json` 后跑 `scripts/verify_delivery.py`。
不要拿未提交工作区代码冒充某个 revision。

**14. 密钥扫描只豁免“声明的摘要字段”**

`scripts/secret_scan.py` 只豁免示例 manifest 的 `sha256`、验证记录顶层的 `*_sha256`、图片/视频证据的
`sha256`/`asset_revision`/`video_revision` 和 32 位 `asset_id`；模型正文、配置键、未声明 hex 仍扫描。
不要把任意 64 位 hex 加进 allowlist 来让检查变绿；新增验证记录字段时先看 `metadata_hash_spans` 的测试。

**15. 测试必须隔离用户项目与外部服务**

用 `WQ_PROJECT_HOME` 指向临时目录；浏览器脚本会创建示例项目，必须对**隔离项目目录的临时服务**运行，
不要写进用户默认 `data/projects`。`check_ci.py` 会设置临时目录并禁止外部 `requests/httpx`。
前端 `questionSourceState.test.mjs` 用 fixture 替换除 `vue` 外的导入；组件新增导入时要在映射里补真实路径
（如 `utils/ragStage.js`），否则测试会报 “does not provide an export”。

**16. Python 版本口径以实际环境为准**

当前机器只有 Python 3.14.0；干净 CI venv 也是 3.14.0。`pyproject.toml` 声明 `>=3.11`，README/启动器不要再写
“当前锁定依赖使用 3.12 验证”。若在别的 Python 版本上运行，先实测并记录，不能只改版本文本。

**17. 接口不要暴露本机绝对路径**

RAG 状态已移除 `index_dir`；项目/示例/媒体接口只返回白名单元数据。新增接口时不要返回项目根、`.env` 路径、
调用栈、完整文件正文或隐藏真值。错误信息要可操作但不泄露路径/凭证。

**18. 示例资源读取必须有界，OSError 必须转 503**

`sample_catalog._read_bounded` 最多读 `limit+1` 字节；清单上限 2MB、CSV 上限 20MB，先用 `stat()` 检查实际大小，
再比较声明大小/SHA-256。不要用无界 `Path.read_bytes()` 读取清单或 CSV，也不要把 `OSError` 泄漏成 500。
校验和调用方解析必须使用同一份返回字节；新增资源类型时沿用这套边界。

## 与苗圃杯原项目的关系

基线代码来自 `C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\提交文件\`（**无 Git**，冻结的只读参照）。
其中 `提交文件/` 与 `1/提交文件/` 的 Python 源码完全相同，`提交文件/` 是唯一权威版本。

原仓库 `yunshuitianhu/water-quality-monitoring` **不要使用**：只有 5 次提交、首次即 `Initial commit` 提交全部功能，
正是赛道禁止的形态，且缺失最终版大量模块。本仓库首次提交已显式标注代码来源，**绝不补造提交历史**。

本机还有 `D:\aiRAG\`（时间戳 2026-06-18，晚于苗圃杯 06-03）：同一项目的后续开发版本，多出 `land_use.py`、
`precipitation.py`、真实 ESA 数据与名录，但同样存在本仓库已修复的全部缺陷。**尚未决定是否并入**——改动前先与用户确认。

## 文档索引

- `docs/交付说明_20261028.md` — 交付物/安装/验收/限制。
- `docs/交付核验修复记录_20261028.md` — R01—R03 根因、发布顺序、503 边界、打包门禁与残余风险。
- `docs/validation/delivery_r2_20261028.json` — 新包 revision/文件数/哈希、独立审计与无 Key 副本结果。
- `docs/任务队列.md` — Q/N 任务状态与日期记录。
- `docs/内置仿真示例与一键载入实施记录_20261027.md` — N01—N08 实施与证据。
- `docs/Windows安装与运行环境验证_20261028.md` — 干净 venv/启动器/带空格路径。
- `docs/AI交互与多模态接入实施记录_20261027.md` — 图片附件、只读工具、项目 schema 4。
- `docs/代码审计与修改方案.md`、`docs/代码审计-第二轮发现.md` — 已确认缺陷与修复进展。
- `data/samples/README.md` — 示例来源、字段、单位、缺测规则与演示限制。
- 独立分支 `research/hydro-wq-model`（刻意不并入 main）— HEC-RAS 实测笔记；碰 HEC-RAS 代码前先读。
  内容不在 main：`git fetch origin research/hydro-wq-model` 后
  `git show research/hydro-wq-model:research/hydro-wq-model/HEC-RAS实测笔记.md`。注意 `git ls-tree` 默认转义
  非 ASCII 路径，用 `-c core.quotepath=false` 再筛。
