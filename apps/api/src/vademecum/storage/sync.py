"""Two Vademecums that sync (ADR 0015): the change log, and applying a peer's.

One workspace is *domi* (at home): the Mac with the source folder, the originals and
the on-device reading. The other is *foris* (abroad): a copy a phone can reach while
the Mac sleeps. Each keeps a change log (migration 0007, filled by triggers).
Domi initiates: it pulls foris's changes and applies them, then pushes its own.
Foris applies what domi sends. Neither logs what it applied from the other,
which is what stops a change bouncing back and forth.

Conflicts are avoided by ownership rather than resolved by cleverness:

* **Domi-owned** tables -- sources, their text and pictures, builds, learning
  points, questions, evidence -- are made where the files are. Foris may *create* a source (a file from the phone, text-only until home has read
  it) and domi accepts rows it has never seen; every other change to
  these tables flows domi -> foris only.
* **Shared** tables -- piles, flags, notes, Tutor attempts, literature topics,
  map positions, settings -- are written on either side. A row with
  `updated_at` goes to the later write; a row without one is insert-only and
  simply merges. A removal is an explicit act on one side and is carried to
  the other.

Everything here is plain rows. Files (originals, pictures, schematics) are
fetched by name through the caller's `fetch` when a row that names one
arrives and the file is not here yet.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..db import transaction
from .common import utc_now

Role = Literal["domi", "foris"]
# What domi pushes. `full`: everything, files included. `lean` (foris,
# ADR 0017): records only -- no files, no pictures, no schematics, and of
# the text only the passages a learning point or a question cites, so foris
# stays small however large the library on the Mac grows.
Scope = Literal["full", "lean"]
LEAN_SKIPPED: frozenset[str] = frozenset({"source_images", "schematics"})

# Parents before children, so a batch applies in one pass even with foreign
# keys deferred; deletes run in reverse.
SYNCED_TABLES: tuple[str, ...] = (
    "piles",
    "learning_items",
    "knowledge_gap_flags",
    "curated_articles",
    "sources",
    "source_segments",
    "source_images",
    "generation_runs",
    "build_batches",
    "learning_points",
    "learning_point_sources",
    "learning_point_topics",
    "schematics",
    "tutor_questions",
    "tutor_question_anchors",
    "tutor_cycle_entries",
    "tutor_attempts",
    "literature_topics",
    "literature_checks",
    "literature_records",
    "literature_topic_records",
    "evidence_links",
    "app_state",
    "topic_specialties",
    "map_positions",
    "exam_reports",
    "exam_areas",
    "case_entries",
    "encyclopedia_entries",
    "board_questions",
    "board_cycle_entries",
    "board_attempts",
    "encyclopedia_records",
    "flashcards",
    "flashcard_reviews",
    "socratic_sessions",
    "podcast_episodes",
    "review_events",
    "podcast_listens",
)

DOMI_OWNED: frozenset[str] = frozenset(
    {
        "sources",
        "source_segments",
        "source_images",
        "generation_runs",
        "build_batches",
        "learning_points",
        "learning_point_sources",
        "learning_point_topics",
        "tutor_questions",
        "tutor_question_anchors",
        "evidence_links",
        "literature_checks",
        "literature_records",
        "literature_topic_records",
        "curated_articles",
        "exam_reports",
        "exam_areas",
        "case_entries",
        "encyclopedia_entries",
        "board_questions",
        "encyclopedia_records",
        "flashcards",
        "podcast_episodes",
    }
)

# Tables whose rows name a stored file, and the directory kind it lives in.
FILE_COLUMNS: dict[str, tuple[str, str]] = {
    "sources": ("stored_name", "sources"),
    "source_images": ("stored_name", "images"),
    "schematics": ("stored_name", "schematics"),
}

MAX_BATCH = 500


@dataclass(frozen=True)
class Change:
    seq: int
    table: str
    key: list[Any]
    op: str  # upsert | delete
    row: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return {"seq": self.seq, "table": self.table, "key": self.key, "op": self.op, "row": self.row}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Change":
        table = str(data["table"])
        if table not in SYNCED_TABLES:
            raise ValueError(f"not a synced table: {table}")
        op = str(data["op"])
        if op not in ("upsert", "delete"):
            raise ValueError(f"not a change: {op}")
        key = data["key"]
        if not isinstance(key, list) or not key:
            raise ValueError("a change names a row")
        row = data.get("row")
        if op == "upsert" and not isinstance(row, dict):
            raise ValueError("an upsert carries its row")
        return cls(int(data["seq"]), table, key, op, row if op == "upsert" else None)


class _Dangling(Exception):
    """A batch that cannot stand on its own; rolled back, retried whole."""


@dataclass(frozen=True)
class Applied:
    applied: int
    skipped: int
    deferred: int
    through: int

    def as_dict(self) -> dict[str, int]:
        return {"applied": self.applied, "skipped": self.skipped, "deferred": self.deferred, "through": self.through}


# --- this node -----------------------------------------------------------------


def node_id(connection: sqlite3.Connection) -> str:
    return str(connection.execute("SELECT node_id FROM sync_state WHERE id = 1").fetchone()[0])


def state(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM sync_state WHERE id = 1").fetchone()
    data = {key: row[key] for key in row.keys()}
    data["log_length"] = int(connection.execute("SELECT COALESCE(MAX(seq), 0) FROM sync_log").fetchone()[0])
    del data["applying"]
    del data["id"]
    return data


RECEIVED_KEY = "sync_received_through"


def received_through(connection: sqlite3.Connection, peer_node_id: str) -> int | None:
    """What this node holds of a peer's log, as its own records say (restored with them)."""
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (RECEIVED_KEY,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["value"])
    except ValueError:
        return None
    value = data.get(peer_node_id) if isinstance(data, dict) else None
    return int(value) if isinstance(value, int) else None


def record_received(connection: sqlite3.Connection, peer_node_id: str, through: int) -> None:
    """Kept in app_state, in the same database the rows are in, so a node restored from an
    older copy of itself also says it holds less -- and the sender sends the rest again."""
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (RECEIVED_KEY,)).fetchone()
    try:
        data = json.loads(row["value"]) if row else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[peer_node_id] = max(int(through), int(data.get(peer_node_id) or 0)) if isinstance(data.get(peer_node_id), int) else int(through)
    connection.execute("UPDATE sync_state SET applying = 1 WHERE id = 1")
    try:
        connection.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (RECEIVED_KEY, json.dumps(data), utc_now()),
        )
    finally:
        connection.execute("UPDATE sync_state SET applying = 0 WHERE id = 1")
    connection.commit()


def record_sync(
    connection: sqlite3.Connection,
    *,
    peer_node_id: str | None = None,
    pulled_through: int | None = None,
    pushed_through: int | None = None,
    note: str = "",
) -> None:
    sets = ["last_sync_at = ?", "last_sync_note = ?"]
    values: list[Any] = [utc_now(), note[:200]]
    if peer_node_id is not None:
        sets.append("peer_node_id = ?")
        values.append(peer_node_id)
    if pulled_through is not None:
        sets.append("pulled_through = ?")
        values.append(pulled_through)
    if pushed_through is not None:
        sets.append("pushed_through = ?")
        values.append(pushed_through)
    with transaction(connection) as tx:
        tx.execute(f"UPDATE sync_state SET {', '.join(sets)} WHERE id = 1", values)


# --- the change log, read ------------------------------------------------------


def _pk_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in sorted((r for r in rows if r["pk"]), key=lambda r: r["pk"])]


def _columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in connection.execute(f"PRAGMA table_info({table})").fetchall()]


def _where(pks: list[str]) -> str:
    return " AND ".join(f"{column} = ?" for column in pks)


def _load(connection: sqlite3.Connection, table: str, key: list[Any]) -> dict[str, Any] | None:
    pks = _pk_columns(connection, table)
    row = connection.execute(f"SELECT * FROM {table} WHERE {_where(pks)}", key).fetchone()
    return None if row is None else {column: row[column] for column in row.keys()}


def _cited(connection: sqlite3.Connection, segment_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM learning_point_sources WHERE segment_id = ?"
        " UNION SELECT 1 FROM tutor_question_anchors WHERE segment_id = ? LIMIT 1",
        (segment_id, segment_id),
    ).fetchone()
    return row is not None


def lean(connection: sqlite3.Connection, changes: list[Change]) -> list[Change]:
    """The lean scope applied to a batch: drop what foris does not need, and
    carry along any cited passage a citation in the batch depends on, so no
    citation ever arrives before its text."""
    kept: list[Change] = []
    present = {(c.table, json.dumps(c.key)) for c in changes}
    for change in changes:
        if change.table in LEAN_SKIPPED:
            continue
        if change.table == "source_segments" and change.op == "upsert" and not _cited(connection, change.key[0]):
            continue
        kept.append(change)
        if change.op == "upsert" and change.table in ("learning_point_sources", "tutor_question_anchors"):
            segment_id = (change.row or {}).get("segment_id")
            if isinstance(segment_id, str) and segment_id and ("source_segments", json.dumps([segment_id])) not in present:
                row = _load(connection, "source_segments", [segment_id])
                if row is not None:
                    present.add(("source_segments", json.dumps([segment_id])))
                    kept.append(Change(change.seq, "source_segments", [segment_id], "upsert", row))
    kept.sort(key=lambda change: change.seq)
    return kept


def changes_since(
    connection: sqlite3.Connection, since: int, *, limit: int = MAX_BATCH, scope: Scope = "full"
) -> tuple[list[Change], int, bool]:
    """Changes after *since*, one per row, each carrying the row as it is now.

    Returns ``(changes, through, done)``: ``through`` is the last log sequence
    covered, which the peer records as its cursor once it has applied them.
    In the lean scope the batch is filtered (see ``lean``); ``through`` still
    covers every log entry read, filtered or not.
    """
    limit = max(1, min(limit, MAX_BATCH))
    rows = connection.execute(
        "SELECT seq, table_name, row_key, op FROM sync_log WHERE seq > ? ORDER BY seq LIMIT ?",
        (since, limit + 1),
    ).fetchall()
    done = len(rows) <= limit
    rows = rows[:limit]
    if not rows:
        return [], since, True
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        latest[(row["table_name"], row["row_key"])] = row
    changes: list[Change] = []
    for (table, row_key), entry in latest.items():
        key = json.loads(row_key)
        current = _load(connection, table, key)
        if entry["op"] == "delete" or current is None:
            changes.append(Change(int(entry["seq"]), table, key, "delete", None))
        else:
            changes.append(Change(int(entry["seq"]), table, key, "upsert", current))
    changes.sort(key=lambda change: change.seq)
    if scope == "lean":
        changes = lean(connection, changes)
    return changes, int(rows[-1]["seq"]), done


# --- a peer's changes, applied ---------------------------------------------------


def _wins(local: dict[str, Any] | None, incoming: dict[str, Any]) -> bool:
    """Whether *incoming* replaces *local*: later `updated_at` wins; absent, incoming does."""
    if local is None:
        return True
    theirs = incoming.get("updated_at")
    mine = local.get("updated_at")
    if theirs is None or mine is None:
        return True
    return str(theirs) >= str(mine)


def _file_present(directories: dict[str, Path], kind: str, name: str) -> bool:
    directory = directories.get(kind)
    return directory is not None and (directory / name).is_file()


def store_file(directories: dict[str, Path], kind: str, name: str, data: bytes) -> None:
    directory = directories[kind]
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / name
    tmp = target.with_name(f".{name}.part")
    tmp.write_bytes(data)
    tmp.chmod(0o600)
    tmp.replace(target)


def apply_changes(
    connection: sqlite3.Connection,
    changes: list[Change],
    *,
    role: Role,
    directories: dict[str, Path] | None = None,
    fetch: Callable[[str, str], bytes | None] | None = None,
    require_files: bool = True,
) -> Applied:
    """Apply a peer's changes under this node's role. One transaction.

    A row that names a file this node does not have is *deferred* (not
    applied, not counted as done) when the file cannot be fetched now; the
    cursor does not advance past it, so the next sync tries again. With
    ``require_files`` off (the lean scope) rows are taken without their files.
    """
    directories = directories or {}
    applied = skipped = 0
    deferred_seqs: list[int] = []
    ordered = sorted(
        changes,
        key=lambda change: (
            change.op == "delete",
            SYNCED_TABLES.index(change.table) * (-1 if change.op == "delete" else 1),
            change.seq,
        ),
    )
    try:
        with transaction(connection) as tx:
            tx.execute("UPDATE sync_state SET applying = 1 WHERE id = 1")
            tx.execute("PRAGMA defer_foreign_keys = ON")
            try:
                for change in ordered:
                    outcome = _apply_one(tx, change, role=role, directories=directories, fetch=fetch, require_files=require_files)
                    if outcome == "applied":
                        applied += 1
                    elif outcome == "deferred":
                        deferred_seqs.append(change.seq)
                    else:
                        skipped += 1
                if deferred_seqs and tx.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    # A deferred parent left children dangling: nothing from
                    # this batch is kept, and the whole of it is tried again.
                    raise _Dangling()
                if role == "domi":
                    # Pulling from foris: a row of foris's own that points at a
                    # parent domi does not hold (foris's copy of a paper domi
                    # already had, skipped as a duplicate) is foris's bookkeeping,
                    # not the owner's work. It is dropped rather than refusing
                    # the batch (ADR 0026). Foris, receiving, stays strict: domi
                    # sends the parents again.
                    for row in tx.execute("PRAGMA foreign_key_check").fetchall():
                        tx.execute(f"DELETE FROM {row[0]} WHERE rowid = ?", (row[1],))
                        skipped += 1
                        applied = max(0, applied - 1)
            finally:
                tx.execute("UPDATE sync_state SET applying = 0 WHERE id = 1")
    except _Dangling:
        return Applied(0, 0, len(changes), min(change.seq for change in changes) - 1)
    # The cursor stops short of the first deferred change, so the next sync
    # sees it again; anything after it that did apply is simply re-sent and
    # re-applied, which is harmless.
    if deferred_seqs:
        through = min(deferred_seqs) - 1
    else:
        through = max((change.seq for change in changes), default=0)
    return Applied(applied, skipped, len(deferred_seqs), through)


def _unique_columns(message: str, table: str) -> list[str]:
    """The columns sqlite names in 'UNIQUE constraint failed: t.a, t.b'."""
    named = message.split(":", 1)[-1]
    return [part.strip().split(".", 1)[1] for part in named.split(",") if part.strip().startswith(f"{table}.")]


def _apply_one(
    tx: sqlite3.Connection,
    change: Change,
    *,
    role: Role,
    directories: dict[str, Path],
    fetch: Callable[[str, str], bytes | None] | None,
    require_files: bool = True,
) -> str:
    local = _load(tx, change.table, change.key)
    if role == "domi" and change.table in DOMI_OWNED:
        # Domi makes these. Foris may only hand over a row domi has never
        # seen (a file taken in from the phone); the rest is domi's to
        # overwrite.
        if change.op != "upsert" or local is not None:
            return "skipped"
        # A page the owner deleted is not brought back by a stale copy (feedback of 6 October).
        if change.table == "encyclopedia_entries" and change.row is not None:
            from .encyclopedia import deleted_pages

            if str(change.row.get("topic") or "").casefold() in deleted_pages(tx):
                return "skipped"
    if change.op == "delete":
        if local is None:
            return "skipped"
        pks = _pk_columns(tx, change.table)
        tx.execute(f"DELETE FROM {change.table} WHERE {_where(pks)}", change.key)
        return "applied"
    assert change.row is not None
    if not _wins(local, change.row):
        return "skipped"
    file_column = FILE_COLUMNS.get(change.table) if require_files else None
    if file_column is not None:
        column, kind = file_column
        name = change.row.get(column)
        if isinstance(name, str) and name and not _file_present(directories, kind, name):
            data = fetch(kind, name) if fetch is not None else None
            if data is None or kind not in directories:
                return "deferred"
            digest = change.row.get("sha256")
            if isinstance(digest, str) and digest and hashlib.sha256(data).hexdigest() != digest:
                return "deferred"
            store_file(directories, kind, name, data)
    columns = [column for column in _columns(tx, change.table) if column in change.row]
    pks = _pk_columns(tx, change.table)
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column not in pks)
    conflict = f"ON CONFLICT({', '.join(pks)}) DO UPDATE SET {updates}" if updates else f"ON CONFLICT({', '.join(pks)}) DO NOTHING"
    # A row that duplicates one this node already holds under another id (both
    # nodes fetched the same paper, by DOI or PMID) is the same fact twice: it is
    # skipped, and the batch goes on (ADR 0026). Foreign keys are deferred, so
    # only a unique key can fail here.
    tx.execute("SAVEPOINT sync_row")
    try:
        tx.execute(
            f"INSERT INTO {change.table} ({', '.join(columns)}) VALUES ({placeholders}) {conflict}",
            [change.row[column] for column in columns],
        )
    except sqlite3.IntegrityError as exc:
        tx.execute("ROLLBACK TO sync_row")
        message = str(exc)
        if "UNIQUE" not in message:
            tx.execute("RELEASE sync_row")
            raise
        if role == "foris" and change.table in DOMI_OWNED:
            # Domi's row is the owner's: foris's own copy of the same thing (a paper
            # it fetched itself) gives way, and what pointed at it follows the cascade.
            for column in _unique_columns(message, change.table):
                value = change.row.get(column)
                if value is not None:
                    tx.execute(f"DELETE FROM {change.table} WHERE {column} = ? AND NOT ({_where(pks)})", [value, *change.key])
            tx.execute(
                f"INSERT INTO {change.table} ({', '.join(columns)}) VALUES ({placeholders}) {conflict}",
                [change.row[column] for column in columns],
            )
            tx.execute("RELEASE sync_row")
            return "applied"
        tx.execute("RELEASE sync_row")
        return "skipped"
    tx.execute("RELEASE sync_row")
    return "applied"


def file_kinds(source_dir: Path) -> dict[str, Path]:
    """Where each kind of stored file lives, from the originals directory."""
    attachments = source_dir.parent
    return {"sources": source_dir, "images": attachments / "images", "schematics": attachments / "schematics"}
