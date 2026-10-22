"""排查闭环与迁移契约。复测文字为受控测试输入，不是现场复测结果。"""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import unittest
from unittest.mock import patch
import zipfile

from fastapi.testclient import TestClient
from backend.main import app
from backend.session import Session, STORE
from backend.project_codec import dump_project, load_project, ProjectError, STATE_FIELDS
from backend.projects import ProjectConflict
from backend import investigations as core
from backend.report_evidence import bundle_status
from test_report_evidence_regressions import sample_session, make_bundle


def draft():
    return core.Draft(title='受控假设，非现场结论', hypothesis='短时输入与传感器偏差需要区分。').model_dump()


def closed_draft():
    value=draft()
    value.update(status='closed',supporting_notes='测试录入：模拟资料支持存在一次异常。',opposing_notes='测试录入：缺少校准资料，不能区分成因。')
    value['retest'].update(location='受控复测点',indicators=['ammonia','do'],plan='重复采样并记录对照点',
        observed_at='2026-10-22T09:00:00+08:00',result='受控流程测试记录，没有实施现场采样。')
    value['review'].update(reviewer='仿真验收',outcome='inconclusive',notes='仅记录测试流程，不能确认来源。')
    return value


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(app)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid}
        self.s=STORE.get(self.sid); sample=sample_session()
        self.s.df=sample.df; self.s.data_filename=sample.data_filename

    def get(self):
        response=self.client.get('/api/investigations',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def body(self,**extra):
        board=self.get()
        return {'scope_id':board['scope_id'],'expected_revision':board['revision'],**extra}

    def create(self,**extra):
        response=self.client.post('/api/investigations',headers=self.headers,json=self.body(**(extra or {'draft':draft()})))
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['task_id']

    def put(self,task_id,value,ack=False):
        return self.client.put('/api/investigations/'+task_id,headers=self.headers,
            json=self.body(draft=value,acknowledge_review=ack))

    def test_manual_create_is_session_scoped_and_not_a_responsibility_conclusion(self):
        task_id=self.create()
        board=self.get()
        self.assertFalse(board['persisted_project']);self.assertEqual(board['revision'],1)
        self.assertEqual(board['items'][0]['id'],task_id)
        self.assertEqual(board['items'][0]['effective_status'],'open')
        self.assertIn('人工录入',board['notice'])
        self.assertEqual(self.client.get('/api/investigations').json()['items'],[])

    def test_report_record_reference_is_verified_and_does_not_stale_report(self):
        b=make_bundle(self.s)
        self.create(reference={'bundle_id':b['id'],'position':2})
        item=self.get()['items'][0]
        snapshot=json.loads(item['reference']['excerpt'])
        self.assertEqual(snapshot['values']['dissolved_oxygen'],0)
        self.assertEqual(snapshot['record_number'],3)
        self.assertEqual(item['draft']['retest']['latitude'],30)
        self.assertEqual(bundle_status(self.s,b)[0],'current')

    def test_missing_stale_foreign_or_uncovered_report_record_is_rejected(self):
        b=make_bundle(self.s)
        for bundle_id,position in [('unknown',2),(b['id'],999)]:
            response=self.client.post('/api/investigations',headers=self.headers,json=self.body(reference={'bundle_id':bundle_id,'position':position}))
            self.assertEqual(response.status_code,409,response.text)
        self.s.df.iloc[0,self.s.df.columns.get_loc('ammonia_nitrogen')]=1.
        response=self.client.post('/api/investigations',headers=self.headers,json=self.body(reference={'bundle_id':b['id'],'position':2}))
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.get()['items'],[])

    def test_old_revision_and_changed_project_scope_cannot_overwrite_or_create(self):
        old=self.body(draft=draft())
        task_id=self.create()
        response=self.client.put('/api/investigations/'+task_id,headers=self.headers,json=old)
        self.assertEqual(response.status_code,409)
        old=self.body(draft=draft())
        self.s.analysis_revision+=1
        response=self.client.post('/api/investigations',headers=self.headers,json=old)
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.get()['revision'],1)

    def test_stage_requires_real_record_fields_and_explicit_review_acknowledgement(self):
        task_id=self.create()
        for state in ('planned','sampled','reviewed','closed'):
            value=draft();value['status']=state
            self.assertEqual(self.put(task_id,value).status_code,422)
        value=closed_draft()
        self.assertEqual(self.put(task_id,value).status_code,422)
        self.assertEqual(self.put(task_id,value,True).status_code,200)
        item=self.get()['items'][0]
        self.assertEqual(item['effective_status'],'closed')
        self.assertEqual(len(item['history']),1)

    def test_changes_to_context_reopen_effective_review_and_new_review_rebases_it(self):
        task_id=self.create();self.assertEqual(self.put(task_id,closed_draft(),True).status_code,200)
        self.s.target_water_class='III'
        item=self.get()['items'][0]
        self.assertEqual(item['draft']['status'],'closed')
        self.assertEqual(item['effective_status'],'needs_recheck')
        value=closed_draft();value['review']['notes']='受控测试：已按新配置重新检查，仍不确定。'
        self.assertEqual(self.put(task_id,value,True).status_code,200)
        self.assertEqual(self.get()['items'][0]['effective_status'],'closed')

    def test_analysis_reset_preserves_task_and_immutable_original_record_excerpt(self):
        b=make_bundle(self.s);self.create(reference={'bundle_id':b['id'],'position':2})
        excerpt=self.get()['items'][0]['reference']['excerpt']
        self.s.reset_analysis()
        item=self.get()['items'][0]
        self.assertEqual(item['reference_status'],'missing')
        self.assertEqual(item['reference']['excerpt'],excerpt)
        body=self.body(draft=draft(),reference={'excerpt':'伪造证据'})
        response=self.client.put('/api/investigations/'+item['id'],headers=self.headers,json=body)
        self.assertEqual(response.status_code,422)

    def test_invalid_coordinates_time_types_and_duplicate_indicators_are_rejected(self):
        for change in [{'latitude':30.},{'latitude':float('nan'),'longitude':120.},
                {'observed_at':'2026-10-22T09:00:00'},{'indicators':['ammonia','ammonia']}, {'indicators':['unverified_metric']}]:
            value=draft();value['retest'].update(change)
            with self.assertRaises(ValueError):core.Draft.model_validate(value)
        value=draft();value['title']='   '
        self.assertEqual(self.client.post('/api/investigations',headers=self.headers,json=self.body(draft=value)).status_code,422)

    def test_persisted_project_auto_saves_board_and_restores_on_reopen(self):
        response=self.client.post('/api/projects',headers=self.headers,json={'name':'受控排查测试','from_current':True})
        self.assertEqual(response.status_code,200,response.text)
        before=self.s.project_revision
        task_id=self.create()
        self.assertEqual(self.s.project_revision,before+1)
        self.assertTrue(self.get()['persisted_project'])
        from backend.projects import repository,apply_snapshot
        info,snapshot=repository().read(self.s.project_id)
        restored=Session('independent-test-process')
        apply_snapshot(restored,info,snapshot)
        self.assertEqual(restored.investigations['items'][0]['id'],task_id)

    def test_storage_failure_rolls_back_board_change(self):
        self.s.project_id='controlled-project'
        previous=deepcopy(self.s.investigations)
        with patch('backend.routers.investigations.repository') as repo:
            repo.return_value.write.side_effect=ProjectConflict('受控版本冲突')
            response=self.client.post('/api/investigations',headers=self.headers,json=self.body(draft=draft()))
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.s.investigations,previous)

    def test_manual_only_temporary_work_is_shelved_before_creating_blank_project(self):
        self.s.df=None;self.s.data_filename=''
        task_id=self.create()
        response=self.client.post('/api/projects',headers=self.headers,json={'name':'受控空项目','from_current':False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.get()['items'],[])
        from backend.projects import repository
        saved=[]
        for info in response.json()['projects']:
            _,snapshot=repository().read(info['id'])
            if any(item['id']==task_id for item in snapshot.state['investigations']['items']):saved.append(info)
        self.assertEqual(len(saved),1)
        self.assertTrue(saved[0]['name'].startswith('未命名项目'))

    def test_busy_session_blocks_reads_and_writes_without_partial_changes(self):
        payload=self.body(draft=draft())
        self.s.operation_lock.acquire()
        try:
            self.assertEqual(self.client.get('/api/investigations',headers=self.headers).status_code,409)
            self.assertEqual(self.client.post('/api/investigations',headers=self.headers,json=payload).status_code,409)
        finally:self.s.operation_lock.release()
        self.assertEqual(self.get()['revision'],0)

    def test_project_v2_round_trip_and_v1_migration_do_not_drop_tasks_silently(self):
        task_id=self.create();raw=dump_project(self.s,'受控排查迁移')
        loaded=load_project(raw)
        self.assertEqual(loaded.state['investigations']['items'][0]['id'],task_id)
        with zipfile.ZipFile(BytesIO(raw)) as archive:entries={name:archive.read(name) for name in archive.namelist()}
        manifest=json.loads(entries['manifest.json']);self.assertEqual(manifest['schema_version'],2)
        packed=json.loads(entries['state.json'])
        packed['items']=[pair for pair in packed['items'] if pair[0]!='investigations']
        entries['state.json']=json.dumps(packed,ensure_ascii=False).encode()
        manifest['contents']['state.json']={'bytes':len(entries['state.json']),'sha256':sha256(entries['state.json']).hexdigest()}
        def rebuild(version):
            manifest['schema_version']=version;entries['manifest.json']=json.dumps(manifest).encode()
            buf=BytesIO()
            with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:
                for name,data in entries.items():archive.writestr(name,data)
            return buf.getvalue()
        self.assertEqual(load_project(rebuild(1)).state['investigations'],core.empty_board())
        with self.assertRaises(ProjectError):load_project(rebuild(2))

    def test_import_rejects_malformed_or_duplicate_tasks_and_history_is_bounded(self):
        task_id=self.create()
        for n in range(22):
            value=draft();value['title']=f'受控修改{n}'
            self.assertEqual(self.put(task_id,value).status_code,200)
        self.assertEqual(len(self.get()['items'][0]['history']),20)
        for mutate in [lambda b:b['items'].append(deepcopy(b['items'][0])),
                lambda b:b['items'][0]['draft'].__setitem__('status','confirmed_polluter'),
                lambda b:b['items'][0].__setitem__('history',list(range(21)))]:
            board=deepcopy(self.s.investigations);mutate(board)
            with self.assertRaises(ValueError):core.validate_board(board)


if __name__=='__main__':unittest.main()
