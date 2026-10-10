"""The owner's notes (ADR 0032, feedback of 10 October): notebooks and nested notes in
Markdown, the same on the Mac and the phone."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import Field

from ..storage import notes as store
from ..storage.sources import ConflictError
from .deps import get_connection
from .schemas import RecordId, Strict

router = APIRouter(prefix="/notes", tags=["notes"])


class NoteIn(Strict):
    title: Annotated[str, Field(max_length=store.MAX_TITLE)] = ""
    parent_id: RecordId | None = None
    body_md: Annotated[str, Field(max_length=store.MAX_BODY)] = ""
    notebook: bool = False


class NoteChange(Strict):
    title: Annotated[str, Field(max_length=store.MAX_TITLE)] | None = None
    body_md: Annotated[str, Field(max_length=store.MAX_BODY)] | None = None
    use_as_source: bool | None = None
    # Moving: the new parent, or "" for the top level; absent leaves it where it is.
    parent_id: Annotated[str, Field(max_length=64)] | None = None
    position: float | None = None


def _sync_files(request: Request) -> None:
    """On the Mac, the note's file follows at once rather than at the next folder look."""
    folder = getattr(request.app.state, "sources_folder", None)
    if folder is None:
        return
    from ..db import connect
    from ..storage import note_files

    connection = connect(request.app.state.database_path)
    try:
        note_files.sync_folder(connection, folder)
    except Exception:  # noqa: BLE001 - the file catches up at the next folder look
        pass
    finally:
        connection.close()


@router.get("")
def list_notes(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """The whole tree, without the text of each note."""
    return {"notes": [note.as_dict(with_body=False) for note in store.tree(connection)]}


@router.get("/search")
def search_notes(q: Annotated[str, Query(max_length=200)] = "", connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return {
        "notes": [
            {**note.as_dict(with_body=False), "path": store.path_of(connection, note)}
            for note in store.search(connection, q)
        ]
    }


@router.get("/{note_id}")
def read_note(note_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    note = store.get_note(connection, note_id)
    return {**note.as_dict(), "path": store.path_of(connection, note)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_note(payload: NoteIn, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    try:
        note = store.create_note(connection, title=payload.title, parent_id=payload.parent_id, body_md=payload.body_md, notebook=payload.notebook)
    except ValueError as exc:
        raise ConflictError("note_refused", str(exc)) from None
    _sync_files(request)
    return note.as_dict()


@router.patch("/{note_id}")
def change_note(note_id: str, payload: NoteChange, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    fields = payload.model_fields_set
    try:
        note = store.update_note(
            connection,
            note_id,
            title=payload.title,
            body_md=payload.body_md,
            use_as_source=payload.use_as_source,
            parent_id=(payload.parent_id or None) if "parent_id" in fields else False,
            position=payload.position,
        )
    except ValueError as exc:
        raise ConflictError("note_refused", str(exc)) from None
    _sync_files(request)
    return note.as_dict()


@router.delete("/{note_id}")
def delete_note(note_id: str, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    gone = store.delete_note(connection, note_id)
    _sync_files(request)
    return {"deleted": gone}
