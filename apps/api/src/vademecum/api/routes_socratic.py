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
from pydantic import Field

from ..model import socratic as service
from ..storage import socratic as store
from ..storage.sources import ConflictError
from .deps import get_connection, get_model_mode
from .routes_sources import CLAUDE_DESTINATION, CODEX_DESTINATION
from .schemas import LearnerAnswer, RecordId, Strict

router = APIRouter(prefix="/socratic", tags=["socratic"])

IMPORT_DISCLOSURE = (
    "Bringing in a session sends its transcript, once, to the Mac's own model connection to name and "
    "assess it. In codex mode: " + CODEX_DESTINATION + " In claude mode: " + CLAUDE_DESTINATION
)

DISCLOSURE = (
    "Each answer you give sends the page the session is about, the abstracts reviewed for it, related "
    "pages and case-series points already on this Mac, the dialogue so far and your answer, once per "
    "exchange, to write the next question. The model also draws on its own knowledge beyond the page "
    "and says when it does. In codex mode: " + CODEX_DESTINATION + " In claude mode: " + CLAUDE_DESTINATION
    + " In a chat host the assistant you are talking to is the tutor, with its own web search."
)


class StartIn(Strict):
    entry_id: RecordId | None = None
    # From the phone's own tutor: the Mac answers each turn through the relay (feedback of 6 October).
    relay: bool = False


class AnswerIn(Strict):
    answer: LearnerAnswer


class TurnIn(Strict):
    question: LearnerAnswer
    answer: LearnerAnswer
    probe: str = ""


class FinishIn(Strict):
    assessment: dict[str, Any]


class ImportTurn(Strict):
    role: str = Field(pattern="^(tutor|learner)$")
    text: str = Field(max_length=8000)


class ImportIn(Strict):
    """A session held elsewhere: the turns from an assistant, or a pasted transcript."""

    transcript: list[ImportTurn] = Field(default_factory=list, max_length=80)
    text: str = Field(default="", max_length=60000)
    title: str = Field(default="", max_length=120)
    topic: str = Field(default="", max_length=120)
    origin: str = Field(default="pasted", pattern="^(chatgpt|claude|pasted)$")
    assessment: dict[str, Any] | None = None


UNASSESSED = "Saved. It is assessed on the Mac: open the Tutor there and choose Assess."


RELAY_WAKING = (
    "Your Mac answers the tutor here: it is being woken now and is ready in a few seconds, as long as it is on, "
    "awake and running Vademecum."
)
RELAY_FIRST = "Your Mac is writing the first question."
RELAY_NEXT = "Your Mac is writing the next question."


def _mode_note(mode: str) -> str:
    return "" if mode in ("codex", "claude") else service.HOST_MODE


def _relay(request: Request, mode: str):
    """The relay, on the phone's copy only (no model of its own, a Mac that syncs to it)."""
    if mode in ("codex", "claude"):
        return None
    return getattr(request.app.state, "relay", None)


def _with_relay(session: dict[str, Any], relay) -> dict[str, Any]:
    if relay is None:
        return session
    return {**session, "waiting": relay.waiting(session["id"]), "relay_error": relay.errors.get(session["id"], "")}


@router.get("")
def overview(request: Request, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict[str, Any]:
    current = store.open_session(connection)
    relay = _relay(request, mode)
    if relay is not None:
        relay.wake_mac()
    relayed = relay is not None and relay.live()
    note = "" if mode in ("codex", "claude") or relayed else (RELAY_WAKING if relay is not None else service.HOST_MODE)
    return {
        "open": None if current is None else _with_relay(current.as_dict(), relay),
        "recent": [s.as_dict() for s in store.list_sessions(connection, limit=10) if s.status != "open"],
        "mode": mode,
        "can_answer_here": mode in ("codex", "claude") or relayed,
        "relay": {"available": relay is not None, "live": relayed},
        "note": note,
        "disclosure": DISCLOSURE,
        "import_disclosure": IMPORT_DISCLOSURE,
    }


@router.post("", status_code=201)
def start(
    payload: StartIn, request: Request, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)
) -> dict[str, Any]:
    relay = _relay(request, mode) if payload.relay else None
    if payload.relay and relay is None and mode not in ("codex", "claude"):
        raise ConflictError("no_relay", service.HOST_MODE)
    current = store.open_session(connection)
    if current is not None:
        store.abandon(connection, current.id)
    started = service.start(connection, entry_id=payload.entry_id, mode=mode)
    if started["session"] is None:
        raise ConflictError("no_page", started["note"])
    if relay is not None:
        # The phone's own tutor: the Mac writes the opening question.
        session = started["session"]
        relay.wake_mac()
        relay.enqueue(session_id=session["id"], entry_id=session["entry_id"], transcript=[], exchanges=0)
        return {"session": _with_relay(session, relay), "note": RELAY_FIRST, "disclosure": DISCLOSURE, "gaps_filed": 0}
    return {**started, "note": _mode_note(mode), "disclosure": DISCLOSURE}


@router.post("/import", status_code=201)
async def import_session(
    payload: ImportIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    """Bring in a Socratic session held elsewhere (ADR 0026). With an assessment
    -- an assistant saving its own session -- it is filed as done and its gaps
    become flags. Without one, it is assessed on the Mac's own connection, here
    or when next opened on the Mac."""
    turns = [turn.model_dump() for turn in payload.transcript] or service.parse_transcript(payload.text)
    entry = service.match_page(connection, payload.topic or payload.title)
    try:
        session = store.import_session(
            connection, title=payload.title or payload.topic, topic=entry.topic if entry else payload.topic,
            entry_id=entry.id if entry else None, origin=payload.origin, transcript=turns, assessment=payload.assessment,
        )
    except ValueError:
        raise ConflictError("empty", "No answers of yours were found in that transcript. Paste the whole conversation.") from None
    if session.as_dict()["assessed"]:
        return {"session": session.as_dict(), "gaps_filed": service.file_gaps(connection, session), "note": ""}
    if mode in ("codex", "claude"):
        workspace = request.state.workspace
        return await service.review(workspace.database_path, session.id, workspace.turn_factory)
    return {"session": session.as_dict(), "gaps_filed": 0, "note": UNASSESSED}


@router.post("/{session_id}/assess")
async def assess(
    session_id: str,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    """Assess an imported session on the Mac's own connection."""
    store.get_session(connection, session_id)
    if mode not in ("codex", "claude"):
        raise ConflictError("needs_model", UNASSESSED)
    workspace = request.state.workspace
    return await service.review(workspace.database_path, session_id, workspace.turn_factory)


@router.get("/{session_id}")
def read(
    session_id: str, request: Request, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)
) -> dict[str, Any]:
    return {"session": _with_relay(store.get_session(connection, session_id).as_dict(), _relay(request, mode))}


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
    session = store.get_session(connection, session_id)
    relay = _relay(request, mode)
    if mode not in ("codex", "claude"):
        if relay is None:
            raise ConflictError("needs_model", service.HOST_MODE)
        # The phone's own tutor: keep the answer, and let the Mac write the next question.
        if session.status != "open":
            return {"session": session.as_dict(), "note": "This session has ended.", "gaps_filed": 0}
        if payload.answer.strip():
            session = store.append(connection, session_id, role="learner", text=payload.answer)
        relay.wake_mac()
        relay.enqueue(
            session_id=session_id, entry_id=session.entry_id, transcript=[dict(turn) for turn in session.transcript], exchanges=session.exchanges
        )
        return {"session": _with_relay(session.as_dict(), relay), "note": RELAY_NEXT, "gaps_filed": 0}
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
