"""What is being processed, and what has been (feedback of 6 October).

Three stages, each answered cheaply:

- **Waiting**: files in the source folder not yet read in. Found by name -- the pile a
  file sits in and its file name -- against the sources already stored, so nothing is
  read or hashed to say so.
- **Read in**: stored sources, with how much of each the encyclopedia agent has built
  from (none, partly, all), by the text it has covered.
- **The last scan**: what it took in and what it turned away and why, recorded as it
  happens; a scan's report was only logged before.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from ..db import transaction
from ..ingest.detect import safe_display_name
from ..ingest.folder import PILES_DIRNAME, SKIP_PREFIXES, TIER_TITLES, UNSORTED, tier_of
from . import app_state
from .common import utc_now

SCAN_KEY = "ingest_scan"
MAX_LISTED = 200


def record_scan(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    """Keep the latest scan's outcome: counts, and the files it turned away, by name."""
    rejected = [
        {"filename": str(item.get("filename") or ""), "pile": str(item.get("pile") or ""), "message": str(item.get("message") or "")}
        for item in report.get("rejected") or []
        if isinstance(item, dict)
    ][:50]
    record = {
        "at": utc_now(),
        "stored": len(report.get("stored") or []),
        "already_present": int(report.get("already_present") or 0),
        "waiting_to_settle": int(report.get("waiting") or 0),
        "more_waiting": bool(report.get("more_waiting")),
        "rejected": rejected,
    }
    with transaction(connection) as tx:
        app_state.write_json(tx, SCAN_KEY, record, at=record["at"])


def last_scan(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return app_state.read_dict(connection, SCAN_KEY)


def _pile_for(relative_parts: tuple[str, ...]) -> str | None:
    """The pile a file in piles/ belongs to, as the folder scan names it."""
    if len(relative_parts) == 1:
        return UNSORTED
    top = relative_parts[0]
    tier = tier_of(top)
    if tier is None:
        return top.strip()  # a pile folder straight under piles/
    if len(relative_parts) == 2:
        return TIER_TITLES[tier]  # a loose file in a confidence folder
    if len(relative_parts) == 3:
        return relative_parts[1].strip()
    return None  # deeper than the scan reads


def folder_files(folder: Path) -> list[tuple[str, str]]:
    """Every file the scan would consider: (pile title, display name)."""
    return [(pile, name) for pile, name, _path in folder_entries(folder)]


def folder_entries(folder: Path) -> list[tuple[str, str, Path]]:
    """Every file the scan would consider: (pile title, display name, path)."""
    root = folder / PILES_DIRNAME
    found: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return found
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [d for d in subdirs if not d.startswith(SKIP_PREFIXES)]
        parts = Path(directory).relative_to(root).parts
        for name in files:
            if name.startswith(SKIP_PREFIXES):
                continue
            pile = _pile_for((*parts, name))
            if pile is not None:
                found.append((pile, safe_display_name(name), Path(directory) / name))
    return found


def progress(
    connection: sqlite3.Connection,
    folder: Path | None,
    *,
    cache: dict[str, Any] | None = None,
    builder: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Every file's stage, each with the reason it is where it is (feedback of 10 October).
    ``cache`` is the folder scan's record of each file's contents, by path; ``builder`` is
    what the background builder is doing."""
    rows_stored = connection.execute(
        "SELECT p.title AS pile, s.display_name, s.sha256 FROM sources s JOIN piles p ON p.id = s.pile_id"
    ).fetchall()
    stored = {(row["pile"].casefold(), row["display_name"]) for row in rows_stored}
    by_digest = {row["sha256"]: row["display_name"] for row in rows_stored if row["sha256"]}
    last = last_scan(connection) or {}
    turned_away = {
        (str(item.get("pile", "")).casefold(), str(item.get("filename", ""))): str(item.get("message", ""))
        for item in last.get("rejected") or []
        if isinstance(item, dict)
    }
    waiting: list[dict[str, str]] = []
    duplicates: list[dict[str, str]] = []
    on_disk = 0
    if folder is not None:
        files = folder_entries(folder)
        on_disk = len(files)
        queued = 0
        for pile, name, path in files:
            if (pile.casefold(), name) in stored:
                continue
            known = (cache or {}).get(str(path))
            digest = known[2] if isinstance(known, list) and len(known) == 3 else None
            if digest and digest in by_digest:
                # Same contents as a file already read in, under another name: nothing to do.
                duplicates.append({"pile": pile, "filename": name, "reason": f"Already here as {by_digest[digest]} (the same contents)."})
                continue
            why = turned_away.get((pile.casefold(), name))
            if why:
                waiting.append({"pile": pile, "filename": name, "reason": f"Turned away: {why}"})
                continue
            queued += 1
            waiting.append({"pile": pile, "filename": name, "reason": "Next to be read in." if queued == 1 else f"Waiting to be read in: {queued - 1} ahead of it."})
    rows = connection.execute(
        """
        SELECT s.id, s.display_name, s.status, s.created_at, p.title AS pile,
               (SELECT COUNT(DISTINCT lps.learning_point_id) FROM learning_point_sources lps WHERE lps.source_id = s.id) AS points,
               (SELECT COALESCE(SUM(g.char_count), 0) FROM source_segments g WHERE g.source_id = s.id) AS chars_total,
               (SELECT COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0) FROM source_segments g WHERE g.source_id = s.id) AS chars_covered
          FROM sources s JOIN piles p ON p.id = s.pile_id
         ORDER BY s.created_at DESC
        """
    ).fetchall()
    sources = []
    counts = {"not_started": 0, "partly": 0, "built": 0, "unreadable": 0}
    for row in rows:
        total, covered = int(row["chars_total"] or 0), int(row["chars_covered"] or 0)
        if row["status"] in ("unreadable", "encrypted", "needs_ocr") or total == 0:
            state = "unreadable"
        elif covered >= total:
            state = "built"
        elif covered > 0:
            state = "partly"
        else:
            state = "not_started"
        counts[state] += 1
        reason = _source_reason(state, row["status"], row["pile"], builder)
        sources.append(
            {
                "id": row["id"],
                "filename": row["display_name"],
                "pile": row["pile"],
                "status": row["status"],
                "state": state,
                "percent": round(100 * covered / total) if total else 0,
                "points": int(row["points"] or 0),
                "added_at": row["created_at"],
                "reason": reason,
            }
        )
    return {
        "folder": {
            "present": folder is not None,
            "files": on_disk,
            "waiting_count": len(waiting),
            "waiting": waiting[:MAX_LISTED],
            "duplicates": duplicates[:MAX_LISTED],
            "last_scan": last_scan(connection),
        },
        "sources": {"counts": counts, "total": len(sources), "items": sources},
        "builder": builder,
    }


_UNREADABLE = {
    "needs_ocr": "No readable text: a scan or picture the Mac could not read.",
    "encrypted": "Locked with a password, so it cannot be read.",
    "unreadable": "The file could not be read.",
}


def _source_reason(state: str, status: str, pile: str, builder: dict[str, Any] | None) -> str:
    """Why a source is not yet fully built, in a sentence."""
    if state == "built":
        return ""
    if state == "unreadable":
        return _UNREADABLE.get(status, "Nothing in it could be read as text.")
    if builder is None or not builder.get("enabled"):
        return "Building in the background is off; turn it on below, or build the pile by hand."
    working = builder.get("builder") or {}
    if working.get("state") == "building" and pile in str(working.get("reason", "")):
        return "Being built now."
    if working.get("state") in ("paused", "limited", "waiting", "blocked", "off"):
        return str(working.get("reason", ""))
    return "In line to be built." if state == "not_started" else "Partly built; the rest is in line."
