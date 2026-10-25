"""报告的可重算事实与本轮工具快照。元数据随消息保存，不追认历史正文。"""
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from uuid import uuid4

import numpy as np
import pandas as pd

from water_quality_mcp.src.water_quality_mcp.analysis import data_summary, quality_report
from water_quality_mcp.src.water_quality_mcp.data_quality import coordinate_mask
from water_quality_mcp.src.water_quality_mcp.evidence import EVIDENCE_VERSION
from .map_samples import METRICS, finite

VERSION = 'report-evidence-v2'
TOOL_LIMIT = 30000
TOOLS = {'get_data_summary', 'find_black_spots', 'get_comprehensive_summary',
    'analyze_video', 'cross_validate_video', 'search_treatment_cases',
    'reverse_geocode', 'get_national_station_data', 'search_nearby_pollution_sources'}
LABELS = {'ammonia_nitrogen': ('氨氮', 'mg/L'), 'dissolved_oxygen': ('溶解氧', 'mg/L'),
    'cod': ('COD', 'mg/L'), 'turbidity': ('浊度', 'NTU'), 'ph': ('pH', '')}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _stable(value):
    """不含输出目录、运行任务ID或消息本身；项目恢复后同内容保持同指纹。"""
    if isinstance(value, pd.DataFrame):
        return {'columns': list(value.columns), 'attrs': _stable(value.attrs),
            'values': sha256(pd.util.hash_pandas_object(value, index=True).values.tobytes()).hexdigest()}
    if isinstance(value, np.ndarray):
        return {'shape': value.shape, 'dtype': str(value.dtype),
            'values': sha256(value.tobytes()).hexdigest()}
    if is_dataclass(value):
        return {f.name: _stable(getattr(value, f.name)) for f in fields(value)
            if f.metadata.get('persist', True)}
    if isinstance(value, dict):
        return {str(k): _stable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    if isinstance(value, np.generic):
        return _stable(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _digest(value):
    raw = json.dumps(_stable(value), ensure_ascii=False, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode('utf-8')
    return sha256(raw).hexdigest()


def fingerprints(session):
    from .project_codec import MODEL_FILES
    def model_ui(value):
        if isinstance(value, dict):
            return {k: ([str(path).replace('\\', '/').rsplit('/', 1)[-1]
                         if isinstance(path, str) and path.replace('\\', '/').rsplit('/', 1)[-1] in MODEL_FILES
                         else path for path in v] if k == '输出文件' and isinstance(v, list) else model_ui(v))
                    for k, v in value.items() if k != '文件生成说明'}
        if isinstance(value, list):
            return [model_ui(v) for v in value]
        return value
    data = _digest({'df': session.df, 'filename': session.data_filename})
    context = _digest({'data': data, 'rules': EVIDENCE_VERSION,
        'target': session.target_water_class, 'detection': session.black_spots_json,
        'black': session.black_spots, 'cross': session.cross_validation,
        'video': session.video_frames, 'video_filename': session.video_filename,
        'stations': session.national_station_result, 'tools': session.tool_evidence,
        'river': session.river_state, 'river_ui': model_ui(session.river_ui)})
    return {'data': data, 'context': context}


def capture_tool(name, args, raw, started_at):
    if name not in TOOLS:
        return None
    # 工具原文是数据；禁止前端执行HTML或从文本提取指令。隐藏实际配置密钥。
    from .project_codec import _scrubber
    scrub = _scrubber()
    cleaned = scrub(raw)
    parsed = json.loads(raw)
    status = 'failed' if not parsed.get('ok') else 'skipped' if parsed.get('skipped') else 'succeeded'
    return {'id': str(uuid4()), 'name': name, 'status': status,
        'started_at': started_at, 'finished_at': utc_now(),
        'arguments': scrub(json.dumps(args, ensure_ascii=False)),
        'output': cleaned[:TOOL_LIMIT], 'output_chars': len(cleaned),
        'truncated': len(cleaned) > TOOL_LIMIT}


def _data(trace):
    try:
        return json.loads(trace['output']).get('data') if not trace.get('truncated') else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _positions(df, key):
    if key == 'records' or key == 'time_range':
        return np.arange(len(df))
    if key == 'gps':
        return np.flatnonzero(coordinate_mask(df).to_numpy())
    column, operation = key.split('.')
    if column not in LABELS or operation not in ('mean', 'max', 'min', 'count'):
        raise ValueError('未知事实选择器')
    values = pd.to_numeric(df.get(column, pd.Series(np.nan, index=df.index)), errors='coerce')
    valid = np.isfinite(values).to_numpy(copy=True)
    if f'__observed_{column}' in df:
        valid &= df[f'__observed_{column}'].eq(True).to_numpy()
    if operation in ('max', 'min') and valid.any():
        # 精确原值的极值记录；不把四舍五入后相等的其它记录当作极值。
        extreme = values.iloc[np.flatnonzero(valid)].agg(operation)
        valid &= values.eq(extreme).to_numpy()
    return np.flatnonzero(valid)


def _black_positions(session, clustered=False):
    df, black = session.df, session.black_spots
    if df is None or black is None:
        return None
    if clustered:
        if 'cluster_id' not in black:
            return None
        black = black[black['cluster_id'].ne(-1)]
    if black.empty:
        return np.array([], dtype=int)
    for key in ('__input_record_id', None):
        original = df[key] if key and key in df else df.index if key is None else None
        selected = black[key] if key and key in black else black.index if key is None else None
        if original is not None and selected is not None and original.is_unique and selected.is_unique:
            index = pd.Index(original).get_indexer(selected)
            if (index >= 0).all():
                return index
    return None


def detection_facts(session):
    try:
        payload = json.loads(session.black_spots_json or '{}')
    except (TypeError, ValueError):
        return []
    if session.black_spots is None or payload.get('黑点总数') != len(session.black_spots):
        return []
    facts = []
    for key, label, field, unit in [('alerts', '黑臭阈值预警记录数', '黑点总数', '条'),
            ('clusters', '聚类热点数（非污染源数）', '聚类热点数', '簇')]:
        positions = _black_positions(session, key == 'clusters')
        facts.append({'id': key, 'label': label, 'value': payload.get(field), 'unit': unit,
            'record_count': len(positions) if positions is not None else None})
    return facts


def summary_facts(session):
    if session.df is None or session.df.empty:
        return []
    summary = json.loads(data_summary(session.df, session.data_filename, session.analysis_config))
    facts = []
    def add(key, label, value, unit=''):
        facts.append({'id': key, 'label': label, 'value': value, 'unit': unit,
            'record_count': len(_positions(session.df, key))})
    add('records', '记录总数', summary['记录数'], '条')
    add('time_range', '采样时间范围', summary['时间范围'])
    add('gps', '有效GPS记录数', summary['有效GPS记录数'], '条')
    for column, (label, unit) in LABELS.items():
        if label not in summary:
            continue
        metric = summary[label]
        add(column+'.count', label+'有效原始观测数', metric['有效观测数'], '条')
        if metric['有效观测数']:
            add(column+'.mean', label+'均值', metric['均值'], unit)
            op = 'min' if column == 'dissolved_oxygen' else 'max'
            add(column+'.'+op, label+('最低值' if op == 'min' else '最高值'), metric['极值'], unit)
    return facts


def _summary_matches(session, payload):
    if not isinstance(payload, dict) or session.df is None:
        return False
    current = json.loads(data_summary(session.df, session.data_filename, session.analysis_config))
    keys = ['记录数', '时间范围', '有效GPS记录数', '证据版本', '数据身份', *[v[0] for v in LABELS.values()]]
    return all(payload.get(k) == current.get(k) for k in keys)


def build_bundle(session, traces, run_id):
    traces = [t for t in traces if t is not None]
    if not traces:
        return None
    summary = next((t for t in reversed(traces) if t['name'] == 'get_data_summary'
        and t['status'] == 'succeeded'), None)
    facts = summary_facts(session) if summary and _summary_matches(session, _data(summary)) else []
    for fact in facts:
        fact['tool_id'] = summary['id']
    detection = next((t for t in reversed(traces) if t['name'] == 'find_black_spots'
        and t['status'] == 'succeeded'), None)
    if detection and _data(detection) == json.loads(session.black_spots_json or '{}'):
        for fact in detection_facts(session):
            fact['tool_id'] = detection['id']
            facts.append(fact)
    references = []
    for trace in traces:
        payload = _data(trace)
        if trace['name'] == 'search_treatment_cases' and trace['status'] == 'succeeded' and isinstance(payload, dict):
            cases = payload.get('检索结果')
            for case in cases[:10] if isinstance(cases, list) else []:
                if isinstance(case, dict):
                    snippets = case.get('证据片段')
                    snippets = snippets[:2] if isinstance(snippets, list) else []
                    references.append({'title': str(case.get('标题', '未提供标题'))[:300],
                        'location': str(case.get('地点', ''))[:300], 'tool_id': trace['id'],
                        'source_file': str(case.get('来源文件',''))[:500],
                        'evidence_ids': [s['证据ID'] for s in snippets if isinstance(s,dict)
                            and isinstance(s.get('证据ID'),str) and 1 <= len(s['证据ID']) <= 1000],
                        'notice': str(case.get('出处状态') or '本轮知识库返回；仅供治理参考。原工具未提供可核验原文链接。')[:500]})
    return {'id': str(uuid4()), 'version': VERSION, 'run_id': run_id, 'created_at': utc_now(),
        'fingerprints': fingerprints(session), 'data_identity': session.data_identity,
        'filename': session.data_filename, 'target_water_class': session.target_water_class,
        'rules': EVIDENCE_VERSION, 'facts': facts, 'tools': traces, 'references': references,
        'scope': '统计卡片可重算；工具原文是本轮返回快照，不代表正文每句话已核验。缺少本轮摘要时不补造统计关联。'}


def find_bundle(session, bundle_id):
    # 只从当前会话的已保存回答查找，不能凭任意ID访问其它会话。
    if not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', bundle_id):
        return None
    for message in reversed(session.messages):
        if not isinstance(message, dict):
            continue
        bundle = message.get('evidence')
        if message.get('role') == 'assistant' and not message.get('incomplete') and isinstance(bundle, dict) and bundle.get('id') == bundle_id:
            return bundle
    return None


def bundle_status(session, bundle):
    if not all(isinstance(bundle.get(key), list) and all(isinstance(item, dict) for item in bundle[key])
            for key in ('facts', 'tools', 'references')):
        return 'stale', '保存的证据结构不完整，请重新分析。'
    if any(not isinstance(f.get('id'), str) or not isinstance(f.get('tool_id'), str) for f in bundle['facts']) or any(
            not isinstance(t.get('id'), str) or not isinstance(t.get('output'), str) for t in bundle['tools']):
        return 'stale', '保存的证据结构不完整，请重新分析。'
    if bundle.get('version') != VERSION or bundle.get('rules') != EVIDENCE_VERSION:
        return 'stale', '证据格式或算法规则已更新，请重新分析。'
    if fingerprints(session) != bundle.get('fingerprints'):
        return 'stale', '数据、配置、检测、外部查询或模型已变化；仅保留历史快照，不能关联当前记录。'
    names = {f.get('id') for f in bundle.get('facts', []) if isinstance(f, dict)}
    expected = (summary_facts(session) if 'records' in names else []) + (detection_facts(session) if 'alerts' in names else [])
    actual = [{k: f.get(k) for k in ('id', 'label', 'value', 'unit', 'record_count')}
        for f in bundle.get('facts', []) if isinstance(f, dict)]
    if expected != actual:
        return 'stale', '保存的统计与当前重算结果不一致，请重新分析。'
    traces = {t.get('id'): t for t in bundle.get('tools', []) if isinstance(t, dict)}
    checked = set()
    for fact in bundle.get('facts', []):
        trace = traces.get(fact.get('tool_id'), {})
        detection = fact['id'] in ('alerts', 'clusters')
        if trace.get('name') != ('find_black_spots' if detection else 'get_data_summary') or trace.get('status') != 'succeeded':
            return 'stale', '统计缺少本轮成功工具关联，请重新分析。'
        if fact['tool_id'] in checked:
            continue
        if not (_data(trace) == json.loads(session.black_spots_json or '{}') if detection else _summary_matches(session, _data(trace))):
            return 'stale', '工具快照与统计口径不一致，请重新分析。'
        checked.add(fact['tool_id'])
    return 'current', '当前数据与分析条件匹配；统计重算一致。正文推断仍需复核。'


def public_bundle(session, bundle):
    status, notice = bundle_status(session, bundle)
    public = {k: v for k, v in bundle.items() if k not in ('fingerprints', 'tools', 'facts', 'references')}
    fingerprint = bundle.get('fingerprints')
    data_version = fingerprint.get('data') if isinstance(fingerprint, dict) else None
    return {**public, 'facts': [f for f in bundle.get('facts', []) if isinstance(f, dict)] if isinstance(bundle.get('facts'), list) else [],
        'references': [r for r in bundle.get('references', []) if isinstance(r, dict)] if isinstance(bundle.get('references'), list) else [],
        'data_version': data_version[:12] if isinstance(data_version, str) else '未知',
        'status': status, 'notice': notice,
        'tools': [{k: v for k, v in t.items() if k != 'output'} for t in bundle.get('tools', []) if isinstance(t, dict)]
            if isinstance(bundle.get('tools'), list) else []}


def record(session, position, quality=None):
    df = session.df
    if df is None or not 0 <= position < len(df):
        raise ValueError('采样记录不存在')
    row = df.iloc[position]
    values, interpolated = {}, {}
    for column in METRICS:
        value = finite(row.get(column))
        observed = row.get(f'__observed_{column}', True) == True  # noqa: E712
        values[column] = value if observed else None
        if not observed and value is not None:
            interpolated[column] = value
    valid_coord = bool(coordinate_mask(df).iloc[position])
    stamp = row.get('datetime')
    quality = quality or quality_report(df, session.analysis_config)
    return {'position': position, 'record_number': position+1,
        'input_record_id': finite(row.get('__input_record_id')),
        'time': str(stamp) if stamp is not None and not pd.isna(stamp) else None,
        'values': values, 'interpolated': interpolated, 'quality': quality.rows[position],
        'coord': [finite(row.get('latitude')), finite(row.get('longitude'))] if valid_coord else None,
        'map_sample_id': position if valid_coord else None}


def fact_records(session, bundle, fact_id, offset, limit):
    fact = next((f for f in bundle.get('facts', []) if f.get('id') == fact_id), None)
    if fact is None:
        raise KeyError('本轮没有该统计事实')
    positions = _black_positions(session, fact_id == 'clusters') if fact_id in ('alerts', 'clusters') else _positions(session.df, fact_id)
    if positions is None:
        raise ValueError('历史检测未保留唯一记录关联，不能猜测对应原始行；请重新检测分析。')
    quality = quality_report(session.df, session.analysis_config)
    return {'fact': fact, 'total': len(positions), 'offset': offset, 'limit': limit,
        'rows': [record(session, int(p), quality) for p in positions[offset:offset+limit]]}


def covered_record(session, bundle, position):
    for fact in bundle['facts']:
        key = fact['id']
        positions = _black_positions(session, key == 'clusters') if key in ('alerts', 'clusters') else _positions(session.df, key)
        if positions is not None and position in positions:
            return True
    return False
