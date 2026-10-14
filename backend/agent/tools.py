"""Agent 工具定义与执行分发。

与 app.py 的关系
----------------
app.py 里有一份等价的工具清单与 execute_tool 分发（模块级 `tools` 列表 +
`execute_tool` 函数）。本模块是后端侧的对应实现，**改工具时两处都要改** ——
这是当前已知的重复，后续应把 app.py 迁到本模块上以消除。

与原版的三点差异
----------------
1. 执行对象是 backend.session.Session，而不是 st.session_state
2. 返回值统一为 (文本, 是否出错)，便于 SSE 层区分展示
3. 图表类工具不再生成 PNG —— 前端用 /api/charts/* 自行渲染
"""

from __future__ import annotations

import json
import logging

from water_quality_mcp.src.water_quality_mcp.analysis import (
    build_comprehensive_summary,
    cross_validate_video_with_monitoring,
    data_summary,
    find_black_spots,
)
from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
from water_quality_mcp.src.water_quality_mcp.geo_tools import (
    get_satellite_image,
    reverse_geocode,
)
from water_quality_mcp.src.water_quality_mcp.national_station import get_national_station_data
from water_quality_mcp.src.water_quality_mcp.rag_engine import search_treatment_cases
from water_quality_mcp.src.water_quality_mcp.report import generate_report
from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace

logger = logging.getLogger(__name__)


def _tool(name: str, desc: str, props: dict | None = None, required: list | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props or {},
                "required": required or [],
            },
        },
    }


TOOLS: list[dict] = [
    _tool("get_data_summary", "获取水质数据统计摘要"),
    _tool("find_black_spots",
          "基于《城市黑臭水体整治工作指南》(2015)和 GB3838-2002 发现水质黑点并做 "
          "DBSCAN 聚类，返回黑点总数、黑臭等级分布、聚类详情与 Top5 严重点位"),
    _tool("analyze_video", "分析已提取的视频帧（浑浊度、水色、风险等级）"),
    _tool("cross_validate_video",
          "将视频帧视觉特征（Laplacian 方差/HSV）与走航监测数据（浊度/氨氮/DO/COD）"
          "交叉印证，计算 Pearson 相关系数与黑臭判定一致率",
          {"time_offset_sec": {"type": "integer", "description": "视频第 0 秒相对首条记录的时间偏移（秒）"},
           "time_window_sec": {"type": "integer", "description": "匹配窗口宽度（秒，默认 30）"}}),
    _tool("get_comprehensive_summary",
          "获取综合分析摘要 JSON，聚合数据概览/黑臭检测/空间分析/交叉印证/河道模型"),
    _tool("search_treatment_cases",
          "从治理方案知识库检索相似污染治理案例，返回案例标题、治理方法与效果",
          {"query": {"type": "string", "description": "检索查询，如「氨氮超标 黑臭水体 城市河道治理」"},
           "top_k": {"type": "integer", "description": "返回案例数，默认 5"}},
          required=["query"]),
    _tool("generate_final_report", "生成符合 GB3838-2002 的溯源报告（自动注入 RAG 参考案例）",
          {"summary": {"type": "string"}}, required=["summary"]),
    _tool("reverse_geocode", "将经纬度转为地址与省份",
          {"lat": {"type": "number"}, "lon": {"type": "number"}}, required=["lat", "lon"]),
    _tool("search_nearby_pollution_sources",
          "多渠道污染溯源：整合高德 POI、排污许可证、国控站点，返回加权融合后的疑似污染源",
          {"lat": {"type": "number"}, "lon": {"type": "number"}}, required=["lat", "lon"]),
    _tool("get_national_station_data",
          "获取国控水质站点数据（实时或离线）。返回站名/pH/溶解氧/氨氮/总磷/总氮等",
          {"province": {"type": "string", "description": "省份，如「广东省」"},
           "keyword": {"type": "string", "description": "站名或流域关键词"},
           "live": {"type": "boolean", "description": "是否实时（默认 true）"}}),
    _tool("get_satellite_image", "获取卫星影像可用性（经纬度 → XYZ 瓦片）",
          {"lat": {"type": "number"}, "lon": {"type": "number"},
           "zoom": {"type": "integer", "description": "缩放级别，默认 16"}},
          required=["lat", "lon"]),
]

# 河道模型的六个工具由 river_model.tools 提供，签名与 MCP 版一致
RIVER_TOOLS = [
    "init_river_model", "run_hydrodynamic_simulation", "simulate_pollution_event",
    "get_concentration_profile", "compare_scenarios", "export_river_model_data",
]


def execute_tool(name: str, args: dict, session, api_key: str | None = None,
                 amap_key: str | None = None) -> tuple[str, bool]:
    """执行工具，返回 (结果文本, 是否出错)。

    所有异常在此收敛 —— 单个工具失败不应中断整个 Agent 回合，
    而是把错误文本作为工具结果回给模型，让它自行决定是否换一条路。
    """
    api_key = resolve_api_key("DEEPSEEK_API_KEY", api_key)
    amap_key = resolve_api_key("AMAP_KEY", amap_key)
    cfg = session.analysis_config

    try:
        if name == "get_data_summary":
            if session.df is None:
                return "数据未加载", True
            return data_summary(session.df), False

        if name == "find_black_spots":
            if session.df is None:
                return "数据未加载", True
            raw, black = find_black_spots(session.df, cfg)
            if black is not None:
                session.black_spots = black
                session.black_spots_json = raw
            return raw, ("error" in raw)

        if name == "cross_validate_video":
            if not session.video_frames:
                return json.dumps({"error": "视频帧未提取，请先上传视频"}, ensure_ascii=False), True
            ws = session.water_quality_state()
            raw = cross_validate_video_with_monitoring(
                ws, int(args.get("time_offset_sec", 0) or 0),
                int(args.get("time_window_sec", 30) or 30))
            result = json.loads(raw) if isinstance(raw, str) else raw
            if "error" not in result:
                session.cross_validation = result
            return raw, ("error" in result)

        if name == "get_comprehensive_summary":
            if session.df is None:
                return "数据未加载", True
            ws = session.water_quality_state()
            return build_comprehensive_summary(ws, session.get_river_state(), cfg), False

        if name == "search_treatment_cases":
            return search_treatment_cases(args.get("query", ""), int(args.get("top_k", 5) or 5)), False

        if name == "generate_final_report":
            if not api_key:
                return "未配置 DeepSeek API Key", True
            return generate_report(api_key, args.get("summary", "")), False

        if name == "reverse_geocode":
            return reverse_geocode(args.get("lat"), args.get("lon"), amap_key), False

        if name == "search_nearby_pollution_sources":
            lat, lon = args.get("lat"), args.get("lon")
            # 未给坐标时，用黑点最密集的聚类中心（与原 app.py 行为一致）
            if lat is None or lon is None:
                black = session.black_spots
                if black is not None and len(black) > 0:
                    cid = black["cluster_id"].value_counts().idxmax()
                    c = black[black["cluster_id"] == cid]
                    lat, lon = float(c["latitude"].mean()), float(c["longitude"].mean())
            if lat is None or lon is None:
                return json.dumps({"error": "未提供坐标且无黑点聚类可用于推断"},
                                  ensure_ascii=False), True
            return multi_source_trace(lat, lon, amap_key, cfg), False

        if name == "get_national_station_data":
            return get_national_station_data(
                province=args.get("province", ""), keyword=args.get("keyword", ""),
                live=bool(args.get("live", True))), False

        if name == "get_satellite_image":
            return get_satellite_image(args.get("lat"), args.get("lon"),
                                       int(args.get("zoom", 16) or 16),
                                       resolve_api_key("CLOUD_TOKEN")), False

        if name in RIVER_TOOLS:
            from river_model.tools import (
                tool_river_compare_scenarios, tool_river_concentration_profile,
                tool_river_export_data, tool_river_hydro_simulation,
                tool_river_init, tool_river_pollution_event,
            )
            st = session.get_river_state()
            fn = {
                "init_river_model": tool_river_init,
                "run_hydrodynamic_simulation": tool_river_hydro_simulation,
                "simulate_pollution_event": tool_river_pollution_event,
                "get_concentration_profile": tool_river_concentration_profile,
                "compare_scenarios": tool_river_compare_scenarios,
                "export_river_model_data": tool_river_export_data,
            }[name]
            return fn(st, **args), False

        return f"未知工具: {name}", True

    except Exception as e:                      # noqa: BLE001 —— 必须收敛, 不能中断回合
        logger.warning("工具 %s 执行失败: %s: %s", name, type(e).__name__, e)
        return f"工具执行出错（{type(e).__name__}）: {e}", True


# ---- 输出截断 ----------------------------------------------------------
# 与 app.py 保持一致：普通工具 2000 字符，综合摘要放宽到 4000。
# 注意 app.py 里有个已记录的缺陷：回合结束时 _trim_tool_messages 又统一
# 按 2000 砍一遍，使这里的放宽失效。新后端没有那个二次截断。
LIMIT_DEFAULT = 2000
LIMIT_SUMMARY = 4000


def truncate(name: str, text: str) -> str:
    limit = LIMIT_SUMMARY if name == "get_comprehensive_summary" else LIMIT_DEFAULT
    return text if len(text) <= limit else text[:limit] + "\n...(已截断)"


# ---- System Prompt ------------------------------------------------------
# 取自 app.py，保留原有的行为约束（禁止编造坐标、极简输出、禁止输出思考过程）
SYSTEM_PROMPT_AUTO = (
    "你是自动分析Agent。以下工具中有大量相互独立的调用，"
    "请在每一轮尽可能并行调用多个不依赖彼此结果的工具，以缩短总耗时。\n\n"
    "第一轮（同时调用，全部独立）：\n"
    "  1. get_data_summary\n"
    "  2. find_black_spots\n"
    "  4. get_national_station_data\n"
    "  5. analyze_video\n"
    "  7. get_satellite_image\n\n"
    "等第一轮全部返回后，第二轮（同时调用）：\n"
    "  3. reverse_geocode（用 find_black_spots 聚类中心坐标）\n"
    "  5.5. cross_validate_video\n"
    "  6. search_nearby_pollution_sources（用 reverse_geocode 结果也在此轮）\n\n"
    "注意：如果第二轮中 3 的结果对 6 是必须的，则把 6 放到第三轮单独调用。\n\n"
    "最后阶段（依次调用）：\n"
    "  10. get_comprehensive_summary\n"
    "  10.5. search_treatment_cases\n"
    "  11. generate_final_report\n\n"
    "输出要求：\n"
    "- 为图表撰写简要说明（时间序列图、溯源地图、交叉印证图），说明核心发现。\n"
    "- 只输出极简结论（不超过五句话）。\n"
    "- 严禁输出任何思考过程、自我检讨、道歉或工具调用日志。\n"
    "- 严禁输出 DSML 标签。"
)

SYSTEM_PROMPT_CHAT = (
    "你是水质监测与污染溯源专家。所有推断必须基于监测数据和 GB3838 标准。"
    "禁止编造坐标。"
)
