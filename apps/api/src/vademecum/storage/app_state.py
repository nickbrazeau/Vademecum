"""Small settings and records kept as JSON in app_state, one row per key."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .common import utc_now


def read_json(connection: sqlite3.Connection, key: str) -> Any | None:
    """The value stored under a key, or None when it is absent or unreadable."""
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except ValueError:
        return None


def read_dict(connection: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    data = read_json(connection, key)
    return data if isinstance(data, dict) else None


def write_json(tx: sqlite3.Connection, key: str, data: Any, *, at: str | None = None) -> None:
    """Store a value under a key, inside the caller's transaction."""
    tx.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(data, separators=(",", ":")), at or utc_now()),
    )
