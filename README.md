# 水质监测与污染溯源 AI Web 应用

> 备赛 **传智杯全国高校 AI Web 开发挑战赛**
> 主题方向：AI 数据分析与可视化 / RAG 知识问答系统

---

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
| Node.js | 18+（前端，规划中） |
| 网络 | 需可访问 `api.deepseek.com` |

## 安装

```bash
pip install -r requirements.txt
pip install -e water_quality_mcp/
```

## 运行验收测试

```bash
python tests/river_model_验收算例.py
```

## 配置 API Key

复制 `.env.example` 为 `.env` 并填入：

| 服务 | 用途 | 是否必需 |
|---|---|---|
| DeepSeek | LLM 分析与报告生成 | **必需** |
| 高德地图 | 逆地理编码、POI 搜索 | 推荐 |
| 星图地球 | 卫星影像底图 | 可选 |

---

## 数据格式

走航监测数据（CSV / Excel），必需列：

| 列名 | 说明 |
|---|---|
| 时间 | `YYYY-MM-DD HH:MM:SS` |
| 经度 / 纬度 | WGS-84 |
| 氨氮(mg/L) | NH₃-N |
| 溶解氧(mg/L) | DO |

可选列：化学需氧量、浑浊度、pH、水温、电导率、总有机碳。

---

## 判定标准

- **GB 3838-2002**《地表水环境质量标准》——单因子污染指数 + 内梅罗综合指数
- **《城市黑臭水体整治工作指南》(2015)**——黑臭等级定性判定

> 走航数据仅含氨氮与溶解氧两项黑臭关键指标，缺少透明度(SD)与氧化还原电位(ORP)，
> 系统在输出中明确标注该局限。
