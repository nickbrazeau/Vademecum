"""Notes as Markdown files in the source folder, on the Mac (ADR 0032).

Each note is written to ``notes/<notebook>/<…>/<title>.md``, its id in a short front
matter so it is known again after a rename. A file edited in any editor comes back in
when it is newer than what was last written; otherwise the note goes out. A note
deleted in Vademecum takes its file with it. A note marked "use as a source" is also
copied, plainly, to ``piles/lowconfidence/My notes/`` where the folder scan reads it in
and the builder builds from it as low-confidence material.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from . import app_state
from . import notes as store
from .page_files import _digest, _safe

logger = logging.getLogger("vademecum.notes")

FOLDER = "notes"
KEY = "note_files"
AS_SOURCE = ("piles", "lowconfidence", "My notes")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _file_text(note: store.Note) -> str:
    return f"---\nvademecum-note: {note.id}\n---\n# {note.title}\n\n{note.body_md.rstrip()}\n"


def _parse(text: str) -> tuple[str | None, str | None, str]:
    """(note id, title, body) from a note file; the title is its first heading."""
    match = _FRONT.match(text)
    if not match:
        return None, None, text
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    rest = text[match.end():].lstrip("\n")
    title = None
    if rest.startswith("# "):
        first, _, rest = rest.partition("\n")
        title = first[2:].strip() or None
    return (fields.get("vademecum-note") or "").strip() or None, title, rest.strip("\n")


def _relative(connection: sqlite3.Connection, note: store.Note) -> str:
    parts = [_safe(title, "Untitled") for title in store.path_of(connection, note)]
    return "/".join(parts) + ".md"


def sync_folder(connection: sqlite3.Connection, folder: Path | None) -> dict[str, int]:
    """Bring note files and notes into step. Returns how many were written and read."""
    if folder is None:
        return {"written": 0, "read": 0}
    root = folder / FOLDER
    record: dict[str, Any] = app_state.read_dict(connection, KEY) or {}
    read = written = 0

    # Edits made in a file come in first.
    if root.is_dir():
        for path in root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            note_id, title, body = _parse(text)
            if not note_id or (record.get(note_id) or {}).get("sha") == _digest(text):
                continue
            try:
                store.update_note(connection, note_id, title=title, body_md=body)
            except Exception:  # noqa: BLE001 - a note deleted meanwhile, or a file half-written
                continue
            record[note_id] = {"path": str(path.relative_to(root)), "sha": _digest(text)}
            read += 1

    present = {note.id: note for note in store.tree(connection)}
    # Notes deleted in Vademecum take their files with them.
    for note_id in [key for key in record if key not in present]:
        old = (record.pop(note_id) or {}).get("path")
        if old:
            (root / old).unlink(missing_ok=True)
        written += 1

    # Then every note goes out where its file is not already what it should be.
    for note in present.values():
        if note.notebook and not note.body_md.strip():
            continue  # a notebook is a folder; its notes are its files
        relative = _relative(connection, note)
        text = _file_text(note)
        known = record.get(note.id) or {}
        target = root / relative
        if known.get("sha") == _digest(text) and known.get("path") == relative and target.is_file():
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            old = known.get("path")
            if old and old != relative:
                (root / old).unlink(missing_ok=True)
        except OSError:
            continue
        record[note.id] = {"path": relative, "sha": _digest(text)}
        written += 1

    _as_sources(folder, present)
    if read or written:
        _save_record(connection, record)
        logger.info("note_files written=%d read=%d", written, read)
    return {"written": written, "read": read}


def _save_record(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    from ..db import transaction

    with transaction(connection) as tx:
        app_state.write_json(tx, KEY, record)


def _as_sources(folder: Path, present: dict[str, store.Note]) -> None:
    """Notes marked as a source are copied where the builder reads; unmarked ones go."""
    target_dir = folder.joinpath(*AS_SOURCE)
    wanted = {f"{_safe(note.title, 'Untitled')}.md": note for note in present.values() if note.use_as_source and note.body_md.strip()}
    if wanted:
        target_dir.mkdir(parents=True, exist_ok=True)
    if target_dir.is_dir():
        for path in target_dir.glob("*.md"):
            if path.name not in wanted:
                path.unlink(missing_ok=True)
    for name, note in wanted.items():
        path = target_dir / name
        text = f"# {note.title}\n\n{note.body_md.rstrip()}\n"
        try:
            if not path.is_file() or path.read_text(encoding="utf-8") != text:
                path.write_text(text, encoding="utf-8")
        except OSError:
            continue
