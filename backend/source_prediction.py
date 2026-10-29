"""当前项目企业预测服务；不从历史回答或静态介绍中补造候选。"""
import json
from datetime import datetime, timezone

from water_quality_mcp.src.water_quality_mcp.source_prediction import predict_sources, prediction_text, wants_prediction


def attach_prediction(session, data, center=None):
    if center is None:
        value = data.get('搜索中心')
        if isinstance(value, str):
            try:
                center = [float(v) for v in value.split(',')]
            except ValueError:
                center = None
        elif isinstance(value, list):
            center = value
    pollutants = [name for column, name in (('ammonia_nitrogen', '氨氮'), ('cod', 'COD'))
                  if session.df is not None and column in session.df and session.df[column].notna().any()]
    prediction = predict_sources(data.get('综合污染源列表') or [], center=center,
        radius_m=data.get('搜索半径_m', 500), identity=session.data_identity, pollutants=pollutants)
    prediction['作用域'] = [session.project_id or '', session.analysis_revision, session.results_revision]
    prediction['生成时间'] = datetime.now(timezone.utc).isoformat()
    prediction['直接回答'] = prediction_text(prediction)
    return {**data, '污染源预测': prediction}


def current_prediction(session):
    try:
        data = json.loads(session.tool_evidence.get('search_nearby_pollution_sources', '{}'))
    except (TypeError, ValueError):
        return None
    prediction = data.get('污染源预测') if isinstance(data, dict) else None
    scope = [session.project_id or '', session.analysis_revision, session.results_revision]
    return prediction if isinstance(prediction, dict) and prediction.get('作用域') == scope else None


def remember_prediction(session, data, center=None):
    """新的企业预测使依赖旧线索的报告过期；历史正文保留。"""
    session.invalidate_report()
    session.results_revision += 1
    result = attach_prediction(session, data, center)
    session.tool_evidence['search_nearby_pollution_sources'] = json.dumps(result, ensure_ascii=False)
    return result


def query_prediction(session):
    """只读查询供侧边解释使用；主 Agent 通过原工具路径记录证据。"""
    from .routers.trace import _default_center, _track_center
    from water_quality_mcp.src.water_quality_mcp.trace_engine import multi_source_trace
    from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key
    stored = current_prediction(session)
    if stored is not None:
        return stored
    center = _default_center(session) or _track_center(session)
    if center is None or session.data_identity['kind'] == 'synthetic':
        return attach_prediction(session, {})['污染源预测']
    raw = multi_source_trace(*center, resolve_api_key('AMAP_KEY'), session.analysis_config)
    data = json.loads(raw) if isinstance(raw, str) else raw
    return attach_prediction(session, data, center)['污染源预测']
