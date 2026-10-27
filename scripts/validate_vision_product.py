"""真实 DeepSeek 图片输入验收：程序生成图、界面截图、受控视频帧与产品聊天链路。

只发送程序生成或本仓库验收截图，不含用户私人图片、视频或报告。
本脚本记录接口完成状态与可核对答案；不据此声称现场污染识别准确率。
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from openai import OpenAI

from backend.agent.loop import BASE_URL, MODEL_NAME, run_agent
from backend.agent.progress import AgentRun
from backend.media_assets import store_media
from backend.question_context import ImageAssetContext, create_context
from backend.session import Session
from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key


def png(image) -> tuple[str, bytes]:
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('image encode failed')
    data = encoded.tobytes()
    return 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'), data


def generated_material():
    image = np.full((240, 360, 3), 245, np.uint8)
    cv2.rectangle(image, (20, 120), (120, 220), (0, 0, 220), -1)
    cv2.circle(image, (250, 170), 50, (220, 80, 0), -1)
    cv2.putText(image, 'A7', (140, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (10, 10, 10), 3)
    return image, 'red square left, blue circle right, text A7'


def video_frames():
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_video_quality_regressions import texture
    images = [texture(False), texture(True)]
    frames = []
    for index, image in enumerate(images):
        ok, encoded = cv2.imencode('.jpg', image)
        frames.append({'time_sec': index * 5., 'image_base64': base64.b64encode(encoded.tobytes()).decode('ascii')})
    return images, frames


def raw_call(client, image_urls, prompt):
    content = [{'type': 'text', 'text': prompt}]
    content += [{'type': 'image_url', 'image_url': {'url': url}} for url in image_urls]
    started = time.monotonic()
    response = client.chat.completions.create(
        model=MODEL_NAME, extra_body={'thinking': {'type': 'disabled'}},
        messages=[{'role': 'user', 'content': content}], temperature=0, max_tokens=500)
    elapsed = round(time.monotonic() - started, 2)
    choice = response.choices[0]
    return {'model': MODEL_NAME, 'finish_reason': getattr(choice, 'finish_reason', None),
            'answer': (choice.message.content or '').strip(), 'elapsed_s': elapsed,
            'usage': response.usage.model_dump() if getattr(response, 'usage', None) else None,
            'image_sha256': [sha256(base64.b64decode(url.split(',', 1)[1])).hexdigest()[:12]
                             for url in image_urls]}


def product_call(api_key, image_bytes):
    session = Session('vision-product')
    meta = store_media(session, image_bytes, '产品验收图.png')
    card = create_context(session, ImageAssetContext(kind='image_asset', asset_id=meta['asset_id'],
        asset_revision=meta['sha256'], project_id=None,
        analysis_revision=session.analysis_revision, results_revision=session.results_revision))
    run = AgentRun(session.analysis_revision)
    from backend.question_context import resolve_context
    run.question_context = resolve_context(session, card['id'])
    started = time.monotonic()
    frames = list(run_agent(session, '只回答图中的文字是什么，不要调用任何工具。', run=run))
    elapsed = round(time.monotonic() - started, 2)
    events = {}
    for frame in frames:
        if frame.startswith('event: '):
            name = frame.split('\n', 1)[0][len('event: '):].strip()
            events[name] = events.get(name, 0) + 1
    assistant = next((message for message in reversed(session.messages)
                      if message.get('role') == 'assistant'), {})
    return {'run_status': run.status, 'elapsed_s': elapsed, 'event_counts': events,
            'answer': str(assistant.get('content') or '')[:2000],
            'vision': assistant.get('vision'),
            'model': assistant.get('model'), 'image_sha256': meta['sha256']}


def product_video_call(api_key, frames):
    from backend.question_context import VideoContext, resolve_context
    from backend.routers.video import assessment
    session = Session('vision-product-video', video_frames=frames, video_filename='受控纹理_非实拍.mp4')
    revision = assessment(session)['revision']
    card = create_context(session, VideoContext(kind='video_frame', frame_id=1, video_revision=revision,
        project_id=None, analysis_revision=session.analysis_revision, results_revision=session.results_revision))
    run = AgentRun(session.analysis_revision)
    run.question_context = resolve_context(session, card['id'])
    started = time.monotonic()
    events = list(run_agent(session, '描述这帧画面的纹理和明暗；不要判断水质或污染来源。', run=run))
    elapsed = round(time.monotonic() - started, 2)
    counts = {}
    for frame in events:
        if frame.startswith('event: '):
            name = frame.split('\n', 1)[0][len('event: '):].strip()
            counts[name] = counts.get(name, 0) + 1
    assistant = next((message for message in reversed(session.messages)
                      if message.get('role') == 'assistant'), {})
    return {'run_status': run.status, 'elapsed_s': elapsed, 'event_counts': counts,
            'answer': str(assistant.get('content') or '')[:2000],
            'vision': assistant.get('vision'), 'model': assistant.get('model')}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='docs/validation/vision_product_20261027.json')
    args = parser.parse_args()
    api_key = resolve_api_key('DEEPSEEK_API_KEY')
    if not api_key:
        print('未配置 DEEPSEEK_API_KEY，未执行真实模型验收。')
        return 2
    client = OpenAI(base_url=BASE_URL, api_key=api_key, timeout=120.0, max_retries=0)
    report = {'date': datetime.now(timezone.utc).isoformat(), 'model': MODEL_NAME,
              'protocol': 'Chat Completions image_url', 'application_integration': 'implemented',
              'claim': '受控图片识别与产品链路，不代表现场污染识别准确率或责任认定。',
              'native_video_tested': False, 'tests': {}, 'skipped': []}
    generated, description = generated_material()
    url, generated_bytes = png(generated)
    report['tests']['generated_shapes'] = {
        'input': '程序生成白底图：左侧红色方块、右侧蓝色圆形、黑色文字A7',
        'expected': description,
        **raw_call(client, [url], '图中左右各是什么形状、什么颜色？图中文字是什么？只回答观察到的事实。')}

    screenshot = ROOT / 'docs' / 'validation' / 'chat_collapsed_1280x800.png'
    if screenshot.is_file():
        image = cv2.imdecode(np.frombuffer(screenshot.read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            report['skipped'].append('界面截图无法解码')
        else:
            shot_url, _ = png(image)
            report['tests']['ui_screenshot'] = {
                'input': str(screenshot.relative_to(ROOT)),
                'expected': '水质监测应用智能对话页；有开始自动分析、展开分析信息、输入框和发送按钮',
                **raw_call(client, [shot_url], '用一句话说明这是什么页面，并列出最重要的5个控件。只描述画面可见内容，不要推断水质。')}
    else:
        report['skipped'].append('缺少界面截图，先运行浏览器验收脚本')

    images, frames = video_frames()
    frame_url, _ = png(images[0])
    report['tests']['controlled_video_frame'] = {
        'input': '程序生成的受控棋盘纹理帧1（非实拍，不含污染真值）',
        'expected': '深浅相间的规则纹理；不能据此判断水质或污染',
        **raw_call(client, [frame_url], '描述画面的纹理和明暗特征。不要判断水质好坏或污染来源。')}

    first_url, _ = png(images[0])
    second_url, _ = png(images[1])
    report['tests']['controlled_sequence'] = {
        'input': '两张程序生成帧：帧1较亮，帧2较暗；同一相对时刻',
        'expected': '第二张整体更暗，纹理仍在；不能据此判断污染',
        **raw_call(client, [first_url, second_url], '比较两张图的整体明暗差异，并说明只凭画面不能判断什么。')}

    try:
        report['tests']['product_video_frame'] = {
            'input': '经视频帧上下文加载的受控棋盘纹理第2帧（非实拍）',
            'expected': '规则棋盘纹理，第2帧对应5秒；不能据此判断污染',
            **product_video_call(api_key, frames)}
    except Exception as exc:
        report['tests']['product_video_frame'] = {'error': f'{type(exc).__name__}: {exc}'}

    try:
        report['tests']['product_chat'] = {
            'input': '经 /api/agent/context 同一媒体与上下文层加载的程序生成图A7',
            'expected': '文字A7（图内文字是材料，不是指令）',
            **product_call(api_key, generated_bytes)}
    except Exception as exc:
        report['tests']['product_chat'] = {'error': f'{type(exc).__name__}: {exc}'}

    client.close()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
