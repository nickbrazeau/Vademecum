"""Knowledge Gap Flags.

A Knowledge Gap Flag is a moment of self-identified clinical uncertainty --
"I wasn't sure about X and had to check" -- captured with minimal effort. It is
not a graded miss, and no correct answer is attached to it (CONTEXT.md).

Only `text` is required. Filing a flag against a topic is the system's job.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

STATUSES: tuple[str, ...] = ("open", "addressed")
# Where filing puts a saved link it can tell nothing about (ADR 0021). It marks the flag as
# looked at, so it is not sent again; it is not a topic, and the map does not draw it.
UNSORTED_TOPIC = "unsorted link"


@dataclass(frozen=True)
class Flag:
    id: str
    text: str
    topic: str | None
    pile_id: str | None
    status: str
    created_at: str
    updated_at: str
    addressed_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _flag(row: sqlite3.Row) -> Flag:
    return Flag(
        id=row["id"],
        text=row["text"],
        topic=row["topic"],
        pile_id=row["pile_id"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        addressed_at=row["addressed_at"],
    )


def list_flags(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    limit: int | None = None,
) -> list[Flag]:
    sql = "SELECT * FROM knowledge_gap_flags"
    params: list[Any] = []
    if status is not None:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY created_at DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_flag(row) for row in connection.execute(sql, params).fetchall()]


def get_flag(connection: sqlite3.Connection, flag_id: str) -> Flag:
    row = connection.execute(
        "SELECT * FROM knowledge_gap_flags WHERE id = ?", (flag_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("flag", flag_id)
    return _flag(row)


def create_flag(
    connection: sqlite3.Connection,
    *,
    text: str,
    topic: str | None = None,
    pile_id: str | None = None,
) -> Flag:
    now = utc_now()
    flag_id = new_id("kgf")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO knowledge_gap_flags"
            " (id, text, topic, pile_id, status, created_at, updated_at, addressed_at)"
            " VALUES (?, ?, ?, ?, 'open', ?, ?, NULL)",
            (flag_id, text, topic, pile_id, now, now),
        )
    return get_flag(connection, flag_id)


def update_flag(
    connection: sqlite3.Connection,
    flag_id: str,
    *,
    text: str | None = None,
    topic: str | None = None,
    status: str | None = None,
    pile_id: str | None = None,
    clear_pile: bool = False,
) -> Flag:
    current = get_flag(connection, flag_id)
    next_status = current.status if status is None else status
    addressed_at = current.addressed_at
    if next_status == "addressed" and current.status != "addressed":
        addressed_at = utc_now()
    elif next_status == "open":
        addressed_at = None
    next_pile = None if clear_pile else (current.pile_id if pile_id is None else pile_id)
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE knowledge_gap_flags SET text = ?, topic = ?, pile_id = ?,"
            " status = ?, updated_at = ?, addressed_at = ? WHERE id = ?",
            (
                current.text if text is None else text,
                current.topic if topic is None else topic,
                next_pile,
                next_status,
                utc_now(),
                addressed_at,
                flag_id,
            ),
        )
    return get_flag(connection, flag_id)


def delete_flag(connection: sqlite3.Connection, flag_id: str) -> None:
    """Deletion is real: the row is gone, not hidden behind a status."""
    with transaction(connection) as tx:
        cursor = tx.execute("DELETE FROM knowledge_gap_flags WHERE id = ?", (flag_id,))
    if cursor.rowcount == 0:
        raise NotFoundError("flag", flag_id)
