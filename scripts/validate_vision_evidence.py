"""Q18 阶段 A 真实视觉观察验收：只发送程序生成图，不发送用户媒体。

用法：
  python scripts/validate_vision_evidence.py

需要 DEEPSEEK_API_KEY；记录 HTTP 状态、来源哈希、模型和结构化观察，
不保存图片 base64、密钥或本机路径。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.main import app
from backend.session import STORE
from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key


def controlled_image() -> bytes:
    image = np.full((240, 360, 3), 245, np.uint8)
    cv2.rectangle(image, (20, 120), (120, 220), (0, 0, 220), -1)
    cv2.circle(image, (250, 170), 50, (220, 80, 0), -1)
    cv2.putText(image, 'A7', (140, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (10, 10, 10), 3)
    ok, encoded = cv2.imencode('.png', image)
    if not ok:
        raise RuntimeError('受控图片编码失败')
    return encoded.tobytes()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='docs/validation/vision_evidence_20261029.json')
    args = parser.parse_args()
    if not resolve_api_key('DEEPSEEK_API_KEY'):
        print('未配置 DEEPSEEK_API_KEY，未执行真实视觉观察验收。')
        return 2

    client = TestClient(app)
    session_id = client.get('/api/session').json()['session_id']
    headers = {'X-Session-Id': session_id}
    report = {
        'date': datetime.now(timezone.utc).isoformat(),
        'endpoint': 'POST /api/vision/observe',
        'input': '程序生成白底图：左侧红色方块、右侧蓝色圆形、黑色文字A7；不含用户媒体',
        'claim': '仅验证结构化视觉观察与来源绑定，不代表现场污染识别准确率或责任认定。',
    }
    try:
        upload = client.post('/api/media', headers=headers,
                             files={'file': ('controlled_shapes.png', controlled_image(), 'image/png')})
        report['upload_status'] = upload.status_code
        if upload.status_code != 200:
            report['error'] = upload.text[:500]
            return 1
        meta = upload.json()
        response = client.post('/api/vision/observe', headers=headers, json={
            'kind': 'image_asset',
            'asset_id': meta['asset_id'],
            'asset_revision': meta['sha256'],
            'question': '图中左右各是什么形状、什么颜色？图中文字是什么？只回答观察到的事实。',
        })
        report['status_code'] = response.status_code
        if response.status_code != 200:
            report['error'] = response.text[:500]
            return 1
        payload = response.json()
        report.update({
            'model': payload.get('model'),
            'elapsed_s': payload.get('elapsed_s'),
            'cached': payload.get('cached'),
            'source_kind': payload.get('source', {}).get('kind'),
            'source_sha256_prefix': str(payload.get('source', {}).get('sha256', ''))[:16],
            'question': payload.get('question'),
            'observations': payload.get('observations'),
            'not_visible': payload.get('not_visible'),
            'quality_notes': payload.get('quality_notes'),
            'limits': payload.get('limits'),
            'no_base64_in_response': 'base64' not in response.text,
        })
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: report[k] for k in ('status_code', 'model', 'elapsed_s',
                                                 'source_sha256_prefix', 'observations')},
                         ensure_ascii=False, indent=2))
        return 0
    finally:
        client.close()
        STORE.drop(session_id)


if __name__ == '__main__':
    raise SystemExit(main())
