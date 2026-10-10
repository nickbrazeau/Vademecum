"""The owner's own notes (ADR 0032, feedback of 10 October).

Notebooks hold notes, and notes can hold notes: one tree, each node in Markdown. Written
on the Mac or the phone and shared between them, the later edit winning. On the Mac the
tree is mirrored to Markdown files under ``notes/`` in the source folder, and a note marked
"use as a source" is also put where the builder reads (``piles/lowconfidence/My notes``).
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

MAX_TITLE = 200
MAX_BODY = 200_000
MAX_DEPTH = 8


@dataclass(frozen=True)
class Note:
    id: str
    parent_id: str | None
    notebook: bool
    title: str
    body_md: str
    position: float
    use_as_source: bool
    created_at: str
    updated_at: str

    def as_dict(self, *, with_body: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not with_body:
            data.pop("body_md")
            data["has_body"] = bool(self.body_md.strip())
        return data


def _note(row: sqlite3.Row) -> Note:
    return Note(
        id=row["id"],
        parent_id=row["parent_id"],
        notebook=bool(row["notebook"]),
        title=row["title"],
        body_md=row["body_md"],
        position=float(row["position"]),
        use_as_source=bool(row["use_as_source"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def tree(connection: sqlite3.Connection) -> list[Note]:
    """Every notebook and note, parents before children, in each level's order."""
    return [_note(row) for row in connection.execute("SELECT * FROM notes ORDER BY parent_id IS NOT NULL, position, title")]


def get_note(connection: sqlite3.Connection, note_id: str) -> Note:
    row = connection.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise NotFoundError("note", note_id)
    return _note(row)


def _depth(connection: sqlite3.Connection, parent_id: str | None) -> int:
    depth = 0
    while parent_id is not None and depth <= MAX_DEPTH:
        row = connection.execute("SELECT parent_id FROM notes WHERE id = ?", (parent_id,)).fetchone()
        if row is None:
            raise NotFoundError("note", parent_id)
        parent_id = row["parent_id"]
        depth += 1
    return depth


def _descendants(connection: sqlite3.Connection, note_id: str) -> set[str]:
    found: set[str] = set()
    frontier = [note_id]
    while frontier:
        current = frontier.pop()
        for row in connection.execute("SELECT id FROM notes WHERE parent_id = ?", (current,)):
            if row["id"] not in found:
                found.add(row["id"])
                frontier.append(row["id"])
    return found


def create_note(
    connection: sqlite3.Connection, *, title: str, parent_id: str | None = None, body_md: str = "", notebook: bool = False
) -> Note:
    title = title.strip()[:MAX_TITLE] or ("Untitled notebook" if notebook else "Untitled note")
    if _depth(connection, parent_id) >= MAX_DEPTH:
        raise ValueError(f"Notes nest at most {MAX_DEPTH} deep.")
    now = utc_now()
    note_id = new_id("note")
    row = connection.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 AS next FROM notes WHERE parent_id IS ?", (parent_id,)
    ).fetchone()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO notes (id, parent_id, notebook, title, body_md, position, use_as_source, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
            (note_id, parent_id, 1 if notebook else 0, title, body_md[:MAX_BODY], float(row["next"]), now, now),
        )
    return get_note(connection, note_id)


def update_note(
    connection: sqlite3.Connection,
    note_id: str,
    *,
    title: str | None = None,
    body_md: str | None = None,
    use_as_source: bool | None = None,
    parent_id: str | None | bool = False,
    position: float | None = None,
) -> Note:
    """Change what is given; ``parent_id`` False leaves it where it is, None moves it to the top."""
    current = get_note(connection, note_id)
    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title.strip()[:MAX_TITLE] or current.title
    if body_md is not None:
        fields["body_md"] = body_md.replace("\r\n", "\n")[:MAX_BODY]
    if use_as_source is not None:
        fields["use_as_source"] = 1 if use_as_source else 0
    if parent_id is not False:
        if parent_id == note_id or (parent_id is not None and parent_id in _descendants(connection, note_id)):
            raise ValueError("A note cannot go inside itself.")
        if _depth(connection, parent_id) >= MAX_DEPTH:
            raise ValueError(f"Notes nest at most {MAX_DEPTH} deep.")
        fields["parent_id"] = parent_id
    if position is not None:
        fields["position"] = float(position)
    if not fields:
        return current
    fields["updated_at"] = utc_now()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with transaction(connection) as tx:
        tx.execute(f"UPDATE notes SET {assignments} WHERE id = ?", (*fields.values(), note_id))
    return get_note(connection, note_id)


def delete_note(connection: sqlite3.Connection, note_id: str) -> int:
    """A note and everything inside it. Returns how many went."""
    get_note(connection, note_id)
    gone = 1 + len(_descendants(connection, note_id))
    with transaction(connection) as tx:
        tx.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return gone


def search(connection: sqlite3.Connection, words: str, *, limit: int = 30) -> list[Note]:
    """Notes whose title or text holds every word, titles first."""
    terms = [term for term in words.lower().split() if term][:8]
    if not terms:
        return []
    where = " AND ".join("(lower(title) LIKE ? OR lower(body_md) LIKE ?)" for _ in terms)
    params: list[Any] = []
    for term in terms:
        params += [f"%{term}%", f"%{term}%"]
    rows = connection.execute(
        f"SELECT * FROM notes WHERE {where} ORDER BY (lower(title) LIKE ?) DESC, updated_at DESC LIMIT ?",
        (*params, f"%{terms[0]}%", int(limit)),
    ).fetchall()
    return [_note(row) for row in rows]


def path_of(connection: sqlite3.Connection, note: Note) -> list[str]:
    """Titles from the top of the tree down to this note."""
    titles = [note.title]
    parent = note.parent_id
    seen = 0
    while parent is not None and seen <= MAX_DEPTH:
        row = connection.execute("SELECT title, parent_id FROM notes WHERE id = ?", (parent,)).fetchone()
        if row is None:
            break
        titles.append(row["title"])
        parent = row["parent_id"]
        seen += 1
    return list(reversed(titles))
