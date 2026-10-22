"""在无.env的交付副本中，用两个独立进程验收基础分析与项目迁移。

生成的是无地理坐标的公式演示数据，不是实测、不用于物理反演准确率评测。
不调用付费模型；requests/httpx对外传输在验收期间被禁止。
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def demo_csv(path: Path):
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream)
        writer.writerow(['时间','氨氮(mg/L)','溶解氧(mg/L)','水温(℃)'])
        for index in range(180):
            pulse=math.exp(-((index-90)/25)**2)
            writer.writerow([(datetime(2026,1,1,9)+timedelta(seconds=index*10)).isoformat(' '),
                             round(.4+5*pulse,6),0 if index==90 else round(6-5*pulse,6),20])


def fingerprint(session):
    import numpy as np
    import pandas as pd
    from backend.project_codec import MODEL_FILES
    arrays={}
    def walk(value,path):
        if isinstance(value,np.ndarray):
            arrays[path]=dict(shape=list(value.shape),dtype=str(value.dtype),
                              sha256=sha256(np.ascontiguousarray(value).tobytes()).hexdigest())
        elif is_dataclass(value):
            for field in fields(value):
                walk(getattr(value,field.name),path+'.'+field.name)
        elif isinstance(value,dict):
            for key,item in value.items():
                walk(item,path+'.'+str(key))
        elif isinstance(value,(list,tuple)):
            for index,item in enumerate(value):
                walk(item,path+f'.{index}')
    walk(session.river_state,'model')
    model_dir=Path(session.river_state.output_dir).resolve() if session.river_state else None
    artifacts={name:sha256((model_dir/name).read_bytes()).hexdigest()
               for name in MODEL_FILES if model_dir and (model_dir/name).is_file()}
    def portable_ui(value,key=''):
        if key=='输出文件':
            result=[]
            for name in value:
                path=Path(name).resolve()
                assert model_dir and path.parent==model_dir and path.name in artifacts, '输出附件未在当前模型目录物化'
                result.append(path.name)
            return result
        if isinstance(value,dict):
            return {k:portable_ui(v,k) for k,v in value.items()}
        if isinstance(value,list):
            return [portable_ui(item) for item in value]
        return value
    return dict(rows=len(session.df),columns=list(session.df.columns),
                dataframe=sha256(pd.util.hash_pandas_object(session.df,index=True).values.tobytes()).hexdigest(),
                detection=sha256((session.black_spots_json or '').encode()).hexdigest(),
                identity=session.data_identity,model_arrays=arrays,
                river_ui=portable_ui(session.river_ui),artifacts=artifacts,analysis_done=session.analysis_done)


def worker(output: Path,phase: str):
    from fastapi.testclient import TestClient
    import httpx
    from backend.web import create_app
    from backend.session import STORE
    import backend
    assert Path(backend.__file__).resolve().is_relative_to(ROOT)
    def denied(*args,**kwargs):
        raise AssertionError('交付验收禁止requests/httpx向外发送HTTP请求')
    with patch('requests.sessions.Session.request',side_effect=denied), \
         patch.object(httpx.HTTPTransport,'handle_request',side_effect=denied), \
         patch.object(httpx.AsyncHTTPTransport,'handle_async_request',side_effect=denied), \
         TestClient(create_app()) as client:
        if phase=='create':
            for route in ('/','/chat','/charts','/river','/tech'):
                assert client.get(route).status_code==200
            response=client.get('/api/config')
            assert not any(response.json()['keys'].values())
            sid=response.headers['x-session-id']; headers={'X-Session-Id':sid}
            path=output/'演示_仿真_非实测.csv'; demo_csv(path)
            with path.open('rb') as stream:
                response=client.post('/api/data/upload',headers=headers,files={'file':(path.name,stream,'text/csv')})
            response.raise_for_status(); assert response.json()['rows']==180
            session=STORE.get(sid)
            assert session.data_identity['kind']=='synthetic'
            assert (session.df['dissolved_oxygen']==0).any()
            response=client.post('/api/analysis/black-spots',headers=headers)
            response.raise_for_status(); assert session.black_spots_json
            response=client.post('/api/river/init',headers=headers,json=dict(n_cross_sections=12,auto_detect=False,
                parameters=dict(river_name='仿真理想河段（非实测）',length_m=600.,width_avg_m=12.,depth_avg_m=1.5,
                                longitudinal_slope=.0001,bed_elevation_avg_m=0.,high_water_mean_m=1.5,
                                ammonia_nh3_mgL=.4,dissolved_oxygen_mgL=6.)))
            response.raise_for_status()
            response=client.post('/api/river/hydro',headers=headers,json=dict(upstream_flow_m3s=2.,duration_h=.5,dt_s=60.))
            response.raise_for_status()
            response=client.post('/api/river/pollution',headers=headers,json=dict(chainage_m=200.,load_kg=2.,
                duration_min=5.,simulation_hours=.5,output_interval_s=60.,wq_refinement=4))
            response.raise_for_status()
            playback=client.get('/api/river/playback',headers=headers); playback.raise_for_status()
            assert playback.json()['returned_frames']>1
            assert not session.analysis_done
            blocked=client.post('/api/agent/chat',headers=headers,json={'message':'开始分析','auto':True})
            blocked.raise_for_status(); assert '未配置 DeepSeek API Key' in blocked.text
            assert not session.analysis_done
            rag=client.get('/api/rag/status',headers=headers); rag.raise_for_status()
            assert not rag.json()['available'] and rag.json()['source_documents']>0
            state=client.post('/api/projects',headers=headers,json={'name':'交付演示_仿真_非实测','from_current':True})
            state.raise_for_status(); project=state.json()['current']['id']
            save=client.post('/api/projects/save',headers=headers,json={}); save.raise_for_status()
            export=client.post(f'/api/projects/{project}/export',headers=headers,json={}); export.raise_for_status()
            (output/'演示_仿真_非实测.wqproj').write_bytes(export.content)
            record=dict(session_id=sid,project_id=project,expected=fingerprint(session),
                        playback_frames=playback.json()['returned_frames'],rag_status=rag.json(),
                        source_root=str(ROOT))
            (output/'create.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        else:
            record=json.loads((output/'create.json').read_text(encoding='utf-8'))
            sid=record['session_id']; headers={'X-Session-Id':sid}
            assert STORE.count==0,'必须在独立的新进程恢复'
            response=client.get('/api/data/status',headers=headers); response.raise_for_status()
            assert response.json()['rows']==180
            session=STORE.get(sid)
            assert fingerprint(session)==record['expected'],'保存前与进程重启后的数据/检测/模型数组不一致'
            client.get('/api/river/playback',headers=headers).raise_for_status()
            imported=client.post('/api/projects/import',headers=headers,
                files={'file':('demo.wqproj',(output/'演示_仿真_非实测.wqproj').read_bytes(),'application/zip')})
            imported.raise_for_status()
            assert imported.json()['current']['id']!=record['project_id'],'导入必须创建新ID'
            assert fingerprint(STORE.get(sid))==record['expected']
            projects=client.get('/api/projects',headers=headers).json()
            assert len(projects['projects'])==2,'导入不得覆盖原项目'
            reexport=client.post(f"/api/projects/{imported.json()['current']['id']}/export",headers=headers,json={})
            reexport.raise_for_status()
            (output/'reexport.wqproj').write_bytes(reexport.content)
            (output/'restore.json').write_text(json.dumps(dict(passed=True,rows=180,
                unchanged_model_arrays=len(record['expected']['model_arrays']),
                independent_process_restore=True,import_creates_new_id=True,projects=2,
                requests_httpx_external_requests_forbidden=True),indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--phase',choices=['create','restore'],help=argparse.SUPPRESS)
    args=parser.parse_args(); output=args.output_dir.resolve()
    if (ROOT/'.env').exists() or (ROOT/'.mcp.json').exists():
        parser.error('请在解压后的无本机配置交付副本运行；不会改动或移除现有密钥文件。')
    for key in ('DEEPSEEK_API_KEY','AMAP_KEY','CLOUD_TOKEN'):
        os.environ.pop(key,None)
    if args.phase:
        worker(output,args.phase)
        return
    output.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'WQ_PROJECT_HOME':str(output/'isolated-projects'),'PYTHONIOENCODING':'utf-8'}
    for phase in ('create','restore'):
        with (output/f'{phase}.log').open('w',encoding='utf-8') as log:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--output-dir',str(output),'--phase',phase],
                cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
    report=json.loads((output/'restore.json').read_text(encoding='utf-8'))
    report.update(scope='无密钥交付副本，两次独立进程的ASGI接口验收；已有依赖环境；非异机安装或现场准确率',
                  demo='公式示例无GPS，非实测，不用于正演或反演精度评测',
                  limitations=['未验证新机器依赖安装','RAG索引及模型缓存不随交付包提供',
                               '本验收不替代真实DeepSeek和外部查询验收'])
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    main()
