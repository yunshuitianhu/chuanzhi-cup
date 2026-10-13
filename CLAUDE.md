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

```bash
pip install -r requirements.txt
pip install -e water_quality_mcp/          # 可选；见下方"导入路径"一节

streamlit run app.py                        # 运行 Web 应用
python -m water_quality_mcp.server          # 以 MCP Server 方式运行（stdio）

python tests/river_model_验收算例.py         # 水动力求解器验收：9 通过 / 0 失败 / 1 已知缺陷
python tests/字段名一致性检查.py             # 界面读取的字段名 vs 后端实际产出的 JSON 键名
python test_smoke.py                        # 冒烟测试（当前 1 项失败，原因见下）

python scripts/build_rag_index.py           # 重建 RAG 索引（首次需下载 ~400MB 嵌入模型）
python scripts/build_rag_index.py --no-reset  # 增量更新
```

测试均为**脚本式**（非 pytest），无参数化/筛选机制——"跑单个测试"即单独运行对应脚本。两个验收脚本接受可选的项目根目录参数，默认取自身所在的上一级。

`test_smoke.py:45` 有一个**已知的路径错误**：它查找 `提交文件/苗圃杯/.../河道巡航走航样本数据.xlsx`，而样本文件实际在上一级。后果是第 3 项（黑臭检测）与第 5 项（图表生成）被 `if df is not None` **静默跳过**，连计数都不进——所以"4 总计"是虚的，核心算法链路目前零自动化覆盖。修复前不要把它当作可信的回归基线。

## 架构

```
app.py                    Streamlit UI + DeepSeek Function Calling Agent 编排
  │  （薄层：委托给下方两个包，不重复实现分析逻辑）
  ├─→ water_quality_mcp/  水质分析内核，25 个 MCP 工具
  └─→ river_model/        一维水动力-水质数值模型
        └─ knowledge_base/ --(build_rag_index)--> .rag_index/  ChromaDB 向量索引
```

**`water_quality_mcp`**（src 布局，`src/water_quality_mcp/`）
- `analysis.py` — 核心算法：双标准黑臭检测、内梅罗指数、时空增强评分、DBSCAN 聚类、视频-传感器交叉印证、综合摘要
- `loaders.py` — 数据加载清洗与视频抽帧
- `trace_engine.py` / `pollution_permit.py` / `national_station.py` / `geo_tools.py` — 多渠道溯源
- `rag_engine.py` / `knowledge_loader.py` — RAG 检索与索引构建
- `server.py` — MCP Server 入口（注意：注册的工具有 25 个，文档写 23）

**`river_model`**
- `pure_py_hydro.py` — Preissmann 四点隐式格式求解 Saint-Venant 方程
- `wq_engine.py` — ADR 对流-扩散-反应水质模型（算子分裂 + RK4）
- `hydro_engine.py` — 引擎调度（选择 HEC-RAS 或纯 Python）
- `parameter_estimator.py` — 6 层河道参数自发现管道
- `tools.py` — 7 个 MCP 工具入口

### 关键领域概念

- **双标准黑臭检测**：GB3838-2002 提供连续的内梅罗评分（用于排序/聚类），《城市黑臭水体整治工作指南》(2015) 提供"是/否黑臭"定性判定（用于筛选）。两者分工明确，不要混用。
- **时空增强评分**：`增强评分 = 内梅罗 × 严重度因子 × (1 + 1.5×时间因子) × (1 + 1.5×空间因子)`。孤立单点超标被抑制，持续成片超标被放大。输出同时含 `black_score`（增强）与 `nemerow_raw`（原始）。
- **引擎选择**：HEC-RAS COM 分支**仅支持恒定流**。非恒定流（潮汐）工况必须走纯 Python 求解器——`hydro_engine.is_nonconstant()` 负责判定，不要绕过它。
- **数据源权威性**：政府公示数据 > 商业地图数据。系统对每个外部数据源都做了独立降级（缺 Key / 网络失败 / API 改版都不应阻断整体流程）。

## 必须知道的坑

**动手前先读 `docs/` 下两份审计文档**，它们记录了已确认的缺陷、证据等级与修改方案，避免重复劳动或改错方向：

- `docs/代码审计与修改方案.md` — 第一轮，含修复进展表
- `docs/代码审计-第二轮发现.md` — 第二轮（全文通读后）

其中几条最容易踩的：

**1. 导入路径不一致 —— 改动任何 import 前先看清**

项目实际上**并未真正 pip 安装**，全部依赖 `sys.path` 操作，且存在两种互相冲突的风格：

- `from water_quality_mcp.src.water_quality_mcp.X import ...`（`app.py`、`report.py`、`knowledge_loader.py` 等）——要求**项目根目录**在 `sys.path` 上
- `from .analysis import ...`（包内相对导入）——要求 `water_quality_mcp/src` 在 `sys.path` 上

`state.py` 会在导入时把项目根目录注入 `sys.path`（为了让 `river_model` 可导入）。`.rag_index` 由 `rag_engine.py` 以 `../../../` 相对 `__file__` 解析到项目根目录。多处输出文件（`trace_map.html`、`_uploaded_data.xlsx`、`river_*.png`）是 **CWD 相对**路径——从别的目录启动会读写到不同位置。

**2. 字段名不匹配是系统性问题，不是孤例**

界面/调用层按英文 `snake_case` 键名编写，而后端包产出**中文键名**，`.get()` 取不到时静默返回默认值、不报错。已定位 8 处（详见第二轮审计文档 A 节）。新增前后端交互时**务必用 `tests/字段名一致性检查.py` 校验**，不要凭印象写键名。

**3. 已知未修复的缺陷，不要"顺手"改**

- `pure_py_hydro.py` 连续性方程遗漏水面宽 `B_mid`，非恒定工况质量不守恒（残差达蓄水率 103%）。补上 `B_mid` 的实验已回滚——残差不变且破坏 Newton 收敛。**需要重新推导 Preissmann 离散，非外科修复可解。**
- `analysis_config.py` 有两个独立单例（`get_config()` 管理的 `_DEFAULT_CONFIG` 与模块级 `DEFAULT_CONFIG`）。全库无人调用 `set_config`，属潜伏隐患。**改动零收益、风险非零，建议不动。**

**4. 不要在作品说明中美化能力**

已核实：文档宣称的"6 源溯源"实际只实现 4 源；ESA 土地利用、降雨模块、视频 5 维分析**代码中完全不存在**；河道模型的水动力部分**不可用于潮汐工况**。作品说明必须按代码实际能力撰写。

## 与苗圃杯原项目的关系

原项目在 `C:\Users\Cloud-Mist Sky Lake\Desktop\苗圃杯\提交文件\`（**无 Git**，是被冻结的只读参照）。其中 `提交文件/` 与 `1/提交文件/` 的 **Python 源码完全相同**，`提交文件/` 是唯一权威版本。

原仓库 `yunshuitianhu/water-quality-monitoring` **不要使用**：它只有 5 次提交、首次即 `Initial commit` 提交全部功能，正是赛道禁止的形态，且缺失最终版的大量模块。新仓库首次提交已显式标注了代码来源，**绝不补造提交历史**。
