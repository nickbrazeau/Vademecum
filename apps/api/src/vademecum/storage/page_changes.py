"""Page edits and deletions asked for away from the Mac (feedback of 10 October).

Encyclopedia pages are the Mac's: a copy of a page edited on the cloud copy would be
overwritten by the Mac's own at the next sync. So the cloud copy records the change
here, shows it at once, and the Mac applies it to its own page at its next sync, as if
the owner had made it there; the result then comes back to the phone in the usual way.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..db import transaction
from .common import new_id, utc_now

KINDS = ("edit", "revert", "delete")


def record(connection: sqlite3.Connection, entry_id: str, kind: str, body_md: str = "") -> str:
    if kind not in KINDS:
        raise ValueError(f"not a page change: {kind}")
    change_id = new_id("pgc")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO page_changes (id, entry_id, kind, body_md, created_at) VALUES (?, ?, ?, ?, ?)",
            (change_id, entry_id, kind, body_md, utc_now()),
        )
    return change_id


def waiting(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM page_changes WHERE applied_at IS NULL ORDER BY created_at, id"
    ).fetchall()


def apply_waiting(connection: sqlite3.Connection, folder: Path | None) -> int:
    """On the Mac: apply each change still waiting, oldest first, and mark it applied.
    A change to a page that no longer exists is marked applied with nothing to do."""
    from . import encyclopedia, page_files

    applied = 0
    for change in waiting(connection):
        try:
            encyclopedia.get_entry(connection, change["entry_id"])
            present = True
        except Exception:  # noqa: BLE001 - a page gone since is simply nothing to do
            present = False
        if present:
            if change["kind"] == "edit":
                page_files.set_edit(connection, change["entry_id"], change["body_md"])
            elif change["kind"] == "revert":
                page_files.set_edit(connection, change["entry_id"], "")
            else:
                encyclopedia.delete_entry(connection, change["entry_id"])
                page_files.remove_file(connection, folder, change["entry_id"])
        with transaction(connection) as tx:
            tx.execute("UPDATE page_changes SET applied_at = ? WHERE id = ?", (utc_now(), change["id"]))
        applied += 1
    return applied
