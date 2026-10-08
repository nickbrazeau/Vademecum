"""The encyclopedia and the board bank (ADR 0023).

Pages are read here, a page of the day is chosen here, and "Compile now"
starts the two model turns in the background. The board routes mirror the
Tutor's cycle; answering a choice is local to this machine, so nothing is
transmitted on this path and no disclosure is needed before the button.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field

from ..model.dissection import NEEDS_MODEL
from ..model.encyclopedia import WAITING
from ..storage import encyclopedia as store
from ..storage import learner
from ..storage import map as map_store
from ..storage.sources import ConflictError
from .deps import get_connection, get_model_mode
from .routes_sources import CLAUDE_DESTINATION, CODEX_DESTINATION
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
    # Within one page's questions (Tutor mode from the Improvement Map), not the pass.
    entry_id: RecordId | None = None
    # "need": the page the learner model says needs it most (ADR 0031), not the pass.
    focus: Literal["need"] | None = None


class BoardAnswer(Strict):
    question_id: RecordId
    choice: Annotated[int, Field(ge=0, le=4)]


DISSECTION_DISCLOSURE = (
    "Dissecting a pile is a standing consent: until you stop it, the agent sends batch after batch "
    "of the pile's excerpts -- their text, filenames, confidence labels and locations -- and the "
    "follow-up checks' claims with retrieved abstracts, as Build does, without a further prompt; "
    "then, per topic, the points built, to write each page, with one public PubMed search of the "
    "topic's words for its literature review; then each page and its points, to write board "
    "questions. In codex mode: " + CODEX_DESTINATION + " In claude mode: " + CLAUDE_DESTINATION
    + " The topic's words go to PubMed."
)


class DissectIn(Strict):
    pile_id: RecordId


def get_dissector(request: Request):
    return getattr(request.app.state, "dissector", None)


class PageEditIn(Strict):
    body_md: Annotated[str, Field(max_length=200_000)]


def _sync_files(request: Request) -> None:
    """Mirror pages to Markdown files in the source folder, and read the owner's file edits back (ADR 0026)."""
    from ..app import current_sources_dir
    from ..db import connect
    from ..storage import page_files

    settings = request.app.state.settings
    if settings.tenancy != "single":
        return
    folder = current_sources_dir(settings)
    connection = connect(request.state.workspace.database_path)
    try:
        page_files.sync_folder(connection, folder, request.state.workspace.source_dir.parent / "images")
    finally:
        connection.close()


def _page_payload(connection: sqlite3.Connection, entry: store.Entry) -> dict[str, Any]:
    from ..storage import page_files

    data = entry.as_dict()
    data["markdown"] = page_files.body_for(connection, entry.id)
    # Its Markdown file in the source folder, like a Joplin note (feedback of 5 October).
    data["file_path"] = page_files.file_of(connection, entry.id)
    data["citations"] = store.cited_points(connection, list(entry.point_ids))
    data["literature"] = store.records_for_entry(connection, entry.id)
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
    return {
        "entries": [entry.as_dict(include_sections=False) for entry in entries],
        # The subjects the pages are shelved under, in the map's own order.
        "specialties": [entry.as_dict() for entry in map_store.list_specialties(connection)],
        **_state(request, connection, mode),
    }


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


@router.get("/dissection")
def read_dissection(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    dissector = get_dissector(request)
    if dissector is None:
        return {"status": "idle", "running": False, "can_run": False, "blocked_reason": NEEDS_MODEL, "disclosure": DISSECTION_DISCLOSURE}
    return {**dissector.describe(connection), "disclosure": DISSECTION_DISCLOSURE}


@router.post("/dissection", status_code=status.HTTP_202_ACCEPTED)
async def start_dissection(payload: DissectIn, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Start, or resume, the agent on one pile. Pressing this is the consent the disclosure describes."""
    dissector = get_dissector(request)
    if dissector is None or not dissector.can_run:
        raise ConflictError("needs_model", NEEDS_MODEL)
    dissector.start(payload.pile_id)
    return {"started": True, **dissector.describe(connection), "disclosure": DISSECTION_DISCLOSURE}


@router.post("/dissection/stop")
async def stop_dissection(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    dissector = get_dissector(request)
    if dissector is None:
        raise ConflictError("needs_model", NEEDS_MODEL)
    await dissector.stop(by_owner=True)
    return {"stopped": True, **dissector.describe(connection), "disclosure": DISSECTION_DISCLOSURE}


@router.get("/deleted")
def list_deleted(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Pages the owner deleted, which the encyclopedia does not write again."""
    return {"deleted": sorted(store.deleted_pages(connection).values(), key=lambda d: d.get("deleted_at", ""), reverse=True)}


class RestoreIn(Strict):
    topic: str = Field(min_length=1, max_length=200)


@router.post("/deleted/restore")
def restore_deleted(payload: RestoreIn, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict[str, Any]:
    """Bring a deleted page back: it is written again at the next compile, on the Mac."""
    if mode not in ("codex", "claude"):
        raise ConflictError("on_the_mac", "Pages are brought back on the Mac, where the encyclopedia is written.")
    return {"restored": store.restore_page(connection, payload.topic)}


@router.get("/{entry_id}")
def read_entry(entry_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return _page_payload(connection, store.get_entry(connection, entry_id))


@router.post("/{entry_id}/reveal")
def reveal_file(entry_id: str, request: Request, open_it: bool = False, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Show the page's Markdown file in Finder, or open it in the Mac's editor for .md
    files. On this Mac only: nothing is sent, and the cloud copy has no folder."""
    import subprocess
    import sys

    from ..storage import page_files

    store.get_entry(connection, entry_id)
    folder = getattr(request.app.state, "sources_folder", None)
    relative = page_files.file_of(connection, entry_id)
    if folder is None or relative is None or sys.platform != "darwin":
        raise ConflictError("no_file", "This page's Markdown file is on the Mac, in the source folder under encyclopedia/.")
    path = (Path(folder) / relative).resolve()
    if not path.is_file() or Path(folder).resolve() not in path.parents:
        raise ConflictError("no_file", "The file is not there yet; it is written at the next folder scan.")
    subprocess.run(["open", str(path)] if open_it else ["open", "-R", str(path)], check=False, timeout=10, capture_output=True)
    return {"path": relative}


@router.delete("/{entry_id}")
def delete_page(entry_id: str, request: Request, connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict[str, Any]:
    """Delete a page, its questions, cards and Markdown file, and never write it again
    (feedback of 6 October). On the Mac, whose copy is the page's; the deletion reaches
    the phone at the next sync. Learning points and sources stay."""
    from ..storage import page_files

    if mode not in ("codex", "claude"):
        raise ConflictError("on_the_mac", "Pages are deleted on the Mac, where the encyclopedia is written; the phone follows at the next sync.")
    gone = store.delete_entry(connection, entry_id)
    page_files.remove_file(connection, getattr(request.app.state, "sources_folder", None), entry_id)
    return {"deleted": gone}


@router.put("/{entry_id}")
def edit_entry(entry_id: str, payload: PageEditIn, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """The owner's own edit, in Markdown, kept beside the compiled text and written to the page's file."""
    from ..storage import page_files

    store.get_entry(connection, entry_id)
    page_files.set_edit(connection, entry_id, payload.body_md)
    _sync_files(request)
    return _page_payload(connection, store.get_entry(connection, entry_id))


@router.delete("/{entry_id}/edit")
def revert_entry(entry_id: str, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Drop the owner's edit; the compiled page shows again, and its file follows."""
    from ..storage import page_files

    store.get_entry(connection, entry_id)
    page_files.set_edit(connection, entry_id, "")
    _sync_files(request)
    return _page_payload(connection, store.get_entry(connection, entry_id))


# --- the board bank ------------------------------------------------------------------


@board_router.get("")
def board_overview(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return store.board_overview(connection)


@board_router.get("/next")
def board_next(
    entry_id: str | None = None,
    focus: Literal["need"] | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    """The question to ask now, without its key. Idempotent, so a refresh keeps your place.
    With entry_id, one page's questions instead of the shuffled pass; with focus=need, the
    page the learner model says needs it most (ADR 0031)."""
    if not entry_id and focus == "need":
        entry_id = learner.most_needed_entry(connection, kind="board")
    if entry_id:
        return store.next_for_page(connection, entry_id).as_dict()
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
    asked = store.get_question(connection, payload.question_id)
    if payload.focus == "need" and not payload.entry_id:
        entry_id = learner.most_needed_entry(connection, kind="board", not_entry=asked.entry_id)
        if entry_id:
            return store.next_for_page(connection, entry_id, not_id=payload.question_id).as_dict()
    if payload.entry_id:
        return store.next_for_page(connection, payload.entry_id, not_id=payload.question_id).as_dict()
    return store.advance_question(connection, payload.question_id).as_dict()


@board_router.get("/history")
def board_history(limit: int = 20, connection: sqlite3.Connection = Depends(get_connection)) -> list[dict[str, Any]]:
    return store.recent_attempts(connection, limit=max(1, min(int(limit), 200)))
