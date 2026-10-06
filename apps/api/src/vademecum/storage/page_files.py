"""Encyclopedia pages as Markdown files in the source folder (ADR 0026), as Joplin keeps notes.

Every page is mirrored to ``<source folder>/encyclopedia/<subject>/<title>.md``
with a small front matter that names the page. The file and the page are kept
in step both ways: a page compiled or edited in the web app is written to its
file, and a file the owner edited in any editor is read back as the page's own
edit. A record in ``app_state`` holds the last content written for each page,
so a change is known to have come from the file and not from here. When both
changed since the last look, the file wins: it is the owner's.

The scan of ``piles/`` never reads this folder; nothing here is a source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..db import transaction
from .common import utc_now

logger = logging.getLogger("vademecum.pages")

FOLDER = "encyclopedia"
FIGURES = "_figures"
KEY = "page_files"
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe(name: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub(" ", name).strip().strip(".")
    return (cleaned or fallback)[:80]


def compiled_markdown(connection: sqlite3.Connection, entry_id: str) -> str:
    """The compiled page, as Markdown, with its sources under each paragraph."""
    from .encyclopedia import cited_points, get_entry

    entry = get_entry(connection, entry_id)
    citations = {c["id"]: c for c in cited_points(connection, list(entry.point_ids))}
    lines = [f"# {entry.title}", ""]
    if entry.summary:
        lines += [entry.summary, ""]
    for section in entry.sections:
        lines += [f"## {section['heading']}", ""]
        for paragraph in section["paragraphs"]:
            lines.append(paragraph["text"])
            for figure in paragraph.get("figures") or []:
                lines.append(f"![{figure['source']}, {figure['locator']}](../{FIGURES}/{figure['image_id']}{figure.get('ext', '')})")
            labels: list[str] = []
            for point_id in paragraph["point_ids"]:
                for source in (citations.get(point_id) or {}).get("sources", []):
                    label = f"{source['display_name']}, {source['locator']}" if source["locator"] else source["display_name"]
                    if label not in labels:
                        labels.append(label)
            if labels:
                lines.append(f"*From {'; '.join(labels)}*")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def body_for(connection: sqlite3.Connection, entry_id: str) -> str:
    row = connection.execute("SELECT body_md FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is not None and (row["body_md"] or "").strip():
        return row["body_md"].rstrip() + "\n"
    return compiled_markdown(connection, entry_id)


def _file_text(entry_id: str, topic: str, body: str) -> str:
    return f"---\nvademecum: {entry_id}\ntopic: {topic}\n---\n{body}"


def _parse(text: str) -> tuple[str | None, str]:
    match = _FRONT.match(text)
    if not match:
        return None, text
    fields = dict(line.split(":", 1) for line in match.group(1).splitlines() if ":" in line)
    return (fields.get("vademecum") or "").strip() or None, text[match.end():]


def _read_record(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY,)).fetchone()
    try:
        data = json.loads(row["value"]) if row else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def _write_record(connection: sqlite3.Connection, record: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps(record, separators=(",", ":")), utc_now()),
        )


def file_of(connection: sqlite3.Connection, entry_id: str) -> str | None:
    """The page's Markdown file, relative to the source folder, once it has been written."""
    path = (_read_record(connection).get(entry_id) or {}).get("path")
    return f"{FOLDER}/{path}" if isinstance(path, str) and path else None


def set_edit(connection: sqlite3.Connection, entry_id: str, body_md: str) -> None:
    now = utc_now()
    text = body_md.replace("\r\n", "\n").strip()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE encyclopedia_entries SET body_md = ?, edited_at = ?, updated_at = ? WHERE id = ?",
            (text[:200_000], now if text else None, now, entry_id),
        )


def sync_folder(connection: sqlite3.Connection, folder: Path | None, images_dir: Path | None = None) -> dict[str, int]:
    """Bring files and pages into step. Returns how many were written out and read in."""
    if folder is None:
        return {"written": 0, "read": 0}
    from . import map as map_store

    root = folder / FOLDER
    record = _read_record(connection)
    names = {entry.id: entry.name for entry in map_store.list_specialties(connection)}
    read = written = 0

    # Owner's edits made in a file come in first.
    if root.is_dir():
        for path in root.rglob("*.md"):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            entry_id, body = _parse(text)
            if not entry_id:
                continue
            known = record.get(entry_id) or {}
            if known.get("sha") == _digest(text):
                continue
            exists = connection.execute("SELECT 1 FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone()
            if exists is None:
                continue
            set_edit(connection, entry_id, body)
            record[entry_id] = {"path": str(path.relative_to(root)), "sha": _digest(text)}
            read += 1

    # Figures the pages use are copied beside them, once, so the files read anywhere.
    _copy_figures(connection, root, images_dir)

    # Then every page goes out, where its file is not already what it should be.
    rows = connection.execute("SELECT id, topic, title, specialty_id FROM encyclopedia_entries WHERE status = 'current'").fetchall()
    for row in rows:
        subject = _safe(names.get(row["specialty_id"] or "", "Other topics"), "Other topics")
        relative = f"{subject}/{_safe(row['title'], row['id'])}.md"
        text = _file_text(row["id"], row["topic"], body_for(connection, row["id"]))
        known = record.get(row["id"]) or {}
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
        record[row["id"]] = {"path": relative, "sha": _digest(text)}
        written += 1

    if read or written:
        _write_record(connection, record)
        logger.info("page_files written=%d read=%d", written, read)
    return {"written": written, "read": read}


def _copy_figures(connection: sqlite3.Connection, root: Path, images_dir: Path | None) -> None:
    import shutil

    if images_dir is None:
        return
    wanted: dict[str, str] = {}
    for row in connection.execute("SELECT sections FROM encyclopedia_entries WHERE status = 'current'").fetchall():
        try:
            for section in json.loads(row["sections"] or "[]"):
                for paragraph in section.get("paragraphs", []):
                    for figure in paragraph.get("figures") or []:
                        wanted[str(figure.get("image_id"))] = str(figure.get("ext") or "")
        except (ValueError, AttributeError):
            continue
    target_dir = root / FIGURES
    for image_id, ext in wanted.items():
        target = target_dir / f"{image_id}{ext}"
        if target.is_file():
            continue
        found = connection.execute("SELECT stored_name FROM source_images WHERE id = ?", (image_id,)).fetchone()
        if found is None or not (images_dir / found["stored_name"]).is_file():
            continue
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(images_dir / found["stored_name"], target)
        except OSError:
            continue
