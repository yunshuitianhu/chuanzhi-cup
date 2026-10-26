'''研判卡片的读取、预览和确认保存路由。'''
from fastapi import APIRouter, Depends, HTTPException

from ..analysis_cards import public_cards
from ..analysis_cards_workflow import Confirm, confirm, preview
from ..deps import get_exclusive_session
from ..investigations import BoardError
from ..report_evidence import bundle_status
from ..session import Session
from .agent import evidence_for

router = APIRouter(prefix='/agent', tags=['agent'])


def _message(exc):
    text = str(exc).strip()
    return text[:500] if text else '请求无法处理'


@router.get('/evidence/{bundle_id}/cards')
def cards(bundle_id: str, session: Session = Depends(get_exclusive_session)):
    bundle = evidence_for(session, bundle_id)
    try:
        return public_cards(session, bundle)
    except HTTPException:
        raise
    except (KeyError, ValueError) as exc:
        try:
            status, notice = bundle_status(session, bundle)
        except Exception:
            status, notice = 'invalid', '研判卡片当前不可用；请重新运行分析生成。'
        if status != 'current':
            return {
                'version': 'analysis-cards-v1',
                'status': 'stale' if status == 'stale' else 'unavailable',
                'notice': notice,
                'cards': [],
            }
        return {
            'version': 'analysis-cards-v1',
            'status': 'invalid',
            'notice': '研判卡片当前不可用；请重新运行分析生成。',
            'cards': [],
        }
    except Exception as exc:
        raise HTTPException(500, '研判卡片读取失败，请稍后重试') from exc


@router.post('/evidence/{bundle_id}/cards/{card_id}/preview')
def preview_card(bundle_id: str, card_id: str, session: Session = Depends(get_exclusive_session)):
    bundle = evidence_for(session, bundle_id, True)
    try:
        return preview(session, bundle, card_id)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(404, _message(exc)) from exc
    except BoardError as exc:
        raise HTTPException(409, _message(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, _message(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, '生成复测草稿失败，请稍后重试') from exc


@router.post('/evidence/{bundle_id}/cards/{card_id}/confirm')
def confirm_card(bundle_id: str, card_id: str, payload: Confirm,
                 session: Session = Depends(get_exclusive_session)):
    bundle = evidence_for(session, bundle_id, True)
    try:
        return confirm(session, bundle, card_id, payload)
    except HTTPException:
        raise
    except KeyError as exc:
        raise HTTPException(404, _message(exc)) from exc
    except BoardError as exc:
        raise HTTPException(409, _message(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, _message(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, '确认保存失败，请稍后重试；如预览失效请重新生成草稿。') from exc
