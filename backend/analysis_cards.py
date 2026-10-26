'''研判卡片核心模块。

本模块只处理候选卡片目录、提交校验、最终证据挂载和公开读取。
不写排查板，不调用模型，不解析 Markdown。
'''
from __future__ import annotations

from typing import Literal
from uuid import UUID, uuid4
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .investigations import Draft
from .report_evidence import (_black_positions, _data, _digest, _positions, build_bundle,
    bundle_status, covered_record, fingerprints, record, utc_now)

CARD_VERSION = 'analysis-cards-v1'
CATALOG_VERSION = 'analysis-cards-catalog-v1'
PROPOSAL_VERSION = 'analysis-cards-v1-proposal'
PROPOSAL_ATTR = '_analysis_card_proposal'
MAX_CARDS = 5
MAX_REFS = 8
MAX_GAPS = 8
MAX_GAP_CHARS = 500
POSITION_LIMIT = 200
RAG_LIMIT = 100
FRAME_LIMIT = 80
VIDEO_TOOLS = {'analyze_video', 'cross_validate_video'}
Indicator = Literal['ammonia', 'do', 'cod', 'ph', 'turbidity', 'sd', 'orp', 'flow', 'connection']


class _Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Ref(_Model):
    kind: Literal['fact', 'record', 'rag', 'tool', 'video_frame']
    fact_id: str | None = Field(None, max_length=100)
    position: int | None = Field(None, ge=0)
    evidence_id: str | None = Field(None, max_length=1000)
    name: str | None = Field(None, max_length=100)
    frame_id: int | None = Field(None, ge=0)

    @model_validator(mode='after')
    def check_fields(self):
        allowed = {'fact': {'fact_id'}, 'record': {'position'}, 'rag': {'evidence_id'},
            'tool': {'name'}, 'video_frame': {'frame_id'}}
        values = {'fact_id': self.fact_id, 'position': self.position,
            'evidence_id': self.evidence_id, 'name': self.name, 'frame_id': self.frame_id}
        used = {key for key, value in values.items() if value is not None}
        if used != allowed[self.kind]:
            raise ValueError('引用字段与引用类型不匹配')
        for key in used:
            value = values[key]
            if isinstance(value, str) and not value.strip():
                raise ValueError('引用标识不能为空白')
        return self


class SuggestedRetest(_Model):
    location: str = Field('', max_length=300)
    indicators: list[Indicator] = Field(default_factory=list, max_length=9)
    plan: str = Field('', max_length=3000)

    @model_validator(mode='after')
    def check_indicators(self):
        if len(set(self.indicators)) != len(self.indicators):
            raise ValueError('复测指标不可重复')
        return self


class Suggestion(_Model):
    title: str = Field('', max_length=120)
    hypothesis: str = Field('', max_length=2000)
    supporting_notes: str = Field('', max_length=4000)
    opposing_notes: str = Field('', max_length=4000)
    retest: SuggestedRetest = Field(default_factory=SuggestedRetest)


class Card(_Model):
    title: str = Field(min_length=1, max_length=120)
    finding: str = Field(min_length=1, max_length=2000)
    support: list[Ref] = Field(min_length=1, max_length=MAX_REFS)
    oppose: list[Ref] = Field(default_factory=list, max_length=MAX_REFS)
    gaps: list[str] = Field(default_factory=list, max_length=MAX_GAPS)
    suggestion: Suggestion = Field(default_factory=Suggestion)

    @model_validator(mode='after')
    def check_text(self):
        if not self.title.strip() or not self.finding.strip():
            raise ValueError('卡片标题和发现不能为空白')
        if any(not gap.strip() or len(gap) > MAX_GAP_CHARS for gap in self.gaps):
            raise ValueError('证据缺口必须为500字以内的非空文本')
        return self


class Proposal(_Model):
    catalog_token: str = Field(min_length=1, max_length=200)
    cards: list[Card] = Field(default_factory=list, max_length=MAX_CARDS)


class _StoredCard(_Model):
    id: str = Field(min_length=36, max_length=36)
    title: str = Field(min_length=1, max_length=120)
    finding: str = Field(min_length=1, max_length=2000)
    support: list[Ref] = Field(min_length=1, max_length=MAX_REFS)
    oppose: list[Ref] = Field(default_factory=list, max_length=MAX_REFS)
    gaps: list[str] = Field(default_factory=list, max_length=MAX_GAPS)
    suggestion: Draft

    @model_validator(mode='after')
    def check_card(self):
        try:
            UUID(self.id)
        except (ValueError, TypeError) as exc:
            raise ValueError('卡片标识无效') from exc
        if not self.title.strip() or not self.finding.strip():
            raise ValueError('卡片标题和发现不能为空白')
        if any(not gap.strip() or len(gap) > MAX_GAP_CHARS for gap in self.gaps):
            raise ValueError('证据缺口必须为500字以内的非空文本')
        draft = self.suggestion
        if (draft.status != 'open' or draft.review.outcome or draft.review.reviewer
                or draft.review.notes or draft.retest.observed_at or draft.retest.result
                or draft.retest.latitude is not None or draft.retest.longitude is not None):
            raise ValueError('AI 建议只能包含待确认草稿，不能携带实测、复核或虚构坐标')
        return self


class _StoredPacket(_Model):
    version: Literal['analysis-cards-v1']
    created_at: str = Field(min_length=1, max_length=50)
    catalog_token: str = Field(min_length=1, max_length=200)
    video_revision: str | None = Field(None, min_length=64, max_length=64)
    cards: list[_StoredCard] = Field(default_factory=list, max_length=MAX_CARDS)


def _trace_list(traces):
    return [trace for trace in traces if isinstance(trace, dict)] if isinstance(traces, (list, tuple)) else []


def _items(value):
    return value if isinstance(value, list) else []


def _ok_trace(trace):
    if not (isinstance(trace, dict) and trace.get('status') == 'succeeded'
            and trace.get('truncated') is not True and isinstance(trace.get('output'), str)):
        return False
    try:
        envelope = json.loads(trace['output'])
    except (ValueError, TypeError):
        return False
    return (isinstance(envelope, dict) and envelope.get('ok') is True
        and not envelope.get('skipped') and not envelope.get('error'))


def _catalog_token(session, traces):
    traces = _trace_list(traces)
    payload = {'fingerprints': fingerprints(session), 'traces': [
        {key: trace.get(key) for key in ('id', 'name', 'status', 'started_at', 'finished_at',
            'arguments', 'output', 'output_chars', 'truncated')}
        for trace in traces]}
    return 'ac1_' + _digest(payload)[:48]


def _success_names(traces):
    names = set()
    for trace in traces:
        if _ok_trace(trace) and isinstance(trace.get('name'), str):
            names.add(trace['name'])
    return sorted(names)


def _text(value, limit=500):
    if value is None:
        return ''
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        raw = str(value)
    raw = ' '.join(raw.split())
    return raw[:limit]


def _bound(value, limit):
    if value is None:
        return ''
    return str(value)[:limit]


def _rag_entries(traces):
    found = {}
    for trace in traces:
        if not _ok_trace(trace) or trace.get('name') != 'search_treatment_cases':
            continue
        payload = _data(trace)
        cases = payload.get('检索结果') if isinstance(payload, dict) else None
        if not isinstance(cases, list):
            continue
        for case in cases[:10]:
            if not isinstance(case, dict):
                continue
            snippets = case.get('证据片段')
            if not isinstance(snippets, list):
                continue
            for snippet in snippets[:2]:
                if not isinstance(snippet, dict):
                    continue
                evidence_id = snippet.get('证据ID')
                if not isinstance(evidence_id, str) or not evidence_id or len(evidence_id) > 1000:
                    continue
                excerpt = ''
                for key in ('原文', '片段', '内容', '文本', '摘要', 'excerpt', 'text'):
                    if snippet.get(key) not in (None, ''):
                        excerpt = _text(snippet.get(key), 500)
                        break
                if not excerpt:
                    excerpt = _text(case.get('标题', ''), 500)
                found[evidence_id] = {'evidence_id': evidence_id,
                    'title': _text(case.get('标题', '未提供标题'), 300),
                    'location': _text(case.get('地点', ''), 300),
                    'tool_id': trace.get('id') if isinstance(trace.get('id'), str) else '',
                    'excerpt': excerpt or '本轮知识库片段'}
                if len(found) >= RAG_LIMIT:
                    return found
    return found


def _tool_trace(bundle, name):
    for trace in reversed(_items(bundle.get('tools'))):
        if _ok_trace(trace) and trace.get('name') == name:
            return trace
    return None


def _has_video_trace(traces):
    return any(_ok_trace(trace) and trace.get('name') in VIDEO_TOOLS for trace in traces)


def _video_state(session):
    # assessment 的 revision 是包含项目/分析版本的 SHA256，frame_id 是行位置。
    from fastapi import HTTPException
    from backend.routers.video import assessment
    try:
        result = assessment(session)
    except (HTTPException, ValueError, TypeError):
        return None
    revision, rows = result.get('revision'), result.get('rows')
    if (not isinstance(revision, str) or len(revision) != 64
            or not isinstance(rows, list)):
        return None
    return revision, rows


def _row_frame_id(row):
    value = row.get('id') if isinstance(row, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _frame_excerpt(row):
    picked = {}
    if isinstance(row, dict):
        for key in ('index', 'frame_id', 'frameId', '帧ID', 'frame', 'id', 'ID', 'time', 'timestamp',
                '时间', 'label', '描述', 'description'):
            if row.get(key) not in (None, ''):
                picked[key] = row.get(key)
    raw = json.dumps(picked, ensure_ascii=False, sort_keys=True) if picked else json.dumps(row, ensure_ascii=False, sort_keys=True)
    return _text(raw, 500)


def _find_frame(session, frame_id):
    state = _video_state(session)
    if state is None:
        return None
    revision, rows = state
    for row in rows:
        if _row_frame_id(row) == frame_id:
            return {'revision': revision, 'row': row, 'excerpt': _frame_excerpt(row)}
    return None


def _video_catalog(session, traces):
    if not _has_video_trace(traces):
        return None
    state = _video_state(session)
    if state is None:
        return None
    revision, rows = state
    frame_ids = []
    for row in rows:
        frame_id = _row_frame_id(row)
        if frame_id is not None and frame_id not in frame_ids:
            frame_ids.append(frame_id)
        if len(frame_ids) >= FRAME_LIMIT:
            break
    return {'revision': revision, 'frame_ids': frame_ids, 'frame_count': len(rows)}


def _fact_positions(session, fact_id):
    try:
        if fact_id in ('alerts', 'clusters'):
            found = _black_positions(session, fact_id == 'clusters')
        else:
            found = _positions(session.df, fact_id)
    except Exception:
        return None
    if found is None:
        return None
    return [int(value) for value in found]


def _covered_positions(session, bundle):
    positions = set()
    for fact in _items(bundle.get('facts')):
        if not isinstance(fact, dict) or not isinstance(fact.get('id'), str):
            continue
        found = _fact_positions(session, fact['id'])
        if found is not None:
            positions.update(found)
    return sorted(positions)


def _selected_positions(session, bundle, all_positions=None):
    positions = _covered_positions(session, bundle) if all_positions is None else all_positions
    priority = []
    for fact in _items(bundle.get('facts')):
        key = fact.get('id', '')
        if key.endswith(('.max', '.min')) or key in ('alerts', 'clusters'):
            priority.extend((_fact_positions(session, key) or [])[:20])
    return list(dict.fromkeys([*priority, *positions]))[:POSITION_LIMIT]


def _allowed_refs(session, bundle):
    traces = _items(bundle.get('tools'))
    video = _video_catalog(session, traces)
    return {'fact': {f['id'] for f in _items(bundle.get('facts'))[:80]},
        'record': set(_selected_positions(session, bundle)),
        'rag': set(_rag_entries(traces)), 'tool': set(_success_names(traces)),
        'video_frame': set(video['frame_ids']) if video else set()}


def get_catalog(session, traces):
    traces = _trace_list(traces)
    token = _catalog_token(session, traces)
    bundle = None
    try:
        bundle = build_bundle(session, traces, 'analysis-card-catalog')
    except Exception:
        bundle = None
    facts = []
    positions = []
    if isinstance(bundle, dict):
        for fact in _items(bundle.get('facts')):
            if not isinstance(fact, dict) or not isinstance(fact.get('id'), str):
                continue
            facts.append({key: fact.get(key) for key in ('id', 'label', 'value', 'unit', 'record_count')})
        positions = _covered_positions(session, bundle)
    rag = _rag_entries(traces)
    return {'version': CATALOG_VERSION, 'token': token, 'facts': facts[:80],
        'fact_count': len(facts), 'record_positions': _selected_positions(session, bundle, positions) if isinstance(bundle, dict) else [],
        'record_position_count': len(positions),
        'record_positions_truncated': len(positions) > POSITION_LIMIT,
        'successful_tools': _success_names(traces),
        'rag_evidence_ids': list(rag)[:RAG_LIMIT], 'rag_evidence_count': len(rag),
        'video': _video_catalog(session, traces),
        'limits': {'facts': 80, 'record_positions': POSITION_LIMIT,
            'rag_evidence_ids': RAG_LIMIT, 'video_frames': FRAME_LIMIT},
        'notice': '目录只来自本轮已捕获工具快照；事实ID与记录位置可用于卡片引用，RAG与视频是参考，不是当前观测。'}


def _normalize_suggestion(card):
    suggestion = card.suggestion
    title = (suggestion.title.strip() or card.title.strip())[:120]
    hypothesis = (suggestion.hypothesis.strip() or card.finding.strip())[:2000]
    normalized = {'title': title, 'hypothesis': hypothesis,
        'supporting_notes': suggestion.supporting_notes[:4000],
        'opposing_notes': suggestion.opposing_notes[:4000], 'status': 'open',
        'retest': {'location': suggestion.retest.location[:300], 'latitude': None,
            'longitude': None, 'indicators': list(suggestion.retest.indicators),
            'plan': suggestion.retest.plan[:3000], 'observed_at': '', 'result': ''},
        'review': {'outcome': '', 'reviewer': '', 'notes': ''}}
    return Draft.model_validate(normalized).model_dump()


def _public_suggestion(draft):
    retest = draft.get('retest') if isinstance(draft.get('retest'), dict) else {}
    return {'title': _bound(draft.get('title'), 120),
        'hypothesis': _bound(draft.get('hypothesis'), 2000),
        'supporting_notes': _bound(draft.get('supporting_notes'), 4000),
        'opposing_notes': _bound(draft.get('opposing_notes'), 4000),
        'retest': {'location': _bound(retest.get('location'), 300), 'latitude': None,
            'longitude': None, 'indicators': list(retest.get('indicators') or []),
            'plan': _bound(retest.get('plan'), 3000)}}


def _card_has_video(card):
    return any(ref.kind == 'video_frame' for ref in [*card.support, *card.oppose])


def _stored_has_video(card):
    return any(ref.kind == 'video_frame' for ref in [*card.support, *card.oppose])


def _resolve_ref(session, bundle, ref, allowed=None):
    if not isinstance(ref, Ref):
        ref = Ref.model_validate(ref)
    allowed = _allowed_refs(session, bundle) if allowed is None else allowed
    identity = {'fact': ref.fact_id, 'record': ref.position, 'rag': ref.evidence_id,
        'tool': ref.name, 'video_frame': ref.frame_id}[ref.kind]
    if identity not in allowed[ref.kind]:
        raise ValueError('引用未出现在本轮有界证据目录中')
    if ref.kind == 'fact':
        fact = next((item for item in _items(bundle.get('facts'))
            if isinstance(item, dict) and item.get('id') == ref.fact_id), None)
        if fact is None:
            raise ValueError('统计事实不在本轮证据中')
        count = fact.get('record_count')
        excerpt = f"{fact.get('label')}: {_text(fact.get('value'), 300)} {fact.get('unit') or ''}"
        if count is not None:
            excerpt += f' · 关联 {count} 条记录'
        resolved = {'kind': 'fact', 'label': _text(fact.get('label') or '统计事实', 200),
            'fact_id': ref.fact_id, 'excerpt': _text(excerpt, 500), 'source_url': None,
            'notice': '本轮统计事实，可由当前证据重算。'}
        tool_id = fact.get('tool_id')
        if isinstance(tool_id, str) and tool_id:
            resolved['tool_id'] = tool_id
        return resolved
    if ref.kind == 'record':
        position = ref.position
        if position is None or not covered_record(session, bundle, position):
            raise ValueError('采样记录不在本轮报告支持集合内')
        if '_quality' not in allowed:
            from water_quality_mcp.src.water_quality_mcp.analysis import quality_report
            allowed['_quality'] = quality_report(session.df, session.analysis_config)
        row = record(session, position, allowed['_quality'])
        bundle_id = bundle.get('id')
        source_url = f'/charts?evidence={bundle_id}&row={position}#trace-map' if isinstance(bundle_id, str) else None
        snapshot = {key: row.get(key) for key in ('record_number', 'input_record_id', 'time', 'values', 'coord')}
        resolved = {'kind': 'record', 'label': '采样记录 #' + str(row.get('record_number')),
            'position': position, 'excerpt': _text(json.dumps(snapshot, ensure_ascii=False, sort_keys=True), 500),
            'source_url': source_url,
            'notice': '本轮采样记录；' + ('坐标来自数据记录。' if row.get('coord') else '无有效坐标，仅查看原始记录。')}
        if row.get('map_sample_id') is not None:
            resolved['map_sample_id'] = row.get('map_sample_id')
        return resolved
    if ref.kind == 'tool':
        trace = _tool_trace(bundle, ref.name)
        if trace is None or not isinstance(trace.get('id'), str):
            raise ValueError('工具引用不在本轮成功且未截断的工具快照中')
        return {'kind': 'tool', 'label': '工具 ' + ref.name, 'tool_id': trace['id'],
            'excerpt': '本轮工具 ' + ref.name + ' 成功返回；原文快照可在证据工具中查看。',
            'source_url': None, 'notice': '本轮工具快照，不代表正文每句话已核验。'}
    if ref.kind == 'rag':
        entry = _rag_entries(_items(bundle.get('tools'))).get(ref.evidence_id)
        if entry is None:
            raise ValueError('知识库证据不在本轮成功且未截断的工具输出中')
        resolved = {'kind': 'rag', 'label': entry['title'] or ref.evidence_id,
            'evidence_id': ref.evidence_id, 'excerpt': entry['excerpt'], 'source_url': None,
            'notice': '历史知识库参考片段，不是当前现场观测；仅供治理参考。'}
        if entry.get('tool_id'):
            resolved['tool_id'] = entry['tool_id']
        return resolved
    if ref.kind == 'video_frame':
        if not _has_video_trace(_items(bundle.get('tools'))):
            raise ValueError('本轮没有成功且未截断的视频工具快照')
        frame = _find_frame(session, ref.frame_id)
        if frame is None:
            raise ValueError('视频帧不在当前视频评估中')
        return {'kind': 'video_frame', 'label': '视频帧 #' + str(ref.frame_id),
            'frame_id': ref.frame_id, 'video_revision': frame['revision'],
            'excerpt': frame['excerpt'], 'source_url': None,
            'notice': '当前视频帧参考；需人工查看原视频复核。'}
    raise ValueError('未知引用类型')


def submit_cards(session, payload, traces):
    setattr(session, PROPOSAL_ATTR, None)
    proposal = payload if isinstance(payload, Proposal) else Proposal.model_validate(payload)
    traces = _trace_list(traces)
    token = _catalog_token(session, traces)
    if proposal.catalog_token != token:
        raise ValueError('卡片目录已变化；请重新获取目录后再提交')
    try:
        bundle = build_bundle(session, traces, 'analysis-card-submit')
    except Exception as exc:
        raise ValueError('本轮证据不足以校验卡片引用') from exc
    if not isinstance(bundle, dict):
        raise ValueError('本轮尚未捕获可校验的工具证据')
    status, notice = bundle_status(session, bundle)
    if status != 'current':
        raise ValueError('证据已变化，请重新分析后再提交卡片：' + _text(notice, 200))
    allowed = _allowed_refs(session, bundle)
    for card in proposal.cards:
        for ref in [*card.support, *card.oppose]:
            _resolve_ref(session, bundle, ref, allowed)
    packet = {'version': PROPOSAL_VERSION, 'created_at': utc_now(), 'catalog_token': token,
        'fingerprints': fingerprints(session), 'cards': [card.model_dump() for card in proposal.cards]}
    setattr(session, PROPOSAL_ATTR, packet)
    return {'accepted': True, 'count': len(proposal.cards),
        'notice': '卡片为待确认草稿，尚未写入排查板。'}


def _attach_cards(session, bundle, packet):
    if not isinstance(packet, dict) or packet.get('version') != PROPOSAL_VERSION:
        raise ValueError('卡片提交包无效')
    if (not isinstance(bundle.get('fingerprints'), dict)
            or packet.get('fingerprints') != bundle.get('fingerprints')
            or packet.get('fingerprints') != fingerprints(session)):
        raise ValueError('卡片与最终报告数据指纹不一致')
    tools = _items(bundle.get('tools'))
    if packet.get('catalog_token') != _catalog_token(session, tools):
        raise ValueError('卡片目录令牌与最终证据不一致')
    cards_raw = packet.get('cards')
    if not isinstance(cards_raw, list) or len(cards_raw) > MAX_CARDS:
        raise ValueError('卡片数量无效')
    cards = [Card.model_validate(card) for card in cards_raw]
    status, notice = bundle_status(session, bundle)
    if status != 'current':
        raise ValueError('最终证据不是当前有效状态：' + _text(notice, 200))
    video_revision = None
    if any(_card_has_video(card) for card in cards):
        state = _video_state(session)
        if state is None:
            raise ValueError('视频引用缺少当前评估')
        video_revision = state[0]
    stored = []
    allowed = _allowed_refs(session, bundle)
    for card in cards:
        for ref in [*card.support, *card.oppose]:
            _resolve_ref(session, bundle, ref, allowed)
        stored.append({'id': str(uuid4()), 'title': card.title, 'finding': card.finding,
            'support': [ref.model_dump() for ref in card.support],
            'oppose': [ref.model_dump() for ref in card.oppose], 'gaps': list(card.gaps),
            'suggestion': _normalize_suggestion(card)})
    bundle['analysis_cards'] = {'version': CARD_VERSION, 'created_at': utc_now(),
        'catalog_token': packet['catalog_token'], 'video_revision': video_revision,
        'cards': stored}


def attach_cards(session, bundle):
    if not isinstance(bundle, dict):
        return bundle
    if isinstance(bundle.get('analysis_cards'), dict):
        return bundle
    packet = getattr(session, PROPOSAL_ATTR, None)
    if packet is None:
        return bundle
    try:
        _attach_cards(session, bundle, packet)
    except Exception:
        bundle['analysis_cards'] = {'version': CARD_VERSION, 'status': 'invalid',
            'notice': '研判卡片与最终证据不一致或格式无效，暂不可用；原报告已保留。'}
    return bundle


def _public_result(status, notice, cards):
    return {'version': CARD_VERSION, 'status': status, 'notice': _text(notice, 500), 'cards': cards}


def public_cards(session, bundle):
    if not isinstance(bundle, dict):
        return _public_result('unavailable', '没有可读取的报告证据。', [])
    raw = bundle.get('analysis_cards')
    if raw is None:
        return _public_result('unavailable', '本回答未生成研判卡片；仅保留原报告证据。', [])
    if isinstance(raw, dict) and raw.get('status') == 'invalid':
        return _public_result('invalid', raw.get('notice') or '研判卡片格式无效，暂不可用。', [])
    status, notice = bundle_status(session, bundle)
    if status != 'current':
        return _public_result('stale', notice, [])
    try:
        packet = _StoredPacket.model_validate(raw)
        if packet.catalog_token != _catalog_token(session, _items(bundle.get('tools'))):
            raise ValueError('卡片目录令牌不匹配')
        has_video = any(_stored_has_video(card) for card in packet.cards)
        if has_video:
            if packet.video_revision is None:
                raise ValueError('视频引用缺少版本')
            state = _video_state(session)
            if state is None or state[0] != packet.video_revision:
                return _public_result('stale', '视频帧或视频评估版本已变化，请重新分析。', [])
        cards = []
        allowed = _allowed_refs(session, bundle)
        for card in packet.cards:
            support = [_resolve_ref(session, bundle, ref, allowed) for ref in card.support]
            oppose = [_resolve_ref(session, bundle, ref, allowed) for ref in card.oppose]
            if has_video and any(ref.get('kind') == 'video_frame' and ref.get('video_revision') != packet.video_revision for ref in [*support, *oppose]):
                return _public_result('stale', '视频帧版本已变化，请重新分析。', [])
            confirmed_task_id = None
            for item in getattr(session, 'investigations', {}).get('items', []):
                origin = item.get('origin', {}) if isinstance(item, dict) else {}
                provenance = origin.get('analysis_card') if isinstance(origin, dict) else None
                if (isinstance(provenance, dict) and provenance.get('bundle_id') == bundle.get('id')
                        and provenance.get('card_id') == card.id):
                    confirmed_task_id = item.get('id')
                    break
            cards.append({'id': card.id, 'title': card.title, 'finding': card.finding,
                'confirmed_task_id': confirmed_task_id,
                'support': support, 'oppose': oppose, 'gaps': list(card.gaps),
                'suggestion': _public_suggestion(card.suggestion.model_dump()),
                'opposing_evidence_status': 'provided' if oppose else 'not_provided'})
        return _public_result('ready', '卡片为 AI 辅助草稿，需人工确认；不代表污染责任、概率或已确认结论。', cards)
    except Exception:
        return _public_result('invalid', '研判卡片结构或引用无法在当前证据中核验，暂不可用。', [])


def get_card(session, bundle, card_id):
    public = public_cards(session, bundle)
    if public.get('status') != 'ready':
        raise ValueError(public.get('notice') or '研判卡片当前不可用')
    for card in public.get('cards', []):
        if card.get('id') == card_id:
            return card
    raise KeyError('研判卡片不存在')


__all__ = ['get_catalog', 'submit_cards', 'attach_cards', 'public_cards', 'get_card']
