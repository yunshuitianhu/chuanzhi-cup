"""项目隔离、重启恢复、包校验和故障保存回归；临时目录，不调用外部服务。"""
from dataclasses import replace
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from backend.main import app
from backend import projects as projects_module
from backend.session import Session, STORE, SessionStore
from backend.projects import repository, ProjectRepository
from backend.project_codec import dump_project, load_project, ProjectError, MAX_PACKAGE
from river_model.config import RiverConfig
from river_model.cross_sections import generate_cross_sections
from river_model.state import RiverModelState, HydroResult, WQResult


def sample_session():
    s=Session(id='sample')
    s.df=pd.DataFrame({'datetime':pd.to_datetime(['2025-08-01','2025-08-02']),
        'ammonia_nitrogen':[0.,8.], 'dissolved_oxygen':[0.,4.],
        '__observed_ammonia_nitrogen':[True,False], 'nullable':pd.array([1,None],dtype='Int64')},index=[4,9])
    s.df.attrs={'quality':{'missing':np.int64(1)},'units':{'dissolved_oxygen':'mg/L'}}
    s.black_spots=s.df.iloc[[1]].copy()
    s.black_spots_json='{"聚类数量":1}'
    s.video_frames=[{'time_sec':0.,'image_base64':'aW1hZ2U='}]
    s.messages=[{'role':'assistant','content':'测试报告'}]
    s.full_messages=[{'role':'user','content':'测试问题'}]
    s.national_station_result={'含实时监测值':False,'查询条件':{'省份':'江苏省'}}
    s.target_water_class='III'
    cfg=RiverConfig(river_name='测试河流',length_m=800.)
    cs=generate_cross_sections(3,channel_length_m=800.)
    axis=np.array([0.,.5]);x=np.array([0.,400.,800.]);a=np.ones((2,3))*60
    hydro=HydroResult(axis,x,a/30,a*.1,np.ones((2,3))*.1,a,{'run_id':'h1','tidal_amplitude_m':0})
    wq=WQResult(axis,x,ammonia=np.ones((2,3))*.1,nitrate=np.zeros((2,3)),
        dissolved_oxygen=np.ones((2,3))*8,params={'hydro_run_id':'h1','mass_balance':{'ammonia':{'residual_kg':0.}}})
    s.river_state=RiverModelState(cfg,cs,hydro,wq,{'一号情景':{'load_kg':1.}},model_initialized=True)
    s.river_ui={'form':{'river_name':'测试河流','length_m':800.},'pollution':{'峰值浓度_mgL':.1}}
    return s


def rewrite_package(raw,change):
    with zipfile.ZipFile(BytesIO(raw)) as src:
        entries={name:src.read(name) for name in src.namelist()}
    change(entries)
    out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as dst:
        for name,value in entries.items():dst.writestr(name,value)
    return out.getvalue()


class CodecTests(unittest.TestCase):
    def test_roundtrip_preserves_frames_masks_dtypes_arrays_and_metadata(self):
        s=sample_session(); restored=load_project(dump_project(s,'河流项目'))
        pd.testing.assert_frame_equal(s.df,restored.state['df'])
        pd.testing.assert_frame_equal(s.black_spots,restored.state['black_spots'])
        self.assertEqual(s.df.attrs,restored.state['df'].attrs)
        self.assertEqual(s.video_frames,restored.state['video_frames'])
        self.assertEqual(s.full_messages,restored.state['full_messages'])
        model=restored.state['river_state']
        np.testing.assert_array_equal(model.wq_result.ammonia,s.river_state.wq_result.ammonia)
        np.testing.assert_array_equal(model.cross_sections[0].stations,s.river_state.cross_sections[0].stations)
        self.assertTrue(model.has_wq());self.assertEqual(model.config.river_name,'测试河流')
        self.assertEqual(model.output_dir,'')

    def test_timezone_and_object_cells_roundtrip(self):
        s=Session(id='x');s.df=pd.DataFrame({'datetime':pd.date_range('2026-10-18',periods=2,tz='Asia/Shanghai'),
            'object':[{'unit':'mg/L'},None],'float':[np.nan,0.]})
        restored=load_project(dump_project(s,'时区项目'))
        pd.testing.assert_frame_equal(s.df,restored.state['df'])

    def test_configured_secrets_are_redacted_without_mutating_session(self):
        s=sample_session();secret='example-private-key-123456'
        s.messages[0]['content']=secret
        s.river_ui['api_key']=secret
        with patch('water_quality_mcp.src.water_quality_mcp.config.resolve_api_key',return_value=secret):
            raw=dump_project(s,'项目')
        restored=load_project(raw)
        self.assertNotIn(secret,str(restored.state))
        self.assertEqual(s.messages[0]['content'],secret)
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            self.assertNotIn('.env',archive.namelist())
            self.assertFalse(any(name.endswith(('.pkl','.pickle')) for name in archive.namelist()))

    def test_rejects_corruption_unknown_versions_and_unlisted_files(self):
        raw=dump_project(sample_session(),'项目')
        def version(entries):
            manifest=json.loads(entries['manifest.json']);manifest['schema_version']=999
            entries['manifest.json']=json.dumps(manifest).encode()
        for mutate in (lambda d:d.update({'state.json':b'changed'}),version,
                       lambda d:d.update({'../escape':b'bad'})):
            with self.assertRaises(ProjectError):load_project(rewrite_package(raw,mutate))
        with self.assertRaises(ProjectError):load_project(b'not a project')

    def test_rejects_invalid_model_array_shape_before_restore(self):
        s=sample_session();s.river_state.wq_result.ammonia=np.ones((1,1))
        with self.assertRaisesRegex(ProjectError,'数组尺寸'):
            load_project(dump_project(s,'坏数组'))

    def test_rejects_object_numpy_arrays_in_untrusted_package(self):
        raw=dump_project(sample_session(),'项目')
        def mutate(entries):
            key=next(k for k in entries if k.endswith('.npy'))
            buffer=BytesIO();np.save(buffer,np.array([{'unsafe':True}],dtype=object))
            value=buffer.getvalue();entries[key]=value
            m=json.loads(entries['manifest.json']);m['contents'][key]={'bytes':len(value),'sha256':sha256(value).hexdigest()}
            entries['manifest.json']=json.dumps(m).encode()
        with self.assertRaises(ProjectError):load_project(rewrite_package(raw,mutate))

    def test_rejects_invalid_ui_and_model_metadata_before_activation(self):
        for key in ('ui','params'):
            s=sample_session()
            if key=='ui':s.analysis_steps={'bad':np.ones(2)}
            else:s.river_state.hydro_result.params=['not a mapping']
            with self.assertRaises(ProjectError):load_project(dump_project(s,'无效元数据'))

    def test_rejects_nonstandard_json_constants(self):
        raw=dump_project(sample_session(),'项目')
        def mutate(entries):
            manifest=json.loads(entries['manifest.json']);manifest['runtime']['invalid']=float('nan')
            entries['manifest.json']=json.dumps(manifest).encode()
        with self.assertRaisesRegex(ProjectError,'非标准数值'):
            load_project(rewrite_package(raw,mutate))


class ProjectApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'WQ_PROJECT_HOME':str(Path(self.tmp.name)/'home')})
        self.env.start()
        self.client=TestClient(app)
        self.sid=self.client.get('/api/session').json()['session_id']
        self.headers={'X-Session-Id':self.sid}
        self.ids=[self.sid]
        s=sample_session();s.id=self.sid
        STORE._sessions[self.sid]=s
        self.session=s

    def tearDown(self):
        self.client.close()
        for sid in self.ids:STORE.drop(sid)
        self.env.stop();self.tmp.cleanup()

    def post(self,path,payload=None,headers=None):
        r=self.client.post('/api/projects'+path,headers=headers or self.headers,json=payload or {})
        self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def create(self,name='河流A',from_current=True):
        return self.post('',{'name':name,'from_current':from_current})['current']['id']

    def test_two_projects_switch_without_sharing_data_or_configuration(self):
        a=self.create()
        self.session.df.loc[4,'ammonia_nitrogen']=12.
        b=self.create('河流B',False)
        self.assertIsNone(self.session.df);self.assertIsNone(self.session.river_state)
        self.session.df=pd.DataFrame({'datetime':pd.to_datetime(['2025-09-01']),
            'ammonia_nitrogen':[.1],'dissolved_oxygen':[8.]})
        self.session.target_water_class='V'
        self.post(f'/{a}/open')
        self.assertEqual(self.session.df.loc[4,'ammonia_nitrogen'],12.)
        self.assertEqual(self.session.target_water_class,'III')
        self.assertEqual(self.session.river_state.config.river_name,'测试河流')
        self.post(f'/{b}/open')
        self.assertEqual(len(self.session.df),1);self.assertEqual(self.session.target_water_class,'V')

    def test_saved_project_restores_in_fresh_store_and_new_python_process(self):
        pid=self.create();self.post('/save',{'river_form':{'length_m':800.}})
        restored=SessionStore().get(self.sid)
        self.assertEqual(restored.project_id,pid)
        pd.testing.assert_frame_equal(restored.df,self.session.df)
        script='from backend.session import STORE; import json; s=STORE.get('+repr(self.sid)+'); print(json.dumps({"id":s.project_id,"rows":len(s.df),"has_wq":s.river_state.has_wq()}))'
        result=subprocess.run([sys.executable,'-c',script],cwd=ROOT,env=os.environ.copy(),capture_output=True,text=True,encoding='utf-8',timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        value=json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(value,{'id':pid,'rows':2,'has_wq':True})

    def test_export_import_into_different_root_and_keep_original(self):
        original=self.create()
        response=self.client.post(f'/api/projects/{original}/export',headers=self.headers,json={})
        self.assertEqual(response.status_code,200,response.text[:100])
        raw=response.content
        newhome=Path(self.tmp.name)/'other-machine'
        with patch.dict(os.environ,{'WQ_PROJECT_HOME':str(newhome)}):
            STORE.drop(self.sid)
            sid=self.client.get('/api/session').json()['session_id'];self.ids.append(sid)
            result=self.client.post('/api/projects/import',headers={'X-Session-Id':sid},files={'file':('portable.wqproj',raw)})
            self.assertEqual(result.status_code,200,result.text)
            self.assertNotEqual(result.json()['current']['id'],original)
            pd.testing.assert_frame_equal(STORE.get(sid).df,self.session.df)
            self.assertTrue(STORE.get(sid).river_state.has_wq())
            self.assertTrue((newhome/'projects.sqlite3').exists())
        self.assertEqual(repository().info(original)['id'],original)

    def test_corrupt_import_and_unknown_project_preserve_current_state(self):
        pid=self.create();before=repository().info(pid)
        response=self.client.post('/api/projects/import',headers=self.headers,files={'file':('bad.wqproj',b'bad')})
        self.assertEqual(response.status_code,400)
        self.assertEqual(self.session.project_id,pid)
        self.assertEqual(repository().info(pid),before)
        response=self.client.post('/api/projects/not-a-project/open',headers=self.headers,json={})
        self.assertEqual(response.status_code,404)
        self.assertEqual(self.session.project_id,pid)

    def test_conflicting_writes_do_not_overwrite_newer_project(self):
        pid=self.create()
        other=self.client.get('/api/session').json()['session_id'];self.ids.append(other)
        headers={'X-Session-Id':other}
        self.post(f'/{pid}/open',headers=headers)
        STORE.get(other).df.loc[4,'ammonia_nitrogen']=99.
        self.post('/save')
        response=self.client.post('/api/projects/save',headers=headers,json={})
        self.assertEqual(response.status_code,409)
        self.assertNotEqual(repository().read(pid)[1].state['df'].loc[4,'ammonia_nitrogen'],99.)
        copy=self.post('',{'name':'冲突副本','from_current':True},headers=headers)['current']['id']
        self.assertNotEqual(copy,pid)
        self.assertEqual(repository().read(copy)[1].state['df'].loc[4,'ammonia_nitrogen'],99.)

    def test_disk_failure_preserves_previous_snapshot_and_memory(self):
        pid=self.create();before=repository().info(pid)
        self.session.df.loc[4,'ammonia_nitrogen']=42.
        with patch('backend.projects.os.replace',side_effect=OSError('disk failed')):
            response=self.client.post('/api/projects/save',headers=self.headers,json={})
        self.assertEqual(response.status_code,503)
        self.assertEqual(repository().info(pid),before)
        self.assertEqual(self.session.df.loc[4,'ammonia_nitrogen'],42.)

    def test_blank_project_publish_failure_keeps_original_binding(self):
        pid=self.create()
        with patch('backend.projects.prepare_snapshot',side_effect=OSError('controlled materialize failure')):
            response=self.client.post('/api/projects',headers=self.headers,
                json={'name':'新空项目','from_current':False})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.session.project_id,pid)
        self.assertEqual(repository()._binding(self.sid),pid)
        self.assertEqual(len(repository().list()),1)
        restored=SessionStore().get(self.sid)
        self.assertEqual(restored.project_id,pid)
        pd.testing.assert_frame_equal(restored.df,self.session.df)

    def test_import_publish_failure_keeps_original_binding(self):
        pid=self.create()
        exported=self.client.post(f'/api/projects/{pid}/export',headers=self.headers,json={})
        self.assertEqual(exported.status_code,200)
        real_prepare=projects_module.prepare_snapshot
        calls={'n':0}
        def fail_second(snapshot):
            calls['n']+=1
            if calls['n']==2:
                raise OSError('controlled materialize failure')
            return real_prepare(snapshot)
        with patch('backend.projects.prepare_snapshot',side_effect=fail_second):
            response=self.client.post('/api/projects/import',headers=self.headers,
                files={'file':('portable.wqproj',exported.content)})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.session.project_id,pid)
        self.assertEqual(repository()._binding(self.sid),pid)
        self.assertEqual(len(repository().list()),1)
        self.assertEqual(SessionStore().get(self.sid).project_id,pid)

    def test_open_publish_failure_restores_original_binding(self):
        a=self.create('河流A')
        b=self.create('河流B',False)
        self.assertEqual(self.session.project_id,b)
        with patch('backend.projects.publish_snapshot',side_effect=OSError('controlled publish failure')):
            response=self.client.post(f'/api/projects/{a}/open',headers=self.headers,json={})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.session.project_id,b)
        self.assertEqual(repository()._binding(self.sid),b)
        self.assertEqual(len(repository().list()),2)
        self.assertEqual(SessionStore().get(self.sid).project_id,b)

    def test_candidate_cleanup_only_removes_unbound_new_project(self):
        pid=self.create()
        self.assertFalse(repository().discard_candidate(pid))
        self.assertEqual(repository()._binding(self.sid),pid)
        self.assertEqual(len(repository().list()),1)
        candidate=repository().write(Session(id='candidate'),'候选项目',new=True,bind_session=False)
        self.assertTrue(repository().discard_candidate(candidate['id']))
        self.assertEqual([project['id'] for project in repository().list()],[pid])

    def test_busy_session_rejects_project_save_switch_and_import(self):
        pid=self.create()
        with self.session.operation_lock:
            for path in ('/save',f'/{pid}/open',f'/{pid}/export'):
                self.assertEqual(self.client.post('/api/projects'+path,headers=self.headers,json={}).status_code,409)
            self.assertEqual(self.client.post('/api/projects/import',headers=self.headers,files={'file':('x',b'x')}).status_code,409)

    def test_empty_names_and_invalid_forms_do_not_replace_project(self):
        pid=self.create()
        for payload in ({'name':'  ','from_current':False},{'name':'新项目','river_form':{'api_key':'bad'}}):
            self.assertEqual(self.client.post('/api/projects',headers=self.headers,json=payload).status_code,400)
            self.assertEqual(self.session.project_id,pid)

    def test_unsaved_draft_is_shelved_before_opening_another_project(self):
        pid=self.create();other=self.client.get('/api/session').json()['session_id'];self.ids.append(other)
        STORE.get(other).messages=[{'role':'user','content':'未保存的调查'}]
        self.post(f'/{pid}/open',headers={'X-Session-Id':other})
        projects=repository().list()
        self.assertEqual(len(projects),2)
        saved=next(p for p in projects if p['id']!=pid)
        self.assertEqual(repository().read(saved['id'])[1].state['messages'][0]['content'],'未保存的调查')

    def test_plot_artifacts_are_isolated_and_relocated_on_import(self):
        model=self.session.get_river_state();directory=Path(model.output_dir)
        path=directory/'river_hydro_profile.png';path.write_bytes(b'png-example')
        self.session.river_ui['hydro']={'输出文件':[str(path)]}
        pid=self.create();_,snapshot=repository().read(pid)
        self.assertEqual(snapshot.artifacts['river_hydro_profile.png'],b'png-example')
        restored=SessionStore().get(self.sid)
        newpath=Path(restored.river_ui['hydro']['输出文件'][0])
        self.assertNotEqual(newpath,path);self.assertEqual(newpath.read_bytes(),b'png-example')
        self.assertNotEqual(restored.river_state.output_dir,model.output_dir)

    def test_corrupt_saved_project_can_be_recovered_from_imported_backup(self):
        pid=self.create()
        info,backup=repository().raw(pid)
        broken=repository().root/info['snapshot']
        broken.write_bytes(b'broken snapshot')
        STORE.drop(self.sid)
        response=self.client.get('/api/projects',headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['restore_error'])
        result=self.client.post('/api/projects/import',headers=self.headers,
            files={'file':('backup.wqproj',backup)})
        self.assertEqual(result.status_code,200,result.text)
        self.assertNotEqual(result.json()['current']['id'],pid)
        self.assertEqual(len(STORE.get(self.sid).df),2)
        self.assertEqual(broken.read_bytes(),b'broken snapshot')


if __name__=='__main__':unittest.main()
