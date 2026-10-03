"""The encyclopedia and the board bank (ADR 0023).

Pages are read here, a page of the day is chosen here, and "Compile now"
starts the two model turns in the background. The board routes mirror the
Tutor's cycle; answering a choice is local to this machine, so nothing is
transmitted on this path and no disclosure is needed before the button.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field

from ..model.encyclopedia import WAITING
from ..storage import encyclopedia as store
from ..storage.sources import ConflictError
from .deps import get_connection, get_model_mode
from .schemas import RecordId, Strict

router = APIRouter(prefix="/encyclopedia", tags=["encyclopedia"])
board_router = APIRouter(prefix="/tutor/board", tags=["board"])

COMPILE_DISCLOSURE = (
    "Compiling sends, per topic, the learning points already built from your sources -- their "
    "claims, details, support labels and source names -- once, to the Mac's own model connection "
    "(Codex or Claude, on your sign-in; no API key), to write the page. Writing questions sends the "
    "page, its points, and as context the case-series teaching points in its specialty and your "
    "open flags on the topic. Nothing else; no source file is sent."
)


class CompileIn(Strict):
    pass


class BoardAdvance(Strict):
    question_id: RecordId


class BoardAnswer(Strict):
    question_id: RecordId
    choice: Annotated[int, Field(ge=0, le=4)]


def _page_payload(connection: sqlite3.Connection, entry: store.Entry) -> dict[str, Any]:
    data = entry.as_dict()
    data["citations"] = store.cited_points(connection, list(entry.point_ids))
    data["questions"] = [q.as_dict() for q in store.questions_for_entry(connection, entry.id) if q.status == "eligible"]
    return data


def _state(request: Request, connection: sqlite3.Connection, mode: str) -> dict[str, Any]:
    task = getattr(request.app.state, "encyclopedia_task", None)
    return {
        "counts": store.entry_counts(connection),
        "can_compile": mode in ("codex", "claude"),
        "running": task is not None and not task.done(),
        "last_refresh": store.get_last_refresh(connection),
        "note": "" if mode in ("codex", "claude") else WAITING,
        "disclosure": COMPILE_DISCLOSURE,
    }


@router.get("")
def list_entries(
    request: Request,
    q: str | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    entries = store.list_entries(connection, q=(q or "").strip()[:100] or None)
    return {"entries": [entry.as_dict(include_sections=False) for entry in entries], **_state(request, connection, mode)}


@router.get("/page")
def page(
    random: bool = False,
    not_id: str | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    """The page of the day; with random=true, another page, chosen now."""
    entry = store.random_page(connection, not_id=not_id) if random else store.page_of_the_day(connection)
    counts = store.entry_counts(connection)
    return {"page": None if entry is None else _page_payload(connection, entry), "counts": counts, "message": "" if entry else store.NO_PAGES}


@router.post("/compile", status_code=status.HTTP_202_ACCEPTED)
async def compile_now(
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    """Compile stale topics and write questions where a page has too few, in the background."""
    runner = getattr(request.app.state, "encyclopedia_runner", None)
    if mode not in ("codex", "claude") or runner is None:
        raise ConflictError("needs_model", WAITING)
    task = getattr(request.app.state, "encyclopedia_task", None)
    if task is not None and not task.done():
        raise ConflictError("compile_in_progress", "A compile is already in progress.")
    request.app.state.encyclopedia_task = asyncio.create_task(runner(reason="requested"))
    return {"started": True, **_state(request, connection, mode)}


@router.get("/{entry_id}")
def read_entry(entry_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return _page_payload(connection, store.get_entry(connection, entry_id))


# --- the board bank ------------------------------------------------------------------


@board_router.get("")
def board_overview(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return store.board_overview(connection)


@board_router.get("/next")
def board_next(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """The question to ask now, without its key. Idempotent, so a refresh keeps your place."""
    return store.next_question(connection).as_dict()


@board_router.post("/answer")
def board_answer(payload: BoardAnswer, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Check a choice against the key. Local: nothing leaves this machine."""
    question = store.get_question(connection, payload.question_id)
    if question.id not in set(store.eligible_board_ids(connection)):
        raise ConflictError("not_eligible", "This question is no longer eligible; a point it cites was held or its page was rewritten.")
    attempt = store.record_attempt(connection, question, payload.choice)
    return {
        "attempt": attempt.as_dict(),
        "question": question.as_dict(include_answer=True),
        "citations": store.cited_points(connection, list(question.point_ids)),
    }


@board_router.post("/advance")
def board_advance(payload: BoardAdvance, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    store.get_question(connection, payload.question_id)
    return store.advance_question(connection, payload.question_id).as_dict()


@board_router.get("/history")
def board_history(limit: int = 20, connection: sqlite3.Connection = Depends(get_connection)) -> list[dict[str, Any]]:
    return store.recent_attempts(connection, limit=max(1, min(int(limit), 200)))
