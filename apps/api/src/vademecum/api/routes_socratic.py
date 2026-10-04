"""The Socratic tutor (ADR 0025): start, answer, record, finish.

On the Mac's own model connection ``/answer`` runs the turn here. In a chat host
the assistant is the tutor: ``/material`` hands it the page and the rules,
``/turn`` records each exchange and ``/finish`` the assessment. The learner's
answers are stored with the session and never logged.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from ..model import socratic as service
from ..storage import socratic as store
from ..storage.sources import ConflictError
from .deps import get_connection, get_model_mode
from .routes_sources import CLAUDE_DESTINATION, CODEX_DESTINATION
from .schemas import LearnerAnswer, RecordId, Strict

router = APIRouter(prefix="/socratic", tags=["socratic"])

DISCLOSURE = (
    "Each answer you give sends the page the session is about, the abstracts reviewed for it, related "
    "pages and case-series points already on this Mac, the dialogue so far and your answer, once per "
    "exchange, to write the next question. The model also draws on its own knowledge beyond the page "
    "and says when it does. In codex mode: " + CODEX_DESTINATION + " In claude mode: " + CLAUDE_DESTINATION
    + " In a chat host the assistant you are talking to is the tutor, with its own web search."
)


class StartIn(Strict):
    entry_id: RecordId | None = None


class AnswerIn(Strict):
    answer: LearnerAnswer


class TurnIn(Strict):
    question: LearnerAnswer
    answer: LearnerAnswer
    probe: str = ""


class FinishIn(Strict):
    assessment: dict[str, Any]


def _mode_note(mode: str) -> str:
    return "" if mode in ("codex", "claude") else service.HOST_MODE


@router.get("")
def overview(connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict[str, Any]:
    current = store.open_session(connection)
    return {
        "open": None if current is None else current.as_dict(),
        "recent": [s.as_dict() for s in store.list_sessions(connection, limit=10) if s.status != "open"],
        "mode": mode,
        "can_answer_here": mode in ("codex", "claude"),
        "note": _mode_note(mode),
        "disclosure": DISCLOSURE,
    }


@router.post("", status_code=201)
def start(payload: StartIn, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict[str, Any]:
    current = store.open_session(connection)
    if current is not None:
        store.abandon(connection, current.id)
    started = service.start(connection, entry_id=payload.entry_id, mode=mode)
    if started["session"] is None:
        raise ConflictError("no_page", started["note"])
    return {**started, "note": _mode_note(mode), "disclosure": DISCLOSURE}


@router.get("/{session_id}")
def read(session_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return {"session": store.get_session(connection, session_id).as_dict()}


@router.get("/{session_id}/material")
def material(session_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """What a chat host's assistant needs to be the tutor."""
    return service.material(connection, store.get_session(connection, session_id))


@router.post("/{session_id}/answer")
async def answer(
    session_id: str,
    payload: AnswerIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    """Record the learner's answer and have the Mac's own connection ask the next question."""
    store.get_session(connection, session_id)
    if mode not in ("codex", "claude"):
        raise ConflictError("needs_model", service.HOST_MODE)
    workspace = request.state.workspace
    return await service.answer(workspace.database_path, session_id, payload.answer, workspace.turn_factory)


@router.post("/{session_id}/turn")
def record_turn(session_id: str, payload: TurnIn, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """A chat host's assistant records one exchange: its question and the learner's answer."""
    try:
        store.append(connection, session_id, role="tutor", text=payload.question, probe=payload.probe)
        session = store.append(connection, session_id, role="learner", text=payload.answer)
    except ValueError:
        raise ConflictError("closed", "This session has ended.") from None
    return {"session": session.as_dict(), "remaining": max(0, store.MAX_EXCHANGES - session.exchanges)}


@router.post("/{session_id}/finish")
def finish(session_id: str, payload: FinishIn, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    try:
        session = store.finish(connection, session_id, payload.assessment)
    except ValueError:
        raise ConflictError("closed", "This session has ended.") from None
    filed = service.file_gaps(connection, session)
    return {"session": session.as_dict(), "gaps_filed": filed}


@router.post("/{session_id}/abandon")
def abandon(session_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return {"session": store.abandon(connection, session_id).as_dict()}
