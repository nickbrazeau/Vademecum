"""Tiered Piles and the learning items inside them."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Literal, get_args

from ..db import transaction
from .common import NotFoundError, content_hash, new_id, utc_now
from .sources import ConflictError, Coverage

Tier = Literal["low", "mid", "high"]

# Source confidence, lowest to highest. This is how much the owner trusts the
# material they filed here -- not mastery, priority, difficulty, or whether a
# claim has been verified. The UI labels `mid` as "Medium"; the stored value is
# unchanged so every row written before source intake still reads.
TIERS: tuple[str, ...] = get_args(Tier)


@dataclass(frozen=True)
class Pile:
    id: str
    title: str
    tier: str
    description: str
    created_at: str
    updated_at: str
    item_count: int = 0
    source_count: int = 0
    point_count: int = 0
    question_count: int = 0
    segments_total: int = 0
    segments_covered: int = 0
    chars_total: int = 0
    chars_covered: int = 0

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("segments_total")
        data.pop("segments_covered")
        data.pop("chars_total")
        data.pop("chars_covered")
        data["confidence_label"] = {"low": "Low", "mid": "Medium", "high": "High"}[self.tier]
        data["coverage"] = {
            **Coverage(self.chars_total, self.chars_covered).as_dict(),
            "segments_total": self.segments_total,
            "segments_covered": self.segments_covered,
        }
        return data


@dataclass(frozen=True)
class LearningItem:
    id: str
    pile_id: str
    title: str
    body: str
    source: str
    content_hash: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pile(row: sqlite3.Row) -> Pile:
    keys = row.keys()

    def value(name: str) -> int:
        return row[name] if name in keys and row[name] is not None else 0

    return Pile(
        id=row["id"],
        title=row["title"],
        tier=row["tier"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        item_count=value("item_count"),
        source_count=value("source_count"),
        point_count=value("point_count"),
        question_count=value("question_count"),
        segments_total=value("segments_total"),
        segments_covered=value("segments_covered"),
        chars_total=value("chars_total"),
        chars_covered=value("chars_covered"),
    )


def _item(row: sqlite3.Row) -> LearningItem:
    return LearningItem(
        id=row["id"],
        pile_id=row["pile_id"],
        title=row["title"],
        body=row["body"],
        source=row["source"],
        content_hash=row["content_hash"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_PILE_SELECT = """
SELECT p.id, p.title, p.tier, p.description, p.created_at, p.updated_at,
       (SELECT COUNT(*) FROM learning_items i WHERE i.pile_id = p.id) AS item_count,
       (SELECT COUNT(*) FROM sources s WHERE s.pile_id = p.id) AS source_count,
       (SELECT COUNT(*) FROM learning_points l WHERE l.pile_id = p.id) AS point_count,
       (SELECT COUNT(*) FROM tutor_questions q
         WHERE q.pile_id = p.id AND q.retired_at IS NULL) AS question_count,
       (SELECT COUNT(*) FROM source_segments g JOIN sources s2 ON s2.id = g.source_id
         WHERE s2.pile_id = p.id AND s2.excluded = 0
           AND s2.status = 'extracted') AS segments_total,
       (SELECT COUNT(*) FROM source_segments g JOIN sources s3 ON s3.id = g.source_id
         WHERE s3.pile_id = p.id AND s3.excluded = 0 AND s3.status = 'extracted'
           AND g.covered_at IS NOT NULL) AS segments_covered,
       (SELECT COALESCE(SUM(g.char_count), 0)
          FROM source_segments g JOIN sources s4 ON s4.id = g.source_id
         WHERE s4.pile_id = p.id AND s4.excluded = 0 AND s4.status = 'extracted') AS chars_total,
       (SELECT COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0)
          FROM source_segments g JOIN sources s5 ON s5.id = g.source_id
         WHERE s5.pile_id = p.id AND s5.excluded = 0 AND s5.status = 'extracted') AS chars_covered
FROM piles p
"""


def list_piles(connection: sqlite3.Connection, tier: str | None = None) -> list[Pile]:
    if tier is None:
        rows = connection.execute(
            _PILE_SELECT + " ORDER BY p.created_at DESC"
        ).fetchall()
    else:
        rows = connection.execute(
            _PILE_SELECT + " WHERE p.tier = ? ORDER BY p.created_at DESC", (tier,)
        ).fetchall()
    return [_pile(row) for row in rows]


def get_pile(connection: sqlite3.Connection, pile_id: str) -> Pile:
    row = connection.execute(_PILE_SELECT + " WHERE p.id = ?", (pile_id,)).fetchone()
    if row is None:
        raise NotFoundError("pile", pile_id)
    return _pile(row)


def create_pile(
    connection: sqlite3.Connection, *, title: str, tier: str, description: str = ""
) -> Pile:
    now = utc_now()
    pile_id = new_id("pil")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO piles (id, title, tier, description, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (pile_id, title, tier, description, now, now),
        )
    return get_pile(connection, pile_id)


def update_pile(
    connection: sqlite3.Connection,
    pile_id: str,
    *,
    title: str | None = None,
    tier: str | None = None,
    description: str | None = None,
) -> Pile:
    current = get_pile(connection, pile_id)
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE piles SET title = ?, tier = ?, description = ?, updated_at = ?"
            " WHERE id = ?",
            (
                current.title if title is None else title,
                current.tier if tier is None else tier,
                current.description if description is None else description,
                utc_now(),
                pile_id,
            ),
        )
    return get_pile(connection, pile_id)


def delete_pile(connection: sqlite3.Connection, pile_id: str) -> None:
    """Delete a pile, refusing while it holds uploads or generated material.

    Deleting a pile used to cascade. It must not: uploaded originals and answer
    history are the two things in Vademecum that cannot be regenerated, and a
    single confirm-less DELETE is not consent to lose either. Plain-text
    learning items still cascade, which is the behaviour that predates source
    intake and the only thing this pile ever held.
    """
    get_pile(connection, pile_id)
    blocking = connection.execute(
        "SELECT (SELECT COUNT(*) FROM sources WHERE pile_id = ?) AS sources,"
        " (SELECT COUNT(*) FROM learning_points WHERE pile_id = ?) AS points,"
        " (SELECT COUNT(*) FROM tutor_questions WHERE pile_id = ?) AS questions",
        (pile_id, pile_id, pile_id),
    ).fetchone()
    parts = [
        f"{blocking[name]} {label}"
        for name, label in (
            ("sources", "uploaded source(s)"),
            ("points", "learning point(s)"),
            ("questions", "question(s)"),
        )
        if blocking[name]
    ]
    if parts:
        raise ConflictError(
            "pile_in_use",
            "This pile still holds " + ", ".join(parts) + ". Nothing was deleted. "
            "Remove the sources and the generated material first, so you decide "
            "explicitly what goes.",
        )
    with transaction(connection) as tx:
        cursor = tx.execute("DELETE FROM piles WHERE id = ?", (pile_id,))
    if cursor.rowcount == 0:
        raise NotFoundError("pile", pile_id)


def list_items(connection: sqlite3.Connection, pile_id: str) -> list[LearningItem]:
    get_pile(connection, pile_id)  # 404 rather than a silently empty list
    rows = connection.execute(
        "SELECT * FROM learning_items WHERE pile_id = ? ORDER BY created_at DESC",
        (pile_id,),
    ).fetchall()
    return [_item(row) for row in rows]


def get_item(connection: sqlite3.Connection, item_id: str) -> LearningItem:
    row = connection.execute(
        "SELECT * FROM learning_items WHERE id = ?", (item_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("learning item", item_id)
    return _item(row)


def create_item(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    title: str,
    body: str = "",
    source: str = "",
) -> LearningItem:
    get_pile(connection, pile_id)
    now = utc_now()
    item_id = new_id("itm")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO learning_items"
            " (id, pile_id, title, body, source, content_hash, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                pile_id,
                title,
                body,
                source,
                content_hash(title, body, source),
                now,
                now,
            ),
        )
    return get_item(connection, item_id)


def update_item(
    connection: sqlite3.Connection,
    item_id: str,
    *,
    title: str | None = None,
    body: str | None = None,
    source: str | None = None,
    pile_id: str | None = None,
) -> LearningItem:
    current = get_item(connection, item_id)
    if pile_id is not None:
        get_pile(connection, pile_id)
    next_title = current.title if title is None else title
    next_body = current.body if body is None else body
    next_source = current.source if source is None else source
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE learning_items SET pile_id = ?, title = ?, body = ?, source = ?,"
            " content_hash = ?, updated_at = ? WHERE id = ?",
            (
                current.pile_id if pile_id is None else pile_id,
                next_title,
                next_body,
                next_source,
                content_hash(next_title, next_body, next_source),
                utc_now(),
                item_id,
            ),
        )
    return get_item(connection, item_id)


def delete_item(connection: sqlite3.Connection, item_id: str) -> None:
    with transaction(connection) as tx:
        cursor = tx.execute("DELETE FROM learning_items WHERE id = ?", (item_id,))
    if cursor.rowcount == 0:
        raise NotFoundError("learning item", item_id)
