"""排查任务的人工录入、复核和版本化保存。"""
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_exclusive_session
from ..session import Session
from .. import investigations as core
from .projects import storage_errors
from ..projects import repository

router = APIRouter(prefix='/investigations', tags=['investigations'])


@router.get('')
def board(session: Session = Depends(get_exclusive_session)):
    return core.public_board(session)


def commit(session, board, task_id):
    previous = session.investigations
    session.investigations = board
    try:
        with storage_errors():
            if session.project_id:
                repository().write(session)
    except Exception:
        session.investigations = previous
        raise
    return {'board': core.public_board(session), 'task_id': task_id}


@router.post('')
def create(payload: core.Create, session: Session = Depends(get_exclusive_session)):
    try:
        board, task_id = core.create(session, payload)
    except core.BoardError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return commit(session, board, task_id)


@router.put('/{task_id}')
def update(task_id: str, payload: core.Update, session: Session = Depends(get_exclusive_session)):
    try:
        board, selected = core.update(session, task_id, payload)
    except core.BoardError as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return commit(session, board, selected)
