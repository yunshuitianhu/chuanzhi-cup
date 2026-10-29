"""Q18 阶段 B 真实多帧观察验收：只发送程序生成的受控纹理帧。

调用 `observe_sequence`（与 `/api/vision/observe` 共用服务层），记录模型、
耗时、逐帧观察和变化；不保存图片 base64、密钥或本机路径。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'tests') not in sys.path:
    sys.path.insert(0, str(ROOT / 'tests'))

from backend.routers.video import assessment
from backend.session import Session
from backend.vision_evidence import VisionError, VisionServiceError, observe_sequence
from test_video_quality_regressions import video
from water_quality_mcp.src.water_quality_mcp.config import resolve_api_key


def main() -> int:
    out = ROOT / 'docs' / 'validation' / 'vision_sequence_20261029.json'
    if not resolve_api_key('DEEPSEEK_API_KEY'):
        print('未配置 DEEPSEEK_API_KEY，未执行真实多帧验收。')
        return 2
    frames = video()[:2]
    session = Session(id='vision-sequence-validation', video_frames=frames,
                      video_filename='受控纹理_非实拍.mp4')
    revision = assessment(session)['revision']
    report = {
        'date': datetime.now(timezone.utc).isoformat(),
        'scope': 'service-level observe_sequence; controlled generated textures, not user media',
        'claim': '只验证多帧图像输入与结构化逐帧观察，不代表现场污染识别准确率或责任认定。',
        'frame_ids': [0, 1],
        'video_revision_prefix': revision[:16],
    }
    try:
        result = observe_sequence(session, frame_ids=[0, 1], video_revision=revision,
                                  question='按顺序比较两张图的整体明暗；只描述可见差异。',
                                  api_key=resolve_api_key('DEEPSEEK_API_KEY'))
        report.update({
            'status': 'succeeded', 'model': result.get('model'),
            'elapsed_s': result.get('elapsed_s'), 'cached': result.get('cached'),
            'static_sequence': result.get('static_sequence'),
            'frames': [{'index': frame['index'],
                        'frame_id': frame['source'].get('frame_id'),
                        'time_sec': frame['source'].get('time_sec'),
                        'quality_status': (frame.get('source_quality') or {}).get('quality_status'),
                        'observations': frame['observations']} for frame in result['frames']],
            'changes': result.get('changes'), 'not_visible': result.get('not_visible'),
            'quality_notes': result.get('quality_notes'), 'limits': result.get('limits'),
        })
        code = 0
    except (VisionError, VisionServiceError) as exc:
        report.update({'status': 'rejected', 'error': str(exc)})
        code = 1
    except Exception as exc:
        report.update({'status': 'error', 'error': type(exc).__name__})
        code = 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: report.get(k) for k in ('status', 'model', 'elapsed_s',
                                                 'static_sequence', 'changes')},
                     ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
