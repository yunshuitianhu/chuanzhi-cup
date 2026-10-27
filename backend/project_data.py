"""只读业务数据目录与分页读取（Agent 工具的后端实现）。

- 统计在服务器计算；示例页不能冒充全量统计。
- 不读取磁盘路径、密钥、Git 或任意目录；只接受业务类别、筛选和分页。
- 跨项目读取使用既有保存快照，只读加载，不切换或覆盖当前项目。
"""
from __future__ import annotations

from dataclasses import asdict
import json
import re

import numpy as np
import pandas as pd

from water_quality_mcp.src.water_quality_mcp.analysis import quality_report
from water_quality_mcp.src.water_quality_mcp.analysis_config import AnalysisConfig
from water_quality_mcp.src.water_quality_mcp.data_quality import coordinate_mask
from water_quality_mcp.src.water_quality_mcp.evidence import data_identity
from .map_samples import finite
from .question_context import LABELS, _observed
from .report_evidence import _digest, record

MAX_LIMIT = 100
DEFAULT_LIMIT = 20


class DataError(ValueError):
    pass


class _View:
    """当前会话或已保存快照的只读视图；调用方只读取，不写回。"""

    def __init__(self, state, source):
        self.state = state
        self.source = source

    def __getattr__(self, name):
        return self.state.get(name)

    @property
    def analysis_config(self):
        cfg = AnalysisConfig()
        cfg.target_water_class = self.state.get('target_water_class', 'V')
        df = self.state.get('df')
        if df is not None and 'quality_control_config' in getattr(df, 'attrs', {}):
            from water_quality_mcp.src.water_quality_mcp.quality_control import QualityConfig
            cfg.quality_control = QualityConfig(**df.attrs['quality_control_config'])
        return cfg

    @property
    def data_identity(self):
        return data_identity(self.state.get('df'), self.state.get('data_filename', ''))


def _current_state(session):
    return {key: getattr(session, key, None) for key in (
        'df', 'video_frames', 'data_filename', 'video_filename', 'black_spots',
        'black_spots_json', 'cross_validation', 'comprehensive_summary',
        'national_station_result', 'investigations', 'tool_evidence',
        'messages', 'analysis_revision', 'results_revision', 'target_water_class',
        'media_assets', 'river_state')} | {'name': '当前会话项目'}


def _snapshot(session, project_id):
    if not isinstance(project_id, str) or not re.fullmatch(r'[0-9a-f]{32}', project_id):
        raise DataError('项目编号无效。')
    from .projects import repository
    try:
        info, snapshot = repository().read(project_id)
    except Exception as exc:
        raise DataError('无法读取该项目；可能不存在、已损坏或当前目录不可用。') from exc
    cache = getattr(session, '_project_snapshot_cache', None)
    if not isinstance(cache, dict):
        cache = {}
        session._project_snapshot_cache = cache
    key = (project_id, info.get('revision'))
    if key not in cache:
        state = dict(snapshot.state)
        state.update({'project_id': project_id, 'project_revision': info.get('revision'),
                      'results_revision': None, 'name': info.get('name') or '已保存项目'})
        cache[key] = state
        while len(cache) > 4:
            cache.pop(next(iter(cache)))
    return cache[key]


def view_for(session, project_id=None):
    if project_id is None or project_id == (session.project_id or None):
        state = _current_state(session)
        state['project_id'] = session.project_id or None
        state['project_revision'] = session.project_revision
        view = _View(state, {'kind': 'current_project', 'project_id': session.project_id or None,
                             'name': '当前会话项目' if not session.project_id else '当前项目'})
        return view
    state = _snapshot(session, project_id)
    view = _View(state, {'kind': 'saved_project', 'project_id': project_id,
                         'name': state.get('name') or '已保存项目'})
    return view


def _versions(view, session):
    df = view.df
    data_version = _digest({'df': df, 'filename': view.data_filename}) if df is not None else None
    return {'analysis_revision': view.analysis_revision,
            'results_revision': getattr(view, 'results_revision', None),
            'project_revision': getattr(view, 'project_revision', None),
            'data_version': data_version}


def _check_versions(view, args, session):
    expected = args.get('expected_analysis_revision')
    if expected is not None and expected != view.analysis_revision:
        raise DataError('分析数据版本已变化，请重新读取目录后再取数。')
    expected = args.get('expected_project_revision')
    if expected is not None and expected != getattr(view, 'project_revision', None):
        raise DataError('项目版本已变化，请重新读取目录后再取数。')
    expected = args.get('expected_results_revision')
    if expected is not None:
        if view.source['kind'] != 'current_project':
            raise DataError('已保存项目没有当前会话的结果版本，不能按结果版本读取。')
        if expected != session.results_revision:
            raise DataError('分析结果版本已变化，请重新读取目录后再取数。')


def _business_columns(df):
    return [str(column) for column in df.columns if not str(column).startswith('__')]


def catalog(session, args=None):
    args = args or {}
    view = view_for(session, args.get('project_id'))
    _check_versions(view, args, session)
    df = view.df
    video = view.video_frames or []
    black = view.black_spots
    out = {'status': 'ok', 'source': view.source, 'project': {
            'project_id': view.source.get('project_id'), 'name': view.source.get('name'),
            'revision': getattr(view, 'project_revision', None)},
        'versions': _versions(view, session),
        'data_identity': view.data_identity,
        'monitoring': {'present': bool(df is not None and len(df)),
                       'rows': int(len(df)) if df is not None else 0,
                       'columns': _business_columns(df) if df is not None else [],
                       'time_range': ([str(df['datetime'].min()), str(df['datetime'].max())]
                                      if df is not None and len(df) and 'datetime' in df else None),
                       'target_water_class': view.target_water_class},
        'video': {'present': bool(video), 'frames': len(video),
                  'filename': view.video_filename or ''},
        'results': {'has_black_spots': black is not None,
                    'black_spot_count': int(len(black)) if black is not None else 0,
                    'has_cross_validation': view.cross_validation is not None,
                    'has_comprehensive_summary': bool(view.comprehensive_summary),
                    'analysis_status': getattr(session, 'analysis_status', None) if view.source['kind'] == 'current_project' else None,
                    'analysis_done': getattr(session, 'analysis_done', None) if view.source['kind'] == 'current_project' else None},
        'river_model': _river_state(view),
        'investigations': {'items': len((view.investigations or {}).get('items', []))},
        'media_assets': [{'asset_id': meta.get('id'), 'sha256': meta.get('sha256'),
                          'width': meta.get('width'), 'height': meta.get('height'),
                          'bytes': meta.get('bytes'), 'source_name': meta.get('source_name', ''),
                          'created_at': meta.get('created_at', '')}
                         for meta in (view.media_assets or {}).values()],
        'available_categories': ['monitoring_records', 'quality_control', 'black_spots',
            'analysis_results', 'video_frames', 'river_model', 'investigations',
            'reports', 'external_evidence', 'project_info'],
        'notice': '目录只说明数据是否存在和版本；明细须用 read_project_data 读取，示例不能冒充全量。'}
    return out


def _array_stats(value):
    if not isinstance(value, np.ndarray) or not value.size:
        return None
    arr = np.asarray(value, dtype=float)
    finite_values = arr[np.isfinite(arr)]
    if not finite_values.size:
        return None
    return {'minimum': float(finite_values.min()), 'maximum': float(finite_values.max()),
            'mean': float(finite_values.mean())}


def _last_profile(result, field):
    array = getattr(result, field, None)
    chainage = getattr(result, 'chainage', None)
    times = getattr(result, 't', None)
    if (not isinstance(array, np.ndarray) or array.ndim != 2 or not array.size
            or not isinstance(chainage, np.ndarray) or not isinstance(times, np.ndarray)):
        return None
    row = array[-1]
    count = min(len(chainage), 20)
    indexes = np.linspace(0, len(chainage) - 1, count).round().astype(int)
    return {'time_h': finite(times[-1]) if len(times) else None,
            'chainage_m': [finite(chainage[index]) for index in indexes],
            'values': [finite(row[index]) for index in indexes]}


def _result_summary(result, fields):
    if result is None:
        return None
    times = getattr(result, 't', None)
    chainage = getattr(result, 'chainage', None)
    out = {'present': True,
           'time_h': [finite(times.min()), finite(times.max())] if isinstance(times, np.ndarray) and len(times) else None,
           'chainage_m': [finite(chainage.min()), finite(chainage.max())] if isinstance(chainage, np.ndarray) and len(chainage) else None,
           'params': getattr(result, 'params', {}) or {}}
    if fields == 'hydro':
        out['fields'] = {name: {'stats': _array_stats(getattr(result, name, None)),
                                'last_profile': _last_profile(result, name)}
                         for name in ('water_level', 'discharge', 'velocity', 'area')}
    else:
        out['fields'] = {name: {'stats': _array_stats(getattr(result, name, None)),
                                'last_profile': _last_profile(result, name)}
                         for name in ('cbod', 'ammonia', 'nitrate', 'dissolved_oxygen', 'phosphate')
                         if getattr(result, name, None) is not None}
    return out


def _river_state(view):
    model = view.river_state
    if model is None:
        return {'present': False, 'initialized': False, 'has_hydro': False, 'has_wq': False,
                'notice': '本次尚未初始化河道模型；未运行不代表求解失败。'}
    scenarios = getattr(model, 'scenarios', {}) or {}
    return {'present': True, 'initialized': bool(getattr(model, 'model_initialized', False)),
            'has_hydro': bool(getattr(model, 'has_hydro', lambda: False)()),
            'has_wq': bool(getattr(model, 'has_wq', lambda: False)()),
            'config': asdict(model.config) if hasattr(model, 'config') else None,
            'hydro_params': getattr(getattr(model, 'hydro_result', None), 'params', {}) or {},
            'wq_params': getattr(getattr(model, 'wq_result', None), 'params', {}) or {},
            'hydro_result': _result_summary(getattr(model, 'hydro_result', None), 'hydro'),
            'wq_result': _result_summary(getattr(model, 'wq_result', None), 'wq'),
            'scenarios': sorted(scenarios),
            'scenario_count': len(scenarios),
            'inverse_available': False,
            'notice': '只读取已有水位/流量/浓度结果摘要和末尾剖面，不触发重算；'
                      '当前应用未保存反演结果，正演设定源不是反演识别结果。'}


def _timestamp(value):
    from .question_context import _timestamp as parse
    from fastapi import HTTPException
    try:
        return parse(value)
    except HTTPException as exc:
        raise DataError(str(exc.detail)) from exc


def _scope_positions(view, args):
    """只按时间范围建立分母；指标有效性在统计阶段单独处理。"""
    df = view.df
    if df is None or df.empty:
        raise NoResult('当前项目没有监测数据。')
    if args.get('min_value') is not None or args.get('max_value') is not None:
        if not args.get('indicator'):
            raise DataError('按数值范围筛选时需要同时指定指标。')
    mask = np.ones(len(df), dtype=bool)
    if args.get('start') is not None or args.get('end') is not None:
        if 'datetime' not in df:
            raise DataError('监测数据缺少可用的采样时间。')
        stamps = pd.to_datetime(df['datetime'], errors='coerce')
        if not pd.api.types.is_datetime64_any_dtype(stamps.dtype):
            raise DataError('监测记录包含混合时区，须先规范时间再选择。')
        start = _timestamp(args['start']) if args.get('start') is not None else None
        end = _timestamp(args['end']) if args.get('end') is not None else None
        if start is not None and end is not None and end < start:
            raise DataError('结束时间不能早于开始时间。')
        timezone = stamps.dt.tz
        for stamp in (start, end):
            if stamp is None:
                continue
            if (timezone is not None) != bool(stamp.tzinfo):
                raise DataError('所选时间与监测记录的时区形式不一致，不能推测时区。')
        valid_time = stamps.notna().to_numpy()
        if start is not None:
            if timezone is not None:
                start = start.tz_convert(timezone)
            valid_time &= stamps.ge(start).to_numpy()
        if end is not None:
            if timezone is not None:
                end = end.tz_convert(timezone)
            valid_time &= stamps.le(end).to_numpy()
        mask &= valid_time
    return df, mask


class NoResult(Exception):
    pass


def _statistics(df, scope_mask, selected_mask, indicator, values):
    """统计口径：时间/位置范围是分母；有效原始观测才进入数值统计。

    显式 min/max 筛选时，selected 只保留有效且落在范围内的记录，并另外
    给出筛选前范围、有效候选和被数值筛选排除的数量，避免混用分母。
    """
    scope_count = int(scope_mask.sum())
    if not indicator:
        return {'records': int(selected_mask.sum()), 'scope_records': scope_count}
    values = pd.to_numeric(values, errors='coerce')
    observed = _observed(df, indicator)
    valid_scope = observed & np.isfinite(values.to_numpy(dtype=float))
    valid_candidates = int((scope_mask & valid_scope).sum())
    value_filter = selected_mask is not scope_mask
    if value_filter:
        selected_count = int(selected_mask.sum())
        stats = {'records': selected_count, 'scope_records': scope_count,
                 'valid_candidates': valid_candidates,
                 'valid_observations': selected_count,
                 'missing_or_invalid': 0,
                 'excluded_missing_or_invalid': scope_count - valid_candidates,
                 'excluded_by_value_filter': valid_candidates - selected_count}
        chosen = valid_scope & selected_mask
    else:
        stats = {'records': scope_count, 'scope_records': scope_count,
                 'valid_candidates': valid_candidates,
                 'valid_observations': valid_candidates,
                 'missing_or_invalid': scope_count - valid_candidates}
        chosen = valid_scope & scope_mask
    valid = values.iloc[np.flatnonzero(chosen)]
    label, unit = LABELS[indicator]
    stats.update({'indicator': label, 'unit': unit,
                  'minimum': finite(valid.min()) if len(valid) else None,
                  'maximum': finite(valid.max()) if len(valid) else None,
                  'mean': finite(valid.mean()) if len(valid) else None})
    return stats


def _compact_record(view, position, quality):
    full = record(view, position, quality)
    q = full.get('quality') or {}
    return {'position': full['position'], 'record_number': full['record_number'],
            'time': full['time'], 'values': full['values'], 'interpolated': full['interpolated'],
            'coord': full['coord'],
            'quality': {'flag': q.get('flag'), 'label': q.get('label'),
                        'reasons': [reason.get('test') for reason in q.get('reasons', [])
                                    if isinstance(reason, dict)]}}


def _monitoring(view, args, limit, offset):
    try:
        df, scope_mask = _scope_positions(view, args)
    except NoResult as exc:
        return {'status': 'no_result', 'message': str(exc)}
    indicator = args.get('indicator')
    values = None
    selected_mask = scope_mask
    if indicator:
        if indicator not in LABELS:
            raise DataError('指标不在可读取范围内。')
        values = pd.to_numeric(df.get(indicator, pd.Series(np.nan, index=df.index)), errors='coerce')
        if args.get('min_value') is not None or args.get('max_value') is not None:
            observed = _observed(df, indicator)
            in_range = observed & np.isfinite(values.to_numpy(dtype=float))
            if args.get('min_value') is not None:
                in_range &= values.ge(float(args['min_value'])).to_numpy()
            if args.get('max_value') is not None:
                in_range &= values.le(float(args['max_value'])).to_numpy()
            selected_mask = scope_mask & in_range
    stats = _statistics(df, scope_mask, selected_mask, indicator, values)
    positions = np.flatnonzero(selected_mask)
    quality = quality_report(df, view.analysis_config)
    page = positions[offset:offset + limit]
    rows = [_compact_record(view, int(position), quality) for position in page]
    return {'status': 'ok', 'category': 'monitoring_records', 'statistics': stats,
            'records': rows, 'returned_count': len(rows), 'total_count': int(len(positions)),
            'offset': offset, 'limit': limit, 'truncated': offset + len(rows) < len(positions),
            'notice': '时间/位置范围内全部记录计入 total_count；未指定指标时只返回记录，'
                      '指定指标时 valid_observations 只统计有效原始观测，缺测和插值仍计入总数。'
                      '显式 min/max 筛选时 total_count 为筛选后记录，另给 scope_records、'
                      'valid_candidates、excluded_missing_or_invalid 和 excluded_by_value_filter。'}


def _quality(view, args, limit, offset):
    df = view.df
    if df is None or df.empty:
        return {'status': 'no_result', 'message': '当前项目没有监测数据。'}
    quality = quality_report(df, view.analysis_config)
    flagged = [row for row in quality.rows if row.get('flag') in (3, 4, 9)]
    page = flagged[offset:offset + limit]
    return {'status': 'ok', 'category': 'quality_control', 'summary': quality.summary,
            'records': [{'position': row.get('id'), 'flag': row.get('flag'),
                         'label': row.get('label'), 'reasons': row.get('reasons')} for row in page],
            'returned_count': len(page), 'total_count': len(flagged),
            'offset': offset, 'limit': limit, 'truncated': offset + len(page) < len(flagged),
            'notice': '质控标记不撤销阈值预警，也不确认仪器故障；原始读数保留。'}


def _black(view, args, limit, offset):
    try:
        payload = json.loads(view.black_spots_json or '{}')
    except (TypeError, ValueError):
        payload = {}
    black = view.black_spots
    if black is None:
        return {'status': 'no_result', 'message': '当前项目还没有黑臭筛查结果；请先运行检测，不补造结果。'}
    rows = []
    for position in range(offset, min(len(black), offset + limit)):
        row = black.iloc[position]
        rows.append({key: finite(row.get(key)) if key in ('latitude', 'longitude') else row.get(key)
                     for key in ('cluster_id', 'latitude', 'longitude') if key in black})
    return {'status': 'ok', 'category': 'black_spots', 'summary': payload,
            'clusters': rows, 'returned_count': len(rows), 'total_count': int(len(black)),
            'offset': offset, 'limit': limit, 'truncated': offset + len(rows) < len(black),
            'notice': '热点数量不是污染源数量；仿真数据不能关联真实企业。'}


def _analysis_results(view, offset=0):
    cross = view.cross_validation
    comprehensive = view.comprehensive_summary
    if cross is None and not comprehensive:
        return {'status': 'no_result', 'message': '当前项目还没有可读取的分析结果；未运行不等于求解失败。'}
    text = comprehensive if isinstance(comprehensive, str) else ''
    start = max(0, int(offset))
    chunk = text[start:start + 4000]
    end = start + len(chunk)
    return {'status': 'ok', 'category': 'analysis_results',
            'cross_validation': cross,
            'comprehensive_summary': chunk,
            'comprehensive_offset': start,
            'comprehensive_total_chars': len(text),
            'comprehensive_next_offset': end if end < len(text) else None,
            'comprehensive_truncated': end < len(text),
            'notice': '这里读取的是已有结果，不会触发重算；offset 为综合摘要字符偏移，'
                      '返回 comprehensive_next_offset 时可继续读取。历史结果不能冒充当前数据。'}


def _video(view, args, limit, offset):
    from .routers.video import assessment
    frames = view.video_frames or []
    if not frames:
        return {'status': 'no_result', 'message': '当前项目没有视频帧。'}
    try:
        current = assessment(view)
    except Exception as exc:
        detail = getattr(exc, 'detail', None)
        raise DataError(str(detail or '无法核对视频帧版本。')) from exc
    rows = [{'frame_id': row.get('id'), 'frame_number': row.get('id') + 1,
             'time_sec': row.get('time_sec'), 'eligible': row.get('eligible'),
             'quality_status': row.get('quality_status'), 'quality_flags': row.get('quality_flags'),
             'roi_confirmed': row.get('roi_confirmed'), 'review': row.get('review')}
            for row in current['rows'][offset:offset + limit]]
    return {'status': 'ok', 'category': 'video_frames', 'video_revision': current['revision'],
            'settings': current['settings'], 'frames': rows,
            'returned_count': len(rows), 'total_count': len(current['rows']),
            'offset': offset, 'limit': limit, 'truncated': offset + len(rows) < len(current['rows']),
            'notice': '帧编号：frame_id为0基内部编号，frame_number为界面1基编号。'
                      'ROI/同步/质量未确认不阻止查看原图，但不会自动确认这些状态。'}


def _investigations(view, limit, offset):
    board = view.investigations or {'items': []}
    items = board.get('items', []) if isinstance(board, dict) else []
    page = items[offset:offset + limit]
    return {'status': 'ok', 'category': 'investigations', 'items': page,
            'returned_count': len(page), 'total_count': len(items),
            'offset': offset, 'limit': limit, 'truncated': offset + len(page) < len(items),
            'notice': '只读查看；不修改任务状态，也不把候选假设当成已确认结论。'}


def _reports(view, limit, offset):
    messages = view.messages or []
    reports = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            continue
        evidence = message.get('evidence')
        if isinstance(evidence, dict):
            content = message.get('content') if isinstance(message.get('content'), str) else ''
            facts = evidence.get('facts') if isinstance(evidence.get('facts'), list) else []
            references = evidence.get('references') if isinstance(evidence.get('references'), list) else []
            cards = evidence.get('analysis_cards') if isinstance(evidence.get('analysis_cards'), dict) else {}
            card_items = cards.get('cards') if isinstance(cards.get('cards'), list) else []
            reports.append({'message_index': index, 'run_id': message.get('run_id'),
                            'evidence_id': evidence.get('id'),
                            'incomplete': bool(message.get('incomplete')),
                            'created_at': evidence.get('created_at'),
                            'content_chars': len(content),
                            'excerpt': content[:4000],
                            'excerpt_truncated': len(content) > 4000,
                            'fact_count': len(facts),
                            'facts': [{'label': item.get('label'), 'value': item.get('value'),
                                       'unit': item.get('unit')} for item in facts[:10]
                                      if isinstance(item, dict)],
                            'reference_count': len(references),
                            'card_count': len(card_items)})
    page = reports[offset:offset + limit]
    return {'status': 'ok' if reports else 'no_result', 'category': 'reports',
            'reports': page, 'returned_count': len(page), 'total_count': len(reports),
            'offset': offset, 'limit': limit, 'truncated': offset + len(page) < len(reports),
            **({'message': '当前项目还没有带证据的回答。'} if not reports else {}),
            'notice': '只读查看历史回答摘要、统计事实计数和证据入口；不补造历史回答，也不改写已保存正文。'}


def _external(view, limit, offset):
    evidence = view.tool_evidence or {}
    items = []
    for name, value in evidence.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        items.append({'tool': name, 'chars': len(text), 'excerpt': text[:1000],
                      'truncated': len(text) > 1000})
    page = items[offset:offset + limit]
    return {'status': 'ok' if items else 'no_result', 'category': 'external_evidence',
            'items': page, 'returned_count': len(page), 'total_count': len(items),
            'offset': offset, 'limit': limit, 'truncated': offset + len(page) < len(items),
            **({'message': '当前项目还没有外部资料工具结果。'} if not items else {}),
            'notice': '外部资料仅作参考；工具原文没有可核验链接时如实标注，不把权重当概率。'}


def read(session, args):
    if not isinstance(args, dict):
        raise DataError('读取参数须为对象。')
    category = args.get('category')
    allowed = {'monitoring_records', 'quality_control', 'black_spots', 'analysis_results',
               'video_frames', 'river_model', 'investigations', 'reports', 'external_evidence', 'project_info'}
    if category not in allowed:
        raise DataError('数据类别无效。')
    offset = args.get('offset', 0)
    limit = args.get('limit', DEFAULT_LIMIT)
    if type(offset) is not int or offset < 0:
        raise DataError('offset 须为非负整数。')
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise DataError('limit 须为1—100的整数。')
    view = view_for(session, args.get('project_id'))
    _check_versions(view, args, session)
    base = {'source': view.source, 'versions': _versions(view, session), 'category': category,
            'offset': offset, 'limit': limit}
    if category == 'monitoring_records':
        result = _monitoring(view, args, limit, offset)
    elif category == 'quality_control':
        result = _quality(view, args, limit, offset)
    elif category == 'black_spots':
        result = _black(view, args, limit, offset)
    elif category == 'analysis_results':
        result = _analysis_results(view, offset)
    elif category == 'video_frames':
        result = _video(view, args, limit, offset)
    elif category == 'river_model':
        result = {'status': 'ok' if view.river_state is not None else 'no_result',
                  'category': 'river_model', 'model': _river_state(view),
                  **({'message': '当前项目尚未初始化河道模型。'} if view.river_state is None else {})}
    elif category == 'investigations':
        result = _investigations(view, limit, offset)
    elif category == 'reports':
        result = _reports(view, limit, offset)
    elif category == 'external_evidence':
        result = _external(view, limit, offset)
    else:
        result = {'status': 'ok', 'category': 'project_info',
                  'project': {'project_id': view.source.get('project_id'), 'name': view.source.get('name'),
                              'revision': getattr(view, 'project_revision', None)},
                  'data_identity': view.data_identity,
                  'monitoring': {'present': view.df is not None and len(view.df),
                                 'rows': int(len(view.df)) if view.df is not None else 0,
                                 'columns': _business_columns(view.df) if view.df is not None else [],
                                 'time_range': ([str(view.df['datetime'].min()), str(view.df['datetime'].max())]
                                                if view.df is not None and len(view.df) and 'datetime' in view.df else None)},
                  'video': {'present': bool(view.video_frames), 'frames': len(view.video_frames or [])},
                  'notice': '只读项目信息；不切换当前项目。'}
    return {**base, **result}


def inspect(session, args):
    """校验并返回图片引用；实际图像由 loop 在所有工具结果之后加载。"""
    if not isinstance(args, dict):
        raise DataError('图片读取参数须为对象。')
    from .vision import MAX_SEQUENCE_FRAMES, VisionError, asset_image, public_image, video_frame_image
    asset_id = args.get('asset_id')
    has_frames = any(args.get(name) is not None for name in ('frame_id', 'frame_number', 'frame_ids'))
    if asset_id and has_frames:
        raise DataError('一次只能读取视频帧或图片附件中的一种。')
    if asset_id:
        revision = args.get('asset_revision')
        try:
            image = asset_image(session, asset_id, revision)
        except VisionError as exc:
            raise DataError(str(exc)) from exc
        return {'status': 'ok', 'images': [public_image(image)],
                'vision': {'kind': 'image_asset', 'asset_id': image['asset_id'],
                           'asset_revision': image['asset_revision']},
                'notice': '图片将在工具结果之后发送给模型；图像不是浓度测量。'}
    if not has_frames:
        raise DataError('请提供视频帧编号或图片附件ID；用户只说“这张图”时应先请其选择。')
    frame_ids = args.get('frame_ids')
    if frame_ids is not None:
        if args.get('frame_id') is not None or args.get('frame_number') is not None:
            raise DataError('frame_ids 不能与单个帧编号同时提供。')
        if not isinstance(frame_ids, list) or not frame_ids:
            raise DataError('frame_ids 须为非空数组。')
        if len(frame_ids) > MAX_SEQUENCE_FRAMES:
            raise DataError('一次最多读取8帧，请缩小范围或分批查看。')
        ids = []
        for value in frame_ids:
            if type(value) is not int or value < 0:
                raise DataError('帧编号须为非负整数。')
            ids.append(value)
    else:
        if args.get('frame_id') is not None and args.get('frame_number') is not None:
            raise DataError('frame_id 与 frame_number 只能提供一个。')
        if args.get('frame_number') is not None:
            number = args['frame_number']
            if type(number) is not int or number < 1:
                raise DataError('frame_number 须为从1开始的整数。')
            ids = [number - 1]
        else:
            value = args.get('frame_id')
            if type(value) is not int or value < 0:
                raise DataError('frame_id 须为非负整数。')
            ids = [value]
    from .routers.video import assessment
    try:
        current = assessment(session)
    except Exception as exc:
        detail = getattr(exc, 'detail', None)
        raise DataError(str(detail or '当前无法读取视频帧。')) from exc
    revision = args.get('video_revision') or current['revision']
    if revision != current['revision']:
        raise DataError('视频或复核设置已变化，请重新选择当前帧。')
    if any(frame_id >= len(current['rows']) for frame_id in ids):
        raise DataError('当前视频没有该帧。')
    images = []
    try:
        for frame_id in ids:
            images.append(video_frame_image(session, frame_id, revision))
    except VisionError as exc:
        raise DataError(str(exc)) from exc
    return {'status': 'ok', 'images': [public_image(image) for image in images],
            'vision': {'kind': 'video_frames', 'frame_ids': ids, 'video_revision': revision},
            'notice': '图片将在工具结果之后发送给模型；帧编号为0基内部编号。'
                      'ROI/同步/质量未确认不影响查看原图，但不会自动确认这些状态。'}
