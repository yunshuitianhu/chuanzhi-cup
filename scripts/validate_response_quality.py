"""真实模型回答风格抽查：覆盖主聊天、图片、视频帧、仿真身份和界面错误。

只使用程序生成数据和受控纹理帧；结果是阅读样例，不是自动禁词判定，
也不代表现场污染识别准确率、责任认定或全部页面已逐题验收。
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

import cv2
import numpy as np
import pandas as pd

from backend.agent.loop import run_agent
from backend.agent.progress import AgentRun
from backend.media_assets import store_media
from backend.question_context import ImageAssetContext, resolve_context, create_context
from backend.session import Session
from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key


def monitoring():
    n = 120
    values = np.full(n, 2.0)
    values[60:66] = 18.0
    frame = pd.DataFrame({
        'datetime': pd.date_range('2026-10-26 09:00', periods=n, freq='10s'),
        'latitude': 23 + np.linspace(0, .02, n), 'longitude': 113 + np.linspace(0, .02, n),
        'ammonia_nitrogen': values, 'dissolved_oxygen': np.linspace(7, 3, n),
        'turbidity': np.full(n, 12.), 'ph': np.full(n, 7.)})
    frame.attrs['data_identity'] = {'kind': 'synthetic'}
    return frame


def video_frames():
    from test_video_quality_regressions import frame, settings, texture
    frames = [frame(texture(index % 2 == 1), index * 5.) for index in range(40)]
    frames[0]['video_settings'] = settings()
    return frames


def image_bytes():
    image = np.full((240, 360, 3), 245, np.uint8)
    cv2.rectangle(image, (20, 120), (120, 220), (0, 0, 220), -1)
    cv2.circle(image, (250, 170), 50, (220, 80, 0), -1)
    cv2.putText(image, 'A7', (140, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (10, 10, 10), 3)
    ok, encoded = cv2.imencode('.png', image)
    assert ok
    return encoded.tobytes()


QUESTIONS = [
    ('image', '这张图说明什么？', True),
    ('data', '这段氨氮为什么突然高了？', True),
    ('plain', '什么是溶解氧，低了会怎样？', False),
    ('video', '第38帧有哪些问题？', True),
    ('plain', '正演和反演有什么区别？', False),
    ('data', '能确认污染从哪里来吗？', True),
    ('plain', '没有视频能分析吗？', False),
    ('data', '请只给我专业参数和原始字段。', True),
    ('data', '这份数据里哪些结论是仿真的？', True),
    ('plain', '页面上出现“工具执行出错（TimeoutError）”是什么意思？', False),
]


def run_question(kind, question, needs_data, frames):
    session = Session('quality-' + kind)
    if needs_data:
        session.df = monitoring()
        session.data_filename = 'Q19_仿真样本_非实测.csv'
    if kind == 'video':
        session.video_frames = frames
        session.video_filename = '受控纹理_非实拍.mp4'
    run = AgentRun(session.analysis_revision)
    if kind == 'image':
        meta = store_media(session, image_bytes(), '风格验收图.png')
        card = create_context(session, ImageAssetContext(kind='image_asset', asset_id=meta['asset_id'],
            asset_revision=meta['sha256'], project_id=None,
            analysis_revision=session.analysis_revision, results_revision=session.results_revision))
        run.question_context = resolve_context(session, card['id'])
    started = time.monotonic()
    events = list(run_agent(session, question, run=run))
    elapsed = round(time.monotonic() - started, 2)
    counts, tools = {}, []
    for event in events:
        if event.startswith('event: '):
            name = event.split('\n', 1)[0][len('event: '):].strip()
            counts[name] = counts.get(name, 0) + 1
            if name == 'tool':
                try:
                    payload = json.loads(event.split('data: ', 1)[1].split('\n', 1)[0])
                    tools.append(payload.get('name'))
                except (ValueError, IndexError, TypeError):
                    pass
    assistant = next((message for message in reversed(session.messages)
                      if message.get('role') == 'assistant'), {})
    return {'question': question, 'kind': kind, 'run_status': run.status, 'elapsed_s': elapsed,
            'event_counts': counts, 'tools': tools[:20],
            'answer': str(assistant.get('content') or '')[:5000],
            'vision': assistant.get('vision'), 'model': assistant.get('model')}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='docs/validation/response_quality_20261027.json')
    args = parser.parse_args()
    if not resolve_api_key('DEEPSEEK_API_KEY'):
        print('未配置 DEEPSEEK_API_KEY，未执行真实模型抽查。')
        return 2
    frames = video_frames()
    report = {'date': datetime.now(timezone.utc).isoformat(), 'model': 'deepseek-flash',
              'scope': '固定问题集的真实模型阅读样例；自动分析全链路与报告正文未逐题重跑。',
              'claim': '不据此宣称现场准确率、污染责任或所有页面已验收。',
              'tests': []}
    for kind, question, needs_data in QUESTIONS:
        try:
            result = run_question(kind, question, needs_data, frames)
        except Exception as exc:
            result = {'question': question, 'kind': kind,
                      'error': f'{type(exc).__name__}: {exc}'}
        report['tests'].append(result)
        print(question, result.get('run_status', result.get('error')), result.get('tools'), flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
