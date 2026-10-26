'''研判卡片契约验收：受控数据，不代表现场准确率。'''
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.main import app
from backend.session import Session, STORE
from backend.agent.tools import execute_tool
from backend.report_evidence import build_bundle, capture_tool, utc_now, TOOL_LIMIT, record
from backend.project_codec import dump_project, load_project
from backend import investigations as core
from backend import analysis_cards as cards
from backend import analysis_cards_workflow as wf
from backend.projects import ProjectConflict, apply_snapshot
from test_report_evidence_regressions import sample_session


def traces_for(session, names=('get_data_summary', 'find_black_spots'), extra=()):
    traces = []
    for name in names:
        started = utc_now()
        raw, failed = execute_tool(name, {}, session)
        assert not failed, raw
        traces.append(capture_tool(name, {}, raw, started))
    traces.extend(extra)
    return traces


def rag_trace(evidence_id='rag-1', excerpt='受控知识片段', title='受控案例'):
    data = {'检索结果': [{'标题': title, '地点': '受控地点', 'url': 'http://example.invalid/evil',
        '证据片段': [{'证据ID': evidence_id, '原文': excerpt}]}]}
    raw = json.dumps({'ok': True, 'data': data}, ensure_ascii=False)
    return capture_tool('search_treatment_cases', {'query': '受控'}, raw, utc_now())


def video_trace():
    return capture_tool('analyze_video', {}, json.dumps({'ok': True, 'data': {'frames': []}}), utc_now())


def truncated_rag_trace(evidence_id='rag-trunc'):
    data = {'检索结果': [{'标题': '受控', '证据片段': [{'证据ID': evidence_id, '原文': 'x' * TOOL_LIMIT}]}]}
    raw = json.dumps({'ok': True, 'data': data}, ensure_ascii=False)
    trace = capture_tool('search_treatment_cases', {}, raw, utc_now())
    assert trace['truncated']
    return trace


def card_payload(fact_id, oppose_position=None, rag_id=None, video_frame=None, **overrides):
    support = [{'kind': 'fact', 'fact_id': fact_id}]
    if rag_id is not None:
        support.append({'kind': 'rag', 'evidence_id': rag_id})
    if video_frame is not None:
        support.append({'kind': 'video_frame', 'frame_id': video_frame})
    oppose = []
    if oppose_position is not None:
        oppose.append({'kind': 'record', 'position': oppose_position})
    base = {
        'title': '受控研判卡片',
        'finding': '受控发现：仅用于契约测试，不是现场结论。',
        'support': support,
        'oppose': oppose,
        'gaps': ['缺少校准记录'],
        'suggestion': {
            'title': '受控复测建议',
            'hypothesis': '受控假设，需人工复测。',
            'supporting_notes': '受控支持说明。',
            'opposing_notes': '受控反对说明。',
            'retest': {'location': '受控点位', 'indicators': ['ammonia', 'do'], 'plan': '重复采样并记录对照点。'},
        },
    }
    base.update(overrides)
    return base


def pick_coord_position(session, catalog):
    for position in catalog['record_positions']:
        row = record(session, position)
        coord = row.get('coord')
        if (isinstance(coord, (list, tuple)) and len(coord) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in coord)):
            return position
    raise AssertionError('受控样本没有可用坐标记录')


class AnalysisCardCoreTests(unittest.TestCase):
    def setUp(self):
        self.s = sample_session()
        self.traces = traces_for(self.s)
        self.catalog = cards.get_catalog(self.s, self.traces)
        self.fact_id = next((f['id'] for f in self.catalog['facts']
            if f['id'] == 'ammonia_nitrogen.mean'), self.catalog['facts'][0]['id'])
        self.position = pick_coord_position(self.s, self.catalog)

    def test_catalog_is_bounded_and_bound_to_current_fingerprints(self):
        cat = self.catalog
        self.assertEqual(cat['version'], cards.CATALOG_VERSION)
        self.assertTrue(cat['token'].startswith('ac1_'))
        self.assertIn('get_data_summary', cat['successful_tools'])
        self.assertTrue(cat['facts'])
        self.assertTrue(cat['record_positions'])
        self.assertIsNone(cat['video'])
        self.assertLessEqual(len(cat['facts']), cat['limits']['facts'])
        self.assertLessEqual(len(cat['record_positions']), cat['limits']['record_positions'])
        self.s.df.iloc[0, self.s.df.columns.get_loc('ammonia_nitrogen')] = .4
        changed = cards.get_catalog(self.s, self.traces)
        self.assertNotEqual(changed['token'], cat['token'])

    def test_submit_and_attach_ready_cards_with_server_ids_and_excerpts(self):
        traces = self.traces + [rag_trace('rag-1', '受控历史片段')]
        cat = cards.get_catalog(self.s, traces)
        fact_id = next(f['id'] for f in cat['facts'] if f['id'] == self.fact_id)
        pos = pick_coord_position(self.s, cat)
        eid = cat['rag_evidence_ids'][0]
        payload = {'catalog_token': cat['token'], 'cards': [
            card_payload(fact_id, oppose_position=pos),
            card_payload(fact_id, rag_id=eid),
        ]}
        accepted = cards.submit_cards(self.s, payload, traces)
        self.assertTrue(accepted['accepted'])
        self.assertEqual(accepted['count'], 2)
        self.assertTrue(hasattr(self.s, cards.PROPOSAL_ATTR))
        self.assertEqual(self.s.investigations['items'], [])
        bundle = build_bundle(self.s, traces, 'analysis-cards-final')
        self.assertIsNotNone(bundle)
        cards.attach_cards(self.s, bundle)
        public = cards.public_cards(self.s, bundle)
        self.assertEqual(public['version'], cards.CARD_VERSION)
        self.assertEqual(public['status'], 'ready')
        self.assertEqual(len(public['cards']), 2)
        first, second = public['cards']
        UUID(first['id'])
        self.assertEqual(first['opposing_evidence_status'], 'provided')
        self.assertEqual(second['opposing_evidence_status'], 'not_provided')
        self.assertEqual(first['support'][0]['kind'], 'fact')
        self.assertIn('excerpt', first['support'][0])
        self.assertEqual(first['oppose'][0]['position'], pos)
        self.assertIsNone(first['support'][0]['source_url'])
        rag_ref = next(ref for ref in second['support'] if ref['kind'] == 'rag')
        self.assertEqual(rag_ref['excerpt'], '受控历史片段')
        self.assertIsNone(rag_ref['source_url'])
        self.assertNotIn('url', rag_ref)
        self.assertIn('历史知识库参考', rag_ref['notice'])
        self.assertNotIn('probability', json.dumps(first['suggestion'], ensure_ascii=False))

    def test_submit_rejects_fake_stale_truncated_and_historical_refs(self):
        cat = self.catalog
        def submit(card, traces=None):
            return cards.submit_cards(self.s, {'catalog_token': cat['token'], 'cards': [card]}, traces or self.traces)
        with self.assertRaises(ValidationError):
            cards.Card.model_validate(card_payload(self.fact_id, support=[]))
        with self.assertRaises(ValueError):
            submit(card_payload('fact-does-not-exist'))
        with self.assertRaises(ValueError):
            submit(card_payload(self.fact_id, oppose_position=999))
        with self.assertRaises(ValueError):
            submit(card_payload(self.fact_id, rag_id='rag-not-current'))
        truncated = truncated_rag_trace()
        t2 = self.traces + [truncated]
        cat2 = cards.get_catalog(self.s, t2)
        self.assertEqual(cat2['rag_evidence_ids'], [])
        with self.assertRaises(ValueError):
            cards.submit_cards(self.s, {'catalog_token': cat2['token'],
                'cards': [card_payload(self.fact_id, rag_id='rag-trunc')]}, t2)
        with self.assertRaises(ValueError):
            submit(card_payload(self.fact_id, video_frame=0))
        with self.assertRaises(ValueError):
            submit({'title': '受控', 'finding': '受控',
                'support': [{'kind': 'tool', 'name': 'unknown_tool'}], 'oppose': [], 'gaps': []})
        self.s.df.iloc[0, self.s.df.columns.get_loc('ammonia_nitrogen')] = .4
        with self.assertRaises(ValueError):
            submit(card_payload(self.fact_id))

    def test_card_schema_bounds_and_forbidden_confirmation_fields(self):
        card = card_payload(self.fact_id)
        with self.assertRaises(ValidationError):
            cards.Proposal.model_validate({'catalog_token': 't', 'cards': [card] * 6})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'title': 'x' * 121})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'finding': 'x' * 2001})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'support': [{'kind': 'fact', 'fact_id': self.fact_id}] * 9})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'gaps': ['gap'] * 9})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'gaps': ['x' * 501]})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'suggestion': {**card['suggestion'], 'probability': 0.9}})
        with self.assertRaises(ValidationError):
            cards.Card.model_validate({**card, 'confirmation_state': 'confirmed'})
        with self.assertRaises(ValidationError):
            cards.Ref.model_validate({'kind': 'fact', 'fact_id': self.fact_id, 'position': 0})
        with self.assertRaises(ValidationError):
            cards.Ref.model_validate({'kind': 'fact', 'fact_id': '   '})

    def test_video_catalog_uses_true_row_indices_and_sha256_revision(self):
        rows = [{'id': 0, 'time': '00:00', 'label': '帧零'},
                {'id': 2, 'time': '00:02', 'label': '帧二'}]
        revision = 'a' * 64
        with patch('backend.routers.video.assessment', return_value={'revision': revision, 'rows': rows}):
            traces = self.traces + [video_trace()]
            cat = cards.get_catalog(self.s, traces)
            self.assertEqual(cat['video']['revision'], revision)
            self.assertEqual(cat['video']['frame_ids'], [0, 2])
            accepted = cards.submit_cards(self.s, {'catalog_token': cat['token'],
                'cards': [card_payload(self.fact_id, video_frame=2)]}, traces)
            self.assertTrue(accepted['accepted'])
            bundle = build_bundle(self.s, traces, 'video-final')
            cards.attach_cards(self.s, bundle)
            public = cards.public_cards(self.s, bundle)
            self.assertEqual(public['status'], 'ready')
            frame = next(ref for ref in public['cards'][0]['support'] if ref['kind'] == 'video_frame')
            self.assertEqual(frame['frame_id'], 2)
            self.assertEqual(frame['video_revision'], revision)
            with self.assertRaises(ValueError):
                cards.submit_cards(self.s, {'catalog_token': cat['token'],
                    'cards': [card_payload(self.fact_id, video_frame=1)]}, traces)
        with patch('backend.routers.video.assessment', return_value={'revision': 'b' * 64, 'rows': rows}):
            self.assertEqual(cards.public_cards(self.s, bundle)['status'], 'stale')

    def test_public_cards_unavailable_invalid_stale_and_legacy(self):
        plain = build_bundle(self.s, self.traces, 'plain')
        self.assertEqual(cards.public_cards(self.s, plain)['status'], 'unavailable')
        cards.attach_cards(self.s, plain)
        self.assertNotIn('analysis_cards', plain)
        invalid = deepcopy(plain)
        invalid['analysis_cards'] = {'status': 'invalid', 'notice': '受控无效'}
        result = cards.public_cards(self.s, invalid)
        self.assertEqual(result['status'], 'invalid')
        self.assertIn('受控无效', result['notice'])
        cat = self.catalog
        cards.submit_cards(self.s, {'catalog_token': cat['token'],
            'cards': [card_payload(self.fact_id)]}, self.traces)
        bundle = build_bundle(self.s, self.traces, 'stale-final')
        cards.attach_cards(self.s, bundle)
        self.assertEqual(cards.public_cards(self.s, bundle)['status'], 'ready')
        legacy = deepcopy(bundle)
        legacy['version'] = 'report-evidence-v1'
        self.assertEqual(cards.public_cards(self.s, legacy)['status'], 'stale')
        bundle['analysis_cards']['catalog_token'] = 'tampered'
        self.assertEqual(cards.public_cards(self.s, bundle)['status'], 'invalid')
        bundle['analysis_cards']['catalog_token'] = cat['token']
        self.s.df.iloc[0, self.s.df.columns.get_loc('ammonia_nitrogen')] = .4
        self.assertEqual(cards.public_cards(self.s, bundle)['status'], 'stale')
        bad_bundle = build_bundle(self.s, self.traces, 'bad-final')
        cards.attach_cards(self.s, bad_bundle)
        self.assertEqual(cards.public_cards(self.s, bad_bundle)['status'], 'invalid')


class AnalysisCardWorkflowFixture:
    def setUp(self):
        self.client = TestClient(app)
        self.sid = self.client.get('/api/session').json()['session_id']
        self.headers = {'X-Session-Id': self.sid}
        self.s = STORE.get(self.sid)
        sample = sample_session()
        self.s.df = sample.df
        self.s.data_filename = sample.data_filename
        self.traces = traces_for(self.s)
        self.catalog = cards.get_catalog(self.s, self.traces)
        self.fact_id = next((f['id'] for f in self.catalog['facts']
            if f['id'] == 'ammonia_nitrogen.mean'), self.catalog['facts'][0]['id'])
        self.position = pick_coord_position(self.s, self.catalog)
        payload = {'catalog_token': self.catalog['token'], 'cards': [
            card_payload(self.fact_id, oppose_position=self.position),
            card_payload(self.fact_id),
        ]}
        cards.submit_cards(self.s, payload, self.traces)
        self.bundle = build_bundle(self.s, self.traces, 'workflow-final')
        cards.attach_cards(self.s, self.bundle)
        self.s.messages.append({'role': 'assistant', 'content': '受控回答', 'evidence': self.bundle})
        public = self.public()
        self.assertEqual(public['status'], 'ready')
        self.card_id = public['cards'][0]['id']
        self.card_b_id = public['cards'][1]['id']

    def public(self):
        response = self.client.get('/api/agent/evidence/' + self.bundle['id'] + '/cards', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def preview(self, card_id=None):
        card_id = card_id or self.card_id
        response = self.client.post('/api/agent/evidence/' + self.bundle['id'] + '/cards/' + card_id + '/preview',
            headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def confirm(self, preview, draft=None, request_id=None):
        body = {'preview_id': preview['preview_id'], 'request_id': request_id or str(uuid4()),
                'draft': deepcopy(draft if draft is not None else preview['draft'])}
        return self.client.post('/api/agent/evidence/' + self.bundle['id'] + '/cards/' + preview['card_id'] + '/confirm',
            headers=self.headers, json=body)

    def board(self):
        response = self.client.get('/api/investigations', headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def manual_task(self):
        board = self.board()
        body = {'scope_id': board['scope_id'], 'expected_revision': board['revision'],
                'draft': core.Draft(title='受控人工任务', hypothesis='受控假设').model_dump()}
        response = self.client.post('/api/investigations', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['task_id']

class AnalysisCardWorkflowTests(AnalysisCardWorkflowFixture, unittest.TestCase):
    def test_preview_is_read_only_and_defaults_to_open_blank_draft(self):
        before = deepcopy(self.s.investigations)
        preview = self.preview()
        self.assertEqual(preview['bundle_id'], self.bundle['id'])
        self.assertEqual(preview['card_id'], self.card_id)
        draft = preview['draft']
        self.assertEqual(draft['status'], 'open')
        self.assertEqual(draft['review'], {'outcome': '', 'reviewer': '', 'notes': ''})
        self.assertEqual(draft['retest']['observed_at'], '')
        self.assertEqual(draft['retest']['result'], '')
        self.assertEqual(draft['retest']['latitude'], 30.0)
        self.assertEqual(draft['retest']['longitude'], 120.0)
        self.assertEqual(self.s.investigations, before)
        self.assertEqual(self.board()['items'], [])
        self.assertIn('可编辑', preview['notice'])

    def test_multiple_cited_points_do_not_arbitrarily_choose_a_retest_coordinate(self):
        stored=self.bundle['analysis_cards']['cards'][0]
        other=next(position for position in self.catalog['record_positions'] if position!=self.position)
        stored['support'].append({'kind':'record','position':other})
        preview=self.preview()
        self.assertIsNone(preview['draft']['retest']['latitude'])
        self.assertIsNone(preview['draft']['retest']['longitude'])
        self.assertIn('不任意选点',preview['notice'])
        response=self.confirm(preview);self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(self.board()['items'][0]['reference'])

    def test_confirm_saves_exactly_once_and_is_idempotent(self):
        preview = self.preview()
        rid = str(uuid4())
        response = self.confirm(preview, request_id=rid)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertFalse(result['idempotent'])
        task_id = result['task_id']
        board = self.board()
        self.assertEqual(len(board['items']), 1)
        item = board['items'][0]
        self.assertEqual(item['id'], task_id)
        self.assertEqual(self.public()['cards'][0]['confirmed_task_id'], task_id)
        self.assertEqual(item['effective_status'], 'open')
        self.assertEqual(item['draft']['status'], 'open')
        self.assertEqual(item['draft']['review'], {'outcome': '', 'reviewer': '', 'notes': ''})
        self.assertEqual(item['draft']['retest']['observed_at'], '')
        self.assertEqual(item['draft']['retest']['result'], '')
        provenance = item['origin']['analysis_card']
        self.assertEqual(provenance['bundle_id'], self.bundle['id'])
        self.assertEqual(provenance['card_id'], self.card_id)
        self.assertEqual(provenance['request_id'], rid)
        self.assertEqual(len(provenance['draft_hash']), 64)
        same = self.confirm(preview, request_id=rid)
        self.assertEqual(same.status_code, 200, same.text)
        self.assertTrue(same.json()['idempotent'])
        self.assertEqual(len(self.board()['items']), 1)
        conflict = self.confirm(preview, request_id=str(uuid4()))
        self.assertEqual(conflict.status_code, 409, conflict.text)
        changed = deepcopy(preview['draft'])
        changed['title'] = '不同标题'
        reuse = self.confirm(preview, request_id=rid, draft=changed)
        self.assertEqual(reuse.status_code, 409, reuse.text)
        self.assertEqual(len(self.board()['items']), 1)

    def test_confirm_rejects_confirmation_fields_and_uses_verified_coordinates(self):
        preview = self.preview()
        for mutate in [
            lambda d: d.__setitem__('status', 'closed'),
            lambda d: d['review'].__setitem__('outcome', 'confirmed'),
            lambda d: d['retest'].__setitem__('observed_at', '2026-10-22T09:00:00+08:00'),
            lambda d: d['retest'].__setitem__('result', '受控结果'),
        ]:
            draft = deepcopy(preview['draft'])
            mutate(draft)
            response = self.confirm(preview, draft=draft)
            self.assertEqual(response.status_code, 422, response.text)
        draft = deepcopy(preview['draft'])
        draft['retest']['latitude'] = 29.0
        draft['retest']['longitude'] = 99.0
        response = self.confirm(preview, draft=draft)
        self.assertEqual(response.status_code, 200, response.text)
        item = self.board()['items'][0]
        self.assertEqual(item['draft']['retest']['latitude'], 30.0)
        self.assertEqual(item['draft']['retest']['longitude'], 120.0)
        preview_b = self.preview(self.card_b_id)
        draft_b = deepcopy(preview_b['draft'])
        draft_b['retest']['latitude'] = 29.0
        draft_b['retest']['longitude'] = 99.0
        response_b = self.confirm(preview_b, draft=draft_b)
        self.assertEqual(response_b.status_code, 200, response_b.text)
        item_b = next(candidate for candidate in self.board()['items']
            if candidate['id'] == response_b.json()['task_id'])
        self.assertIsNone(item_b['draft']['retest']['latitude'])
        self.assertIsNone(item_b['draft']['retest']['longitude'])

    def test_stale_revision_project_card_and_scope_are_rejected(self):
        preview = self.preview()
        self.manual_task()
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 409, response.text)
        preview = self.preview()
        original_project = self.s.project_id
        self.s.project_id = 'other-project'
        try:
            response = self.confirm(preview)
            self.assertEqual(response.status_code, 409, response.text)
        finally:
            self.s.project_id = original_project
        preview = self.preview()
        stored = self.bundle['analysis_cards']['cards'][0]
        original_finding = stored['finding']
        stored['finding'] = '被篡改的卡片发现'
        try:
            response = self.confirm(preview)
            self.assertEqual(response.status_code, 409, response.text)
        finally:
            stored['finding'] = original_finding
        preview = self.preview()
        fp, scope = wf.board_context(self.s)
        with patch('backend.analysis_cards_workflow.board_context', return_value=(fp, scope + '-other')):
            response = self.confirm(preview)
            self.assertEqual(response.status_code, 409, response.text)

    def test_preview_cache_bound_and_ttl_without_sleeping(self):
        clock = [1000.0]
        with patch('backend.analysis_cards_workflow.time.time', side_effect=lambda: clock[0]):
            previews = [self.preview() for _ in range(21)]
            cache = getattr(self.s, '_analysis_card_previews', {})
            self.assertLessEqual(len(cache), wf.PREVIEW_MAX)
            self.assertNotIn(previews[0]['preview_id'], cache)
            response = self.confirm(previews[0])
            self.assertEqual(response.status_code, 404, response.text)
            fresh = self.preview()
            clock[0] += wf.PREVIEW_TTL + 1
            response = self.confirm(fresh)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertNotIn(fresh['preview_id'], getattr(self.s, '_analysis_card_previews', {}))

    def test_save_failure_rolls_back_and_preview_remains_retryable(self):
        self.s.project_id = str(uuid4())
        preview = self.preview()
        before = deepcopy(self.s.investigations)
        with patch('backend.routers.investigations.repository') as repo:
            repo.return_value.write.side_effect = ProjectConflict('受控冲突')
            response = self.confirm(preview)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.s.investigations, before)
        self.assertIn(preview['preview_id'], self.s._analysis_card_previews)
        with patch('backend.routers.investigations.repository') as repo:
            repo.return_value.write.return_value = {'revision': 1}
            retry = self.confirm(preview)
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(len(self.board()['items']), 1)

    def test_project_codec_keeps_provenance_and_optional_validation_is_strict(self):
        response = self.client.post('/api/projects', headers=self.headers,
            json={'name': '受控卡片项目', 'from_current': True})
        self.assertEqual(response.status_code, 200, response.text)
        preview = self.preview()
        confirmed = self.confirm(preview)
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        task_id = confirmed.json()['task_id']
        board = self.s.investigations
        self.assertIsNone(core.validate_board(deepcopy(board)))
        raw = dump_project(self.s, '受控卡片迁移')
        loaded = load_project(raw)
        items = loaded.state['investigations']['items']
        self.assertEqual(items[0]['id'], task_id)
        provenance = items[0]['origin']['analysis_card']
        self.assertEqual(provenance['card_id'], self.card_id)
        self.assertEqual(len(provenance['draft_hash']), 64)
        invalid = deepcopy(board)
        invalid['items'][0]['origin']['analysis_card']['extra'] = 'x'
        with self.assertRaises(ValueError):
            core.validate_board(invalid)
        old = deepcopy(board)
        old['items'][0]['origin'].pop('analysis_card')
        self.assertIsNone(core.validate_board(old))
        self.assertIsNone(core.validate_board(core.empty_board()))
        for bad in [
            {'bundle_id': 'b', 'card_id': 'c', 'preview_id': 'p', 'request_id': 'not-uuid',
             'confirmed_at': utc_now(), 'draft_hash': 'a' * 64},
            {'bundle_id': 'b', 'card_id': 'c', 'preview_id': 'p', 'request_id': str(uuid4()),
             'confirmed_at': '2026-10-22T09:00:00', 'draft_hash': 'a' * 64},
            {'bundle_id': 'b', 'card_id': 'c', 'preview_id': 'p', 'request_id': str(uuid4()),
             'confirmed_at': utc_now(), 'draft_hash': 'short'},
        ]:
            with self.assertRaises(ValueError):
                wf.provenance_validate(bad)
        restored = Session('restored-card-session')
        apply_snapshot(restored, {'id': self.s.project_id, 'revision': self.s.project_revision}, loaded)
        restored_bundle = restored.messages[-1]['evidence']
        request_id = UUID(items[0]['origin']['analysis_card']['request_id'])
        payload = wf.Confirm(preview_id=preview['preview_id'], request_id=request_id,
            draft=preview['draft'])
        again = wf.confirm(restored, restored_bundle, self.card_id, payload)
        self.assertTrue(again['idempotent'])
        self.assertEqual(len(restored.investigations['items']), 1)

    def test_public_board_keeps_confirmed_task_open_and_labels_origin(self):
        preview = self.preview()
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 200, response.text)
        board = core.public_board(self.s)
        item = board['items'][0]
        self.assertEqual(item['effective_status'], 'open')
        self.assertEqual(item['draft']['status'], 'open')
        self.assertEqual(item['draft']['review'], {'outcome': '', 'reviewer': '', 'notes': ''})
        self.assertEqual(item['origin']['analysis_card']['card_id'], self.card_id)



class ReviewedIntegrationTests(unittest.TestCase):
    def run_script(self, invalid=False, cancel=False):
        from types import SimpleNamespace as NS
        from backend.agent.loop import run_agent
        session = sample_session()
        steps = [('get_data_summary', {}), ('find_black_spots', {}),
            ('get_comprehensive_summary', {}), ('generate_final_report', {'summary':'受控报告'}),
            ('get_analysis_card_catalog', {}), ('submit_analysis_cards', None), (None, None)]
        def response(**kwargs):
            name, args = steps.pop(0)
            if not name:
                return [NS(choices=[NS(delta=NS(content='受控完成，非现场结论', tool_calls=None), finish_reason='stop')])]
            if args is None:
                catalog = json.loads(kwargs['messages'][-1]['content'])['data']
                args = {'catalog_token': 'invalid' if invalid else catalog['token'],
                    'cards': [card_payload('ammonia_nitrogen.max')]}
            return [NS(choices=[NS(delta=NS(content=None,tool_calls=[NS(index=0,id=name,
                function=NS(name=name,arguments=json.dumps(args,ensure_ascii=False)))]),finish_reason='tool_calls')])]
        with patch('backend.agent.loop.resolve_api_key', return_value='controlled'), \
             patch('backend.agent.loop.OpenAI') as client, \
             patch('backend.agent.tools.generate_report', return_value='受控生成报告，非现场结论'):
            client.return_value.chat.completions.create.side_effect=response
            for frame in run_agent(session,'受控自动流程',auto=True):
                if cancel and frame.startswith('event: tool_done') and 'submit_analysis_cards' in frame:
                    session.agent_run.request_stop()
        return session

    def test_video_refs_use_real_frame_assessment_not_a_mocked_revision_shape(self):
        from test_video_quality_regressions import video
        from backend.routers.video import assessment
        session=sample_session();session.video_frames=video();session.video_filename='受控像素_非实拍.mp4'
        traces=traces_for(session,names=('get_data_summary','analyze_video'))
        cat=cards.get_catalog(session,traces)
        self.assertEqual(cat['video']['frame_ids'],[0,1,2,3])
        self.assertEqual(cat['video']['revision'],assessment(session)['revision'])
        cards.submit_cards(session,{'catalog_token':cat['token'],'cards':[card_payload('records',video_frame=0)]},traces)
        bundle=build_bundle(session,traces,'real-quality-contract');cards.attach_cards(session,bundle)
        item=cards.public_cards(session,bundle)['cards'][0]
        ref=next(ref for ref in item['support'] if ref['kind']=='video_frame')
        self.assertEqual(ref['frame_id'],0)
        self.assertEqual(ref['video_revision'],cat['video']['revision'])
        session.video_frames[0]['review']={'decision':'exclude','reviewer':'受控复核','notes':'验收变更'}
        self.assertEqual(cards.public_cards(session,bundle)['status'],'stale')

    def test_real_loop_catalog_submit_final_attach_excludes_proposal_tool_snapshots(self):
        session=self.run_script()
        self.assertEqual(session.agent_run.status,'succeeded')
        bundle=session.messages[-1]['evidence']
        self.assertEqual(cards.public_cards(session,bundle)['status'],'ready')
        self.assertTrue(all(t['name'] not in {'get_analysis_card_catalog','submit_analysis_cards'} for t in bundle['tools']))
        self.assertIsNone(session._analysis_card_proposal)
        self.assertEqual(session.investigations['items'],[])

    def test_card_submission_error_keeps_successful_core_report_and_explicit_invalid_cards(self):
        session=self.run_script(invalid=True)
        self.assertEqual(session.agent_run.status,'succeeded')
        self.assertTrue(session.analysis_done)
        self.assertEqual(cards.public_cards(session,session.messages[-1]['evidence'])['status'],'invalid')
        self.assertIsNone(session._analysis_card_proposal)

    def test_stop_after_submission_never_persists_actionable_cards(self):
        session=self.run_script(cancel=True)
        self.assertEqual(session.agent_run.status,'cancelled')
        self.assertFalse(any(m.get('evidence') for m in session.messages))
        self.assertIsNone(session._analysis_card_proposal)
        self.assertEqual(session.investigations['items'],[])

    def test_record_and_frame_guesses_outside_bounded_catalog_are_rejected(self):
        import pandas as pd
        session=sample_session();session.df=pd.concat([session.df]*50,ignore_index=True)
        session.df['__input_record_id']=range(len(session.df))
        traces=traces_for(session);cat=cards.get_catalog(session,traces)
        missing=next(i for i in range(len(session.df)-1,0,-1) if i not in cat['record_positions'])
        payload=card_payload('records',oppose_position=missing)
        with self.assertRaisesRegex(ValueError,'目录'):
            cards.submit_cards(session,{'catalog_token':cat['token'],'cards':[payload]},traces)
        with patch('backend.routers.video.assessment',return_value={'revision':'a'*64,'rows':[{'id':i} for i in range(100)]}):
            traces=traces+[video_trace()];cat=cards.get_catalog(session,traces)
            self.assertEqual(len(cat['video']['frame_ids']),80)
            with self.assertRaisesRegex(ValueError,'目录'):
                cards.submit_cards(session,{'catalog_token':cat['token'],
                    'cards':[card_payload('records',video_frame=90)]},traces)

    def test_no_current_traces_cannot_reuse_previous_report_or_staged_proposal(self):
        session=sample_session();traces=traces_for(session);cat=cards.get_catalog(session,traces)
        cards.submit_cards(session,{'catalog_token':cat['token'],'cards':[card_payload('records')]},traces)
        actual=json.loads(execute_tool('get_analysis_card_catalog',{},session)[0])['data']
        self.assertEqual(actual['facts'],[])
        self.assertEqual(actual['successful_tools'],[])
        raw,failed=execute_tool('submit_analysis_cards',{'catalog_token':cat['token'],
            'cards':[card_payload('records')]},session,evidence_traces=[])
        self.assertTrue(failed)
        self.assertIsNone(session._analysis_card_proposal)


class ReviewedProvenanceTests(AnalysisCardWorkflowFixture, unittest.TestCase):
    def test_provenance_uniqueness_and_in_memory_corruption_fail_closed(self):
        first=self.preview();self.assertEqual(self.confirm(first).status_code,200)
        duplicate=deepcopy(self.s.investigations);copied=deepcopy(duplicate['items'][0]);copied['id']=str(uuid4());duplicate['items'].append(copied)
        with self.assertRaisesRegex(ValueError,'重复'):
            core.validate_board(duplicate)
        second=self.preview(self.card_b_id)
        self.s.investigations['items'][0]['origin']['analysis_card']=None
        response=self.confirm(second)
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(len(self.s.investigations['items']),1)

    def test_new_preview_cannot_reuse_successful_request_and_no_whitespace_review(self):
        first=self.preview();rid=str(uuid4());self.assertEqual(self.confirm(first,request_id=rid).status_code,200)
        second=self.preview()
        self.assertEqual(self.confirm(second,request_id=rid).status_code,409)
        self.assertEqual(len(self.s.investigations['items']),1)
        preview=self.preview(self.card_b_id);d=deepcopy(preview['draft']);d['review']['notes']=' '
        self.assertEqual(self.confirm(preview,draft=d).status_code,422)


if __name__ == '__main__':
    unittest.main()
