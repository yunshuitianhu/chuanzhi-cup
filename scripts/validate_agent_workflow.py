"""真实DeepSeek及工具链验收；输入显式给定，隔离会话，不替换用户项目。

此脚本会实际调用已配置的模型及检索/外部工具。单元回归中的模拟协议测试不替代本脚本。
"""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def run_case(path, output, video=None, frame_interval=60):
    from fastapi.testclient import TestClient
    from backend.main import app
    from backend.session import STORE
    from backend.agent.loop import run_agent
    output.mkdir(parents=True,exist_ok=True)
    with TestClient(app) as client:
        sid = client.get('/api/session').json()['session_id']
        headers = {'X-Session-Id':sid}
        try:
            with path.open('rb') as source:
                response = client.post('/api/data/upload',headers=headers,
                    files={'file':(path.name,source,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
            response.raise_for_status()
            session = STORE.get(sid)
            if video is not None:
                with video.open('rb') as source:
                    response = client.post(f'/api/data/video?interval={frame_interval}',headers=headers,
                        files={'file':(video.name,source,'video/mp4')})
                response.raise_for_status()
            events = []
            prompt = ('请按标准流程自动分析当前数据，完成数据摘要、黑臭检测、综合摘要、治理方法检索与报告。'
                      + ('本次已上传真实视频，请完成视觉分析和交叉印证。视频时钟偏移0秒只作为未核实情景，不视为已确认同步。' if video else '本次验收未上传视频。')
                      + '没有独立测绘参考线和率定水力参数，不进行河道正演或物理反演。'
                      '严格区分仿真、来源未核验数据与真实归因，不沿用历史结论。')
            for frame in run_agent(session,prompt,auto=True):
                name = frame.splitlines()[0].removeprefix('event: ')
                payload = json.loads(frame.split('\ndata: ',1)[1])
                events.append({'event':name,'data':payload})
                if name != 'delta':
                    print(json.dumps({'file':path.name,'event':name,**payload},ensure_ascii=False),flush=True)
            (output/'events.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            (output/'conversation.json').write_text(json.dumps(session.full_messages,ensure_ascii=False,indent=2),encoding='utf-8')
            report, tool_names = None, {}
            for message in session.full_messages:
                for call in message.get('tool_calls',[]):
                    tool_names[call['id']] = call['function']['name']
                if message.get('role') == 'tool' and tool_names.get(message.get('tool_call_id')) == 'generate_final_report':
                    try:
                        outcome = json.loads(message['content'])
                        if outcome['ok']:
                            report = outcome['data']
                    except (ValueError,KeyError,TypeError):
                        pass
            if report:
                (output/'report.md').write_text(report,encoding='utf-8')
            from river_model.cruise_alignment import audit_track
            audit = audit_track(session.df)
            detection = json.loads(session.black_spots_json or '{}')
            summary = dict(input=path.name,records=len(session.df),data_identity=session.data_identity,
                status=session.analysis_status,analysis_done=session.analysis_done,
                analysis_steps=session.analysis_steps,report_saved=bool(report),
                alert_points=detection.get('黑点总数'),cluster_count=detection.get('聚类热点数'),
                video_frames=len(session.video_frames),cross_validation_available=session.cross_validation is not None,
                track_audit=audit,scope='实际LLM与原生工具；独立临时会话；未做浏览器视觉验收',
                passed=session.analysis_done and bool(report) and any(e['event']=='done' and e['data'].get('status')=='succeeded' for e in events))
            (output/'validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            return summary
        finally:
            STORE.drop(sid)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--excel',type=Path,action='append',required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--video',type=Path,help='可选真实视频；使用同一视频的多输入场景需自行核实其对应关系')
    parser.add_argument('--frame-interval',type=int,default=60)
    args = parser.parse_args()
    for path in [*args.excel, *([args.video] if args.video else [])]:
        if not path.is_file():
            parser.error(f'输入文件不存在：{path}')
    args.output_dir.mkdir(parents=True,exist_ok=True)
    os.environ['WQ_PROJECT_HOME'] = str((args.output_dir/'isolated-project-runtime').resolve())
    results = [run_case(path,args.output_dir/f'case-{index+1}',args.video,args.frame_interval) for index,path in enumerate(args.excel)]
    combined = {'cases':results,'passed':all(case['passed'] for case in results)}
    (args.output_dir/'validation.json').write_text(json.dumps(combined,ensure_ascii=False,indent=2),encoding='utf-8')
    raise SystemExit(0 if combined['passed'] else 1)
