"""人工排查闭环。记录假设和复核，不把线索或人工声明当作责任结论。"""
from copy import deepcopy
from datetime import datetime
from typing import Literal
from uuid import uuid4
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .report_evidence import (fingerprints, _digest, utc_now, find_bundle, bundle_status,
    covered_record, record)

STATES = {'open': '待排查', 'planned': '已安排复测', 'sampled': '已记录复测',
    'reviewed': '已检查记录', 'closed': '已结束本轮排查'}
OUTCOMES = {'supported': '本次记录支持假设', 'not_supported': '本次记录不支持假设',
    'inconclusive': '证据不足，暂无法区分'}
INDICATORS = {'ammonia', 'do', 'cod', 'ph', 'turbidity', 'sd', 'orp', 'flow', 'connection'}


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Retest(Model):
    location: str = Field('', max_length=300)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    indicators: list[Literal['ammonia', 'do', 'cod', 'ph', 'turbidity', 'sd', 'orp', 'flow', 'connection']] = Field(default_factory=list, max_length=9)
    plan: str = Field('', max_length=3000)
    observed_at: str = Field('', max_length=50)
    result: str = Field('', max_length=4000)

    @model_validator(mode='after')
    def check(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError('经纬度须成对填写；没有实测坐标时可填写位置说明')
        if len(set(self.indicators)) != len(self.indicators):
            raise ValueError('复测指标不可重复')
        if self.observed_at:
            stamp = datetime.fromisoformat(self.observed_at.replace('Z', '+00:00'))
            if stamp.utcoffset() is None:
                raise ValueError('复测时间须包含时区，如 +08:00')
        return self


class Review(Model):
    outcome: Literal['', 'supported', 'not_supported', 'inconclusive'] = ''
    reviewer: str = Field('', max_length=100)
    notes: str = Field('', max_length=4000)


class Draft(Model):
    title: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=2000)
    supporting_notes: str = Field('', max_length=4000)
    opposing_notes: str = Field('', max_length=4000)
    status: Literal['open', 'planned', 'sampled', 'reviewed', 'closed'] = 'open'
    retest: Retest = Field(default_factory=Retest)
    review: Review = Field(default_factory=Review)

    @model_validator(mode='after')
    def check(self):
        if not self.title.strip() or not self.hypothesis.strip():
            raise ValueError('标题和候选假设不能为空白')
        if self.status != 'open' and (not self.retest.plan.strip() or not self.retest.indicators
                or not (self.retest.location.strip() or self.retest.latitude is not None)):
            raise ValueError('请补全复测位置、指标与计划')
        if self.status in ('sampled', 'reviewed', 'closed') and (
                not self.retest.observed_at or not self.retest.result.strip()):
            raise ValueError('请补全带时区的复测时间及实际记录')
        if self.status in ('reviewed', 'closed') and (not self.review.outcome
                or not self.review.reviewer.strip() or not self.review.notes.strip()
                or not (self.supporting_notes.strip() or self.opposing_notes.strip())):
            raise ValueError('请记录支持/反对依据、复核人、复核意见和结论')
        return self


class Reference(Model):
    bundle_id: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0)


class Revision(Model):
    scope_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=0)


class Create(Revision):
    draft: Draft | None = None
    reference: Reference | None = None


class Update(Revision):
    draft: Draft
    acknowledge_review: bool = False


class BoardError(ValueError):
    pass


def empty_board():
    return {'version': 1, 'revision': 0, 'items': []}


def context(session):
    fp = fingerprints(session)
    return fp, _digest({'session': session.id, 'project': session.project_id,
        'analysis_revision': session.analysis_revision, 'fingerprints': fp})


def check_revision(session, payload):
    fp, scope = context(session)
    if payload.scope_id != scope or payload.expected_revision != session.investigations['revision']:
        raise BoardError('数据、项目或排查记录已变化；保留草稿，请重新载入后核对再保存。')
    return fp


def _reference(session, ref):
    bundle = find_bundle(session, ref.bundle_id)
    if bundle is None or bundle_status(session, bundle)[0] != 'current':
        raise BoardError('报告证据已过期或不在当前会话，不能作为当前记录加入排查任务')
    if not covered_record(session, bundle, ref.position):
        raise BoardError('该采样记录不在报告支持集合内')
    row = record(session, ref.position)
    snapshot = {k: row[k] for k in ('record_number', 'input_record_id', 'time', 'values', 'coord')}
    return {**ref.model_dump(), 'excerpt': json.dumps(snapshot, ensure_ascii=False),
        'captured_at': utc_now()}, row


def create(session, payload):
    fp = check_revision(session, payload)
    if len(session.investigations['items']) >= 50:
        raise BoardError('当前项目最多保留50个排查任务，请按独立调查分项目管理')
    ref, row = _reference(session, payload.reference) if payload.reference else (None, None)
    draft = payload.draft
    if draft is None:
        if row is None:
            raise ValueError('请提供候选假设或选择报告采样记录')
        coord = row['coord']
        draft = Draft(title=f"记录 #{row['record_number']} 的读数待复核",
            hypothesis='该点读数可能受局部输入、时空差异或传感器偏差影响，需复测区分。',
            retest=Retest(location=f"采样记录 #{row['record_number']} 附近及上下游对照位置",
                latitude=coord[0] if coord else None, longitude=coord[1] if coord else None,
                indicators=['ammonia', 'do'], plan='核对采样时刻与水流信息，在候选位置和对照位置重复采样。'))
    if draft.status != 'open':
        raise ValueError('新任务从待排查开始，请另行记录复测与检查记录')
    now = utc_now()
    item = {'id': str(uuid4()), 'draft': draft.model_dump(), 'reference': ref,
        'origin': {'fingerprints': fp, 'filename': session.data_filename,
            'identity': session.data_identity}, 'review_context': None,
        'created_at': now, 'updated_at': now, 'history': []}
    board = deepcopy(session.investigations)
    board['items'].append(item); board['revision'] += 1
    return board, item['id']


def update(session, task_id, payload):
    fp = check_revision(session, payload)
    board = deepcopy(session.investigations)
    item = next((item for item in board['items'] if item['id'] == task_id), None)
    if item is None:
        raise KeyError('当前项目没有该排查任务')
    if payload.draft.status in ('reviewed', 'closed') and not payload.acknowledge_review:
        raise ValueError('请显式确认已检查本次记录；勾选不代表确认污染责任')
    now = utc_now()
    item['history'] = (item['history'] + [{'at': item['updated_at'], 'draft': item['draft'],
        'review_context': item['review_context']}])[-20:]
    item['draft'] = payload.draft.model_dump()
    item['updated_at'] = now
    item['review_context'] = fp if payload.draft.status in ('reviewed', 'closed') else None
    board['revision'] += 1
    return board, item['id']


def public_board(session):
    fp, scope = context(session)
    items = []
    for item in session.investigations['items']:
        basis = item['review_context'] or item['origin']['fingerprints']
        stale = fp != basis
        ref = item['reference']
        reference_status = None
        if ref:
            bundle = find_bundle(session, ref['bundle_id'])
            reference_status = bundle_status(session, bundle)[0] if bundle else 'missing'
        items.append({**item, 'basis_status': 'stale' if stale else 'current',
            'effective_status': 'needs_recheck' if stale and item['draft']['status'] in ('reviewed', 'closed') else item['draft']['status'],
            'reference_status': reference_status,
            'ai_assisted': bool(item['origin'].get('analysis_card')),
            'origin_label': 'AI 辅助建议，经用户确认加入' if item['origin'].get('analysis_card') else '人工录入'})
    return {'revision': session.investigations['revision'], 'scope_id': scope, 'items': items,
        'persisted_project': bool(session.project_id), 'project_revision': session.project_revision,
        'notice': '假设与笔记包含人工录入或经用户确认的 AI 建议，复测及复核须人工记录，均未独立核验；排查结束不代表确认污染源或法律责任。'}


def validate_board(board):
    """校验导入内容；不执行自由文本，不信任导入包自行声称的当前状态。"""
    if not isinstance(board, dict) or set(board) != {'version', 'revision', 'items'} or board['version'] != 1 or type(board['revision']) is not int or board['revision'] < 0:
        raise ValueError('排查任务版本无效')
    if not isinstance(board['items'], list) or len(board['items']) > 50:
        raise ValueError('排查任务数量无效')
    ids = set()
    card_sources, card_requests, card_previews = set(), set(), set()
    def check_fp(fp):
        if not isinstance(fp, dict) or set(fp) != {'data', 'context'} or any(not isinstance(v, str) or len(v) != 64 for v in fp.values()):
            raise ValueError('排查任务数据依据无效')
    for item in board['items']:
        if not isinstance(item, dict) or set(item) != {'id','draft','reference','origin','review_context','created_at','updated_at','history'}:
            raise ValueError('排查任务字段无效')
        if not isinstance(item['id'], str) or len(item['id']) != 36 or item['id'] in ids:
            raise ValueError('排查任务标识无效')
        ids.add(item['id']); Draft.model_validate(item['draft'])
        origin = item['origin']
        if not isinstance(origin, dict) or set(origin) not in ({'fingerprints','filename','identity'}, {'fingerprints','filename','identity','analysis_card'}) or not isinstance(origin['filename'], str) or not isinstance(origin['identity'], dict):
            raise ValueError('排查任务来源无效')
        check_fp(origin['fingerprints'])
        if 'analysis_card' in origin:
            from .analysis_cards_workflow import provenance_validate
            if origin['analysis_card'] is None:
                raise ValueError('研判卡片来源标记无效')
            provenance = provenance_validate(origin['analysis_card'])
            source = (provenance['bundle_id'], provenance['card_id'])
            if (source in card_sources or provenance['request_id'] in card_requests
                    or provenance['preview_id'] in card_previews):
                raise ValueError('研判卡片确认来源重复，无法安全恢复')
            card_sources.add(source); card_requests.add(provenance['request_id']); card_previews.add(provenance['preview_id'])
        if item['review_context'] is not None:
            check_fp(item['review_context'])
        for stamp in ('created_at','updated_at'):
            if not isinstance(item[stamp], str) or datetime.fromisoformat(item[stamp]).utcoffset() is None:
                raise ValueError('排查任务时间无效')
        ref = item['reference']
        if ref is not None:
            if not isinstance(ref, dict) or set(ref) != {'bundle_id','position','excerpt','captured_at'} or not isinstance(ref['excerpt'], str) or len(ref['excerpt']) > 10000:
                raise ValueError('排查记录关联无效')
            Reference.model_validate({k: ref[k] for k in ('bundle_id','position')})
        if not isinstance(item['history'], list) or len(item['history']) > 20:
            raise ValueError('排查任务修改历史无效')
        for event in item['history']:
            if not isinstance(event, dict) or set(event) != {'at','draft','review_context'} or not isinstance(event['at'], str):
                raise ValueError('排查任务修改历史字段无效')
            Draft.model_validate(event['draft'])
            if event['review_context'] is not None:
                check_fp(event['review_context'])
