"""The Mac's model connection as last seen (feedback of 5 October).

The phone's copy has no model connection of its own: the assistant in the
conversation is its model. So the Mac records a small summary of its own
connection -- which one, signed in or not, the plan, how much of the allowance
is used and when it resets -- in app_state, which syncs, and the phone shows it
as of when it was seen. No account name, address or token is kept.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import transaction
from .common import utc_now

KEY = "model_seen"


def record(connection: sqlite3.Connection, provider: str, status: Any) -> None:
    limits = getattr(status, "rate_limits", None)

    def window(value: Any) -> dict[str, Any] | None:
        if value is None:
            return None
        return {"used_percent": int(value.used_percent), "resets_at": value.resets_at, "window_minutes": value.window_minutes}

    summary = {
        "provider": provider,
        "state": str(getattr(status, "state", "")),
        "signed_in": bool(getattr(status, "signed_in", False)),
        "plan": getattr(status, "plan", None),
        "primary": window(getattr(limits, "primary", None)) if limits else None,
        "secondary": window(getattr(limits, "secondary", None)) if limits else None,
        "limited": bool(getattr(limits, "limited", False)) if limits else False,
        "seen_at": utc_now(),
    }
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps(summary, separators=(",", ":")), summary["seen_at"]),
        )


def read(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["value"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
