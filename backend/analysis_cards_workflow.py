'''研判卡片的预览与确认工作流。

只负责把已校验的卡片转成可编辑草稿、做幂等确认，并在成功提交后写入来源。
卡片目录和卡片的生成/校验在 backend/analysis_cards.py。
'''
from __future__ import annotations

import hashlib
import json
import math
import string
import time
from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .analysis_cards import get_card
from .investigations import (
    BoardError, Create, Draft, Reference, Retest, Review,
    context as board_context,
    create as create_task,
    public_board,
    _reference,
)
from .report_evidence import bundle_status, utc_now

PREVIEW_TTL = 300
PREVIEW_MAX = 20
VALID_INDICATORS = {'ammonia', 'do', 'cod', 'ph', 'turbidity', 'sd', 'orp', 'flow', 'connection'}
PROVENANCE_KEYS = {'bundle_id', 'card_id', 'preview_id', 'request_id', 'confirmed_at', 'draft_hash'}


class Confirm(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    preview_id: str = Field(min_length=1, max_length=64)
    request_id: UUID
    draft: Draft

    @field_validator('request_id', mode='before')
    @classmethod
    def _uuid(cls, value):
        if isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                return UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError('request_id 须为有效 UUID') from exc
        raise ValueError('request_id 须为有效 UUID')

    @model_validator(mode='after')
    def _new_task_only(self):
        draft = self.draft
        if draft.status != 'open':
            raise ValueError('新确认任务须保持“待排查”状态；复测与复核请在排查板中另行记录')
        if draft.review.outcome or draft.review.reviewer or draft.review.notes:
            raise ValueError('新确认任务不能包含人工复核结论')
        if draft.retest.observed_at or draft.retest.result:
            raise ValueError('新确认任务不能包含实际复测时间或结果')
        return self


def provenance_validate(value):
    '''校验 origin.analysis_card；返回规范副本，None 表示没有来源标记。'''
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != PROVENANCE_KEYS:
        raise ValueError('排查任务来源标记字段无效')
    for key in ('bundle_id', 'card_id', 'preview_id', 'request_id'):
        text = value[key]
        if not isinstance(text, str) or len(text) != 36:
            raise ValueError('排查任务来源标记标识无效')
        try:
            if str(UUID(text)) != text:
                raise ValueError('标识须为规范 UUID')
        except (ValueError, AttributeError) as exc:
            raise ValueError('排查任务来源标记标识无效') from exc
    try:
        UUID(value['request_id'])
    except (ValueError, AttributeError) as exc:
        raise ValueError('排查任务来源标记请求ID无效') from exc
    stamp = value['confirmed_at']
    if not isinstance(stamp, str) or not stamp or len(stamp) > 50:
        raise ValueError('排查任务来源标记时间无效')
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('排查任务来源标记时间无效') from exc
    if parsed.utcoffset() is None:
        raise ValueError('排查任务来源标记时间须包含时区')
    digest = value['draft_hash']
    if not isinstance(digest, str) or len(digest) != 64 or any(ch not in string.hexdigits for ch in digest):
        raise ValueError('排查任务来源标记摘要无效')
    return dict(value)


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def _text(value, limit):
    if not isinstance(value, str):
        return ''
    return value.strip()[:limit]


def _indicators(value):
    out = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item in VALID_INDICATORS and item not in out:
                out.append(item)
    return out[:9]


def _coord(row):
    coord = row.get('coord') if isinstance(row, dict) else None
    if not (isinstance(coord, (list, tuple)) and len(coord) == 2):
        return None, None
    lat, lon = coord
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
               for x in (lat, lon)):
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return float(lat), float(lon)


def _bundle_id(bundle):
    if not isinstance(bundle, dict):
        raise ValueError('报告证据无效')
    bundle_id = bundle.get('id')
    if not isinstance(bundle_id, str) or not bundle_id:
        raise ValueError('报告证据标识无效')
    return bundle_id


def _require_current(session, bundle):
    status, notice = bundle_status(session, bundle)
    if status != 'current':
        raise BoardError(notice)


def _load_card(session, bundle, card_id):
    if not isinstance(card_id, str) or not card_id:
        raise KeyError('研判卡片不存在')
    try:
        card = get_card(session, bundle, card_id)
    except KeyError as exc:
        raise KeyError('该报告没有可用的研判卡片') from exc
    except ValueError as exc:
        raise BoardError(str(exc)) from exc
    if not isinstance(card, dict) or card.get('id') != card_id:
        raise KeyError('该报告没有可用的研判卡片')
    return card


def _ref_identity(ref):
    if not isinstance(ref, dict):
        return None
    keys = ('kind', 'fact_id', 'position', 'map_sample_id', 'evidence_id', 'tool_id', 'frame_id', 'video_revision')
    return {key: ref.get(key) for key in keys if key in ref}


def _card_digest(card):
    support = card.get('support') if isinstance(card.get('support'), list) else []
    oppose = card.get('oppose') if isinstance(card.get('oppose'), list) else []
    return _hash({
        'id': card.get('id'),
        'title': card.get('title'),
        'finding': card.get('finding'),
        'gaps': card.get('gaps') if isinstance(card.get('gaps'), list) else [],
        'suggestion': card.get('suggestion') if isinstance(card.get('suggestion'), dict) else {},
        'support': [_ref_identity(ref) for ref in support],
        'oppose': [_ref_identity(ref) for ref in oppose],
    })


def _record_position(card):
    # 多个被引点位并不等于已经选择复测目标，不能任意拿第一个点的坐标。
    positions = set()
    for group in ('support', 'oppose'):
        for ref in card.get(group, []):
            if isinstance(ref, dict) and ref.get('kind') == 'record':
                value = ref.get('position')
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    positions.add(value)
    return next(iter(positions)) if len(positions) == 1 else None


def _draft_from_card(card, lat, lon):
    suggestion = card.get('suggestion') if isinstance(card.get('suggestion'), dict) else {}
    retest = suggestion.get('retest') if isinstance(suggestion.get('retest'), dict) else {}
    title = _text(suggestion.get('title') or card.get('title'), 120)
    hypothesis = _text(suggestion.get('hypothesis') or card.get('finding'), 2000)
    if not title or not hypothesis:
        raise ValueError('研判卡片缺少可用的标题或假设')
    return Draft(
        title=title,
        hypothesis=hypothesis,
        supporting_notes=_text(suggestion.get('supporting_notes'), 4000),
        opposing_notes=_text(suggestion.get('opposing_notes'), 4000),
        status='open',
        retest=Retest(
            location=_text(retest.get('location'), 300),
            latitude=lat,
            longitude=lon,
            indicators=_indicators(retest.get('indicators')),
            plan=_text(retest.get('plan'), 3000),
        ),
        review=Review(),
    )


def _prepare_draft(session, bundle, card, source):
    '''用当前卡片重新确定记录关联和坐标；模型/浏览器坐标不直接信任。'''
    position = _record_position(card)
    reference = None
    lat = lon = None
    if position is not None:
        reference = Reference(bundle_id=_bundle_id(bundle), position=position)
        _, row = _reference(session, reference)
        lat, lon = _coord(row)
    data = source.model_dump()
    retest = data.get('retest')
    if not isinstance(retest, dict):
        retest = {}
    retest['latitude'] = lat
    retest['longitude'] = lon
    data['retest'] = retest
    return Draft.model_validate(data), reference


def _preview_notice(lat, lon, position):
    base = '这是可编辑的复测草稿，不代表污染结论；只有显式确认后才会写入排查板。'
    if lat is not None and lon is not None:
        return base + f'参考坐标取自关联采样记录 #{position+1}，复测目标及位置说明仍须核对。'
    return base + '未取得唯一关联记录的有效坐标（多点引用时不任意选点），经纬度保持为空。'


def _previews(session):
    cache = getattr(session, '_analysis_card_previews', None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(session, '_analysis_card_previews', cache)
    now = time.time()
    for key in list(cache):
        entry = cache.get(key)
        if (not isinstance(entry, dict)
                or not isinstance(entry.get('expires_at'), (int, float))
                or entry['expires_at'] <= now):
            cache.pop(key, None)
    while len(cache) >= PREVIEW_MAX:
        cache.pop(next(iter(cache)))
    return cache


def _get_preview(session, preview_id):
    cache = getattr(session, '_analysis_card_previews', None)
    if not isinstance(cache, dict):
        raise KeyError('预览不存在，请重新生成复测草稿')
    entry = cache.get(preview_id)
    if not isinstance(entry, dict):
        raise KeyError('预览不存在，请重新生成复测草稿')
    if not isinstance(entry.get('expires_at'), (int, float)) or entry['expires_at'] <= time.time():
        cache.pop(preview_id, None)
        raise BoardError('预览已过期，请重新生成复测草稿')
    return entry


def _remove_preview(session, preview_id):
    cache = getattr(session, '_analysis_card_previews', None)
    if isinstance(cache, dict):
        cache.pop(preview_id, None)


def _validate_preview(session, entry, bundle_id, card_id, card_digest):
    if entry.get('bundle_id') != bundle_id or entry.get('card_id') != card_id:
        raise BoardError('预览与当前报告或卡片不匹配，请重新生成复测草稿')
    fp, scope = board_context(session)
    if (entry.get('project_id') != session.project_id
            or entry.get('scope_id') != scope
            or entry.get('expected_revision') != session.investigations['revision']):
        raise BoardError('数据、项目或排查记录已变化；请重新生成复测草稿')
    if entry.get('fingerprints') != fp:
        raise BoardError('数据或分析上下文已变化；请重新生成复测草稿')
    if entry.get('card_digest') != card_digest:
        raise BoardError('研判卡片内容已变化；请重新生成复测草稿')


def _provenance_items(session):
    board = session.investigations if isinstance(getattr(session, 'investigations', None), dict) else {}
    items = board.get('items') if isinstance(board.get('items'), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        origin = item.get('origin')
        if not isinstance(origin, dict):
            continue
        provenance = origin.get('analysis_card')
        if provenance is None and 'analysis_card' not in origin:
            continue
        if not isinstance(provenance, dict):
            raise BoardError('已有卡片确认来源无效，请核对项目后再保存。')
        try:
            provenance_validate(provenance)
        except ValueError as exc:
            raise BoardError('已有卡片确认来源无效，请核对项目后再保存。') from exc
        yield item, provenance


def _find_existing(session, bundle_id, card_id, preview_id, request_id):
    card_item = card_prov = request_item = request_prov = preview_item = preview_prov = None
    for item, provenance in _provenance_items(session):
        if provenance['bundle_id'] == bundle_id and provenance['card_id'] == card_id:
            card_item, card_prov = item, provenance
        if provenance['request_id'] == request_id:
            request_item, request_prov = item, provenance
        if provenance['preview_id'] == preview_id:
            preview_item, preview_prov = item, provenance
    return card_item, card_prov, request_item, request_prov, preview_item, preview_prov


def _same_context(session, item):
    origin = item.get('origin') if isinstance(item, dict) else None
    basis = origin.get('fingerprints') if isinstance(origin, dict) else None
    fp, _ = board_context(session)
    return basis == fp


def _saved_notice(session, idempotent=False):
    if idempotent:
        return '该确认请求已处理，未重复创建排查任务。'
    if session.project_id:
        return '已确认并保存到当前项目排查板；该项为人工确认的候选假设，不代表污染结论。'
    return '已加入当前会话排查板；尚未保存为项目，请在项目栏显式保存。'


def _confirmed_response(session, task_id, bundle_id, card_id, preview_id, idempotent):
    return {
        'board': public_board(session),
        'task_id': task_id,
        'bundle_id': bundle_id,
        'card_id': card_id,
        'preview_id': preview_id,
        'idempotent': idempotent,
        'persisted_project': bool(session.project_id),
        'notice': _saved_notice(session, idempotent),
    }


def _existing_response(session, bundle, item, provenance, card_id, request_id, payload_draft, submitted_hash):
    if not _same_context(session, item):
        raise BoardError('数据或上下文已变化；该确认请求不能作为当前任务复用。')
    if provenance.get('request_id') != request_id:
        raise BoardError('该研判卡片已确认保存过，不能重复创建任务；如需修改请在排查板中编辑该任务。')
    if provenance.get('draft_hash') != submitted_hash:
        # 正常前端不会改坐标；若提交内容被服务端规范化过，再用当前卡片计算一次有效草稿摘要。
        try:
            _require_current(session, bundle)
            card = _load_card(session, bundle, card_id)
            effective, _ = _prepare_draft(session, bundle, card, payload_draft)
        except BoardError:
            raise
        except (KeyError, ValueError) as exc:
            raise BoardError('同一请求ID对应的草稿内容不一致，已拒绝重复提交。') from exc
        if provenance.get('draft_hash') != _hash(effective.model_dump()):
            raise BoardError('同一请求ID对应的草稿内容不一致，已拒绝重复提交。')
    task_id = item.get('id')
    if not isinstance(task_id, str):
        raise BoardError('已有排查任务标识无效；请刷新排查板后重试。')
    return _confirmed_response(session, task_id, provenance['bundle_id'], provenance['card_id'],
                               provenance['preview_id'], True)


def preview(session, bundle, card_id):
    _require_current(session, bundle)
    bundle_id = _bundle_id(bundle)
    card = _load_card(session, bundle, card_id)
    card_digest = _card_digest(card)
    position = _record_position(card)
    lat = lon = None
    if position is not None:
        _, row = _reference(session, Reference(bundle_id=bundle_id, position=position))
        lat, lon = _coord(row)
    draft = _draft_from_card(card, lat, lon)
    fp, scope = board_context(session)
    preview_id = str(uuid4())
    cache = _previews(session)
    cache[preview_id] = {
        'preview_id': preview_id,
        'bundle_id': bundle_id,
        'card_id': card_id,
        'project_id': session.project_id,
        'scope_id': scope,
        'expected_revision': session.investigations['revision'],
        'fingerprints': fp,
        'card_digest': card_digest,
        'draft': draft.model_dump(),
        'created_at': utc_now(),
        'expires_at': time.time() + PREVIEW_TTL,
    }
    while len(cache) > PREVIEW_MAX:
        cache.pop(next(iter(cache)))
    return {
        'preview_id': preview_id,
        'bundle_id': bundle_id,
        'card_id': card_id,
        'scope_id': scope,
        'expected_revision': session.investigations['revision'],
        'draft': draft,
        'notice': _preview_notice(lat, lon, position),
    }


def confirm(session, bundle, card_id, payload):
    bundle_id = _bundle_id(bundle)
    request_id = str(payload.request_id)
    submitted_hash = _hash(payload.draft.model_dump())
    card_item, card_prov, request_item, request_prov, preview_item, preview_prov = _find_existing(
        session, bundle_id, card_id, payload.preview_id, request_id)

    if preview_item is not None and card_item is not None and preview_item.get('id') != card_item.get('id'):
        raise BoardError('该预览已用于其他研判任务，不能重复使用。')
    if card_item is not None:
        if (card_prov['preview_id'] != payload.preview_id
                or (request_item is not None and request_item['id'] != card_item['id'])):
            raise BoardError('确认请求与原预览不匹配；该卡片已保存，请在排查板编辑。')
        _require_current(session, bundle)
        return _existing_response(session, bundle, card_item, card_prov, card_id,
                                  request_id, payload.draft, submitted_hash)
    if request_item is not None:
        raise BoardError('该请求ID已用于其他研判卡片，不能重复使用。')
    if preview_item is not None:
        raise BoardError('该预览已用于其他研判任务，不能重复使用。')

    _require_current(session, bundle)
    card = _load_card(session, bundle, card_id)
    card_digest = _card_digest(card)
    entry = _get_preview(session, payload.preview_id)
    _validate_preview(session, entry, bundle_id, card_id, card_digest)

    draft, reference = _prepare_draft(session, bundle, card, payload.draft)
    saved_hash = _hash(draft.model_dump())
    create_payload = Create(
        scope_id=entry['scope_id'],
        expected_revision=session.investigations['revision'],
        draft=draft,
        reference=reference,
    )
    board, task_id = create_task(session, create_payload)
    item = next((candidate for candidate in board['items'] if candidate.get('id') == task_id), None)
    if item is None:
        raise RuntimeError('新排查任务未在提交板中找到')
    origin = item.setdefault('origin', {})
    provenance = {
        'bundle_id': bundle_id,
        'card_id': card_id,
        'preview_id': payload.preview_id,
        'request_id': request_id,
        'confirmed_at': utc_now(),
        'draft_hash': saved_hash,
    }
    provenance_validate(provenance)
    origin['analysis_card'] = provenance

    from .routers.investigations import commit as commit_board
    result = commit_board(session, board, task_id)
    _remove_preview(session, payload.preview_id)
    return {
        **result,
        'bundle_id': bundle_id,
        'card_id': card_id,
        'preview_id': payload.preview_id,
        'idempotent': False,
        'persisted_project': bool(session.project_id),
        'notice': _saved_notice(session, False),
    }
