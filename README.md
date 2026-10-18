# 水质监测与污染溯源 AI Web 应用

> 备赛 **传智杯全国高校 AI Web 开发挑战赛**
> 主题方向：AI 数据分析与可视化 / RAG 知识问答系统

---

## 项目介绍

面向河道走航监测，将上传的 CSV/Excel 数据转为水质趋势、异常聚类和地图线索，
由 Agent 调用分析工具、检索治理案例，辅助形成现场排查与复测建议。
候选企业和地图 POI 仅作为排查线索，来源权重不代表污染责任概率。
视频目前只提供清晰度和颜色等视觉线索；潮汐模型尚未通过质量守恒验收，
其数值已从综合摘要中排除，不能用于正式分析结论。

## 技术架构

```text
Vue 3 / Pinia / ECharts / Leaflet
           │ REST + SSE（X-Session-Id 会话）
           ▼
FastAPI：上传、分析、Agent、地图数据、河道模型接口
           │
           ├─ 水质分析内核：规则判定、DBSCAN、外部线索查询
           ├─ DeepSeek Function Calling + 治理案例 RAG
           └─ 可配置河段数值模型（内置恒定流引擎；合成单源反演验收）
```

临时会话在单个后端进程的内存中；保存为项目后，监测表、配置、分析、对话和模型结果可在重启后恢复。项目目录由SQLite管理，无需安装数据库服务。运行中任务仍只支持单worker。
本轮改动和验证范围见 [首轮改进记录](docs/首轮改进记录.md)。
第二轮正确性修复、47 项回归和数值质量核算见 [第二轮修复记录](docs/第二轮修复记录.md)。
真实样例发现的 3 项结果缺陷已修复，56 项回归和 27 项真实样例检查通过，范围与剩余局限见 [真实样例修复记录](docs/真实样例修复记录.md)。
正演模型包含氧限制硝化、氮氧核算及走航时空匹配。2026-10-18 起停止采用 HEC-RAS；旧代码移至 `research/legacy_hecras/`，不参与运行或主测试。当前运行路径无需商业软件或 Windows COM；此前说明保留为历史。
污染情景目前支持氨氮和 CBOD；COD 没有独立实现，不能用 CBOD 结果冒充。

## 评审复现与适用边界

安装依赖后，在项目根目录运行：

```bash
python -m unittest discover -s tests -p "test_*regressions.py" -q
python scripts/validate_steady_inverse.py
python scripts/validate_inverse_robustness.py
```

合成验收输出 `work/steady-inverse-validation/validation.json`，任一预设条件失败时返回非零退出码。三个不同尺度理想河段使用 81 点网格、15 秒步长生成含噪观测，以 41 点网格、45 秒步长拟合，并保留观测独立检验。无需密钥、网络、国控接口或真实源标签。

压力检查输出 `work/inverse-robustness/robustness.json`：三个噪声水平各8个固定种子，另测流速/背景失配、候选遗漏、负荷上界不足、排放前观测及仪器增益混淆，共30例。残差过大、多个近优源、触及边界或负荷分辨不足时，`estimate` 为 `null`，保留候选拟合供诊断。工程门槛不是统计置信度。增益混淆案例仍可出现小残差与错误负荷，实验明确记录这一未解决盲点。

河道页支持手工填写名称、长度、参考宽深、坡降、糙率、局部河底高程、下游水位、流量及初始水质。缺少 GPS 时不编造位置，不读取固定样本。`RiverConfig` 默认是理想模板；`YayaoConfig` 仅为旧导入兼容名。自动估计不再默认套用已知河流的物理参数。

当前仅覆盖一维恒定流、理想梯形河段情景。合成反演固定排放时段、水动力与反应参数，估计离散源位置和有界负荷；不能把合成精度当作真实河流精度，尚未实测所有操作系统。潮汐、支汊、二维回流和真实污染责任认定不属于已验收能力。

地图颜色按综合污染指数固定色标绘制，以 ≥8 为显示上限后做邻域平滑，不是采样密度或单点最大值；Top5 详情保留原始指数。国控查询按本次数据来源和监测时间报告；实时失败明确回退，当前站点数据不能冒充历史走航同日实测。手工模型和合成验证可以离线运行；外部查询、Agent、RAG 需相应数据或配置。

前端测试（`frontend` 目录）：`node --test tests/severityHeat.test.mjs`。详情见 [可移植与泛化改进记录](docs/可移植与泛化改进记录.md)。

后续热力图平滑、反演拒绝条件及压力实验的结果与局限见 [热力图平滑与反演稳健性记录](docs/热力图平滑与反演稳健性记录.md)。

## 关于本仓库的代码来源（重要声明）

本仓库的 **Python 分析内核**（`water_quality_mcp/`、`river_model/`、`knowledge_base/`、
`scripts/`、`app.py`、`build_animation.py`）**沿用自作者此前参加苗圃杯的作品**
《水质监测溯源助手》，由作者本人独立开发。

本次参赛的新增工作为：

- **Vue 3 前端**（目录 `frontend/`，从零重写，替代原 Streamlit 界面）
- **FastAPI 后端层**（从零编写）
- **对既有 Python 内核的缺陷修复与重构**（详见 `docs/代码审计与修改方案.md`）
- 全部修复与新增功能均以独立、细粒度的提交记录在本仓库中

原作品与本次作品的关系、以及本次修改的完整清单，见：

- `docs/代码审计与修改方案.md` — 逐条列出发现的缺陷、证据等级与修改方案

> 本仓库不隐藏代码来源。首次导入的既有代码以单独一次提交标注，
> 其后的每一项改动都是真实、可追溯的开发过程。

---

## 目录结构

```
.
├── app.py                      # 原 Streamlit 界面（保留用于 MCP 工具调用与对照）
├── water_quality_mcp/          # 水质分析核心包（25 个 MCP 工具）
│   └── src/water_quality_mcp/
│       ├── analysis.py         #   黑臭检测、内梅罗指数、时空增强评分、交叉印证
│       ├── loaders.py          #   数据加载与清洗、视频抽帧
│       ├── charts.py           #   图表生成
│       ├── geo_tools.py        #   WGS-84/GCJ-02 转换、POI、逆地理
│       ├── trace_engine.py     #   多渠道污染溯源融合
│       ├── rag_engine.py       #   RAG 治理案例检索
│       ├── national_station.py #   国控站点数据
│       ├── pollution_permit.py #   排污许可查询
│       ├── report.py           #   溯源报告生成
│       └── server.py           #   MCP Server 入口
├── river_model/                # 一维水动力-水质数值模型
├── knowledge_base/             # 29 个治理案例（RAG 语料）
├── tests/
│   └── river_model_验收算例.py  # 水动力求解器验收标准
├── docs/
│   └── 代码审计与修改方案.md
└── .rag_index/                 # 已构建的向量索引（供 RAG 直接使用）
```

---

## 环境要求

| 项 | 版本 |
|---|---|
| Python | 3.10+（本项目在 3.14 上验证通过） |
| Node.js | 18+（前端已实现，本轮使用 Node.js 24 验证） |
| 网络 | 需可访问 `api.deepseek.com` |

## 安装

```bash
pip install -r requirements.txt
pip install -e water_quality_mcp/
```

## 启动 Web 应用

在项目根目录启动后端：

```powershell
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8002
```

在另一个终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

浏览器打开终端显示的本地地址，默认是 `http://localhost:5173/`。
前端代理默认连接后端 8002 端口；如果后端改用其他端口，先执行
`$env:BACKEND_PORT='实际端口'` 再启动前端。

只做数据检测与图表查看无需 LLM 密钥；Agent 和报告生成需要配置 DeepSeek。

## 保存与迁移河流项目

侧边栏“河流项目”支持当前内容另存为、新建空项目、保存、切换及导入导出。修改后请点“保存项目”；切换前自动保存，未命名临时数据会先保存为独立项目。导入创建新ID，保留现有项目。保存版本冲突会拒绝覆盖，可用“当前内容另存为”保留修改。

默认使用`data/projects/projects.sqlite3`索引和同目录下的版本包、运行附件。Python自带SQLite，首次使用自动建库。可在启动后端前设置`WQ_PROJECT_HOME`为有写权限的数据目录；无需MySQL、PostgreSQL或Redis。迁移请使用`.wqproj`导出包，不要在运行时只复制SQLite文件。

导出包含处理后的监测表及观测标记、参数来源、分析和对话、已提取视频帧、模型数值结果与模型附件；不包含原始Excel工作簿或原始整段视频，也不包含`.env`与密钥配置。接收电脑需安装项目依赖；基础分析和手工模型无需LLM密钥，外部服务另行配置。包格式可校验、版本化，禁止pickle反序列化和任意路径解压。单包最大256MB、展开最大512MB。

浏览器保留原会话ID时可自动恢复最后保存的项目；换浏览器或清空本地存储后，可从项目列表打开。尚未保存的临时会话不保证重启恢复。数值与附件可迁移，不代表真实河流模型已经率定。

本机开发可在`frontend/.env.development.local`设置`WQ_BACKEND_PORT`，优先于启动环境的`BACKEND_PORT`；两者未设置时仍连接8002。该本机文件不入Git。项目使用、验证结果与当前限制见[项目保存与迁移说明](docs/项目保存与迁移说明.md)。

## 运行验收测试

```bash
python tests/river_model_验收算例.py
python -m unittest discover -s tests -p "test_*regressions.py" -v
python test_smoke.py "你的监测样本.xlsx"
```

浏览器回归额外需要 Playwright 及 Chromium：

```powershell
python -m pip install playwright
python -m playwright install chromium
python tests/browser_session_regression.py
```

浏览器回归使用独立端口、合成监测数据和模拟 SSE，不请求真实 LLM。

## 配置 API Key

复制 `.env.example` 为 `.env` 并填入：

| 服务 | 用途 | 是否必需 |
|---|---|---|
| DeepSeek | LLM 分析与报告生成 | **必需** |
| 高德地图 | 逆地理编码、POI 搜索 | 推荐 |
| 星图地球 | 卫星影像底图 | 可选 |

---

## 数据格式

走航监测数据（CSV / Excel）。时间、氨氮、溶解氧为必需列，必须存在有效实测值。
缺测点不视作达标；插值仅用于展示，不作为实测判定证据。经纬度用于地图与定位：

| 列名 | 说明 |
|---|---|
| 时间 | `YYYY-MM-DD HH:MM:SS` |
| 经度 / 纬度 | WGS-84；缺少时不生成地图和点位结论 |
| 氨氮(mg/L) | NH₃-N |
| 溶解氧(mg/L) | DO |

可选列：化学需氧量、浑浊度、pH、水温、电导率、总有机碳。

---

## 判定标准

- **GB 3838-2002**《地表水环境质量标准》——单因子污染指数 + 内梅罗综合指数
- **《城市黑臭水体整治工作指南》(2015)**——黑臭等级定性判定

> 走航数据仅含氨氮与溶解氧两项黑臭关键指标，缺少透明度(SD)与氧化还原电位(ORP)，
> 系统在输出中明确标注该局限。
