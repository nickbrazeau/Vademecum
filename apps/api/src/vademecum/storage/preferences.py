"""The owner's preferences (ADR 0024): which tabs the web app shows.

One row in ``app_state``, synced like the rest, so the choice follows the
owner from the Mac to the phone. Today and Settings are always shown: a
learner can never hide the way back.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..db import transaction
from .common import utc_now

KEY = "preferences"

TABS: tuple[dict[str, Any], ...] = (
    {"name": "today", "label": "Today", "fixed": True},
    {"name": "tutor", "label": "Tutor", "fixed": False},
    {"name": "flashcards", "label": "Flashcards", "fixed": False},
    {"name": "encyclopedia", "label": "Encyclopedia", "fixed": False},
    {"name": "map", "label": "Improvement Map", "fixed": False},
    {"name": "podcasts", "label": "Podcast Generator", "fixed": False},
    {"name": "construction", "label": "Construction", "fixed": False},
    {"name": "sources", "label": "Sources", "fixed": False},
    {"name": "settings", "label": "Settings", "fixed": True},
)
TAB_NAMES = tuple(tab["name"] for tab in TABS)
FIXED = tuple(tab["name"] for tab in TABS if tab["fixed"])


def _read(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY,)).fetchone()
    if row is None:
        return {}
    try:
        data = json.loads(row["value"])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def get_preferences(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = _read(connection)
    chosen = stored.get("visible_tabs")
    if not isinstance(chosen, list):
        visible = list(TAB_NAMES)
    else:
        wanted = {str(name) for name in chosen}
        # A tab added after the owner chose is shown until they choose again.
        known = set(stored.get("known_tabs") or [name for name in TAB_NAMES if name != "construction"])
        visible = [name for name in TAB_NAMES if name in wanted or name in FIXED or name not in known]
    return {"visible_tabs": visible, "tabs": [dict(tab) for tab in TABS]}


def set_visible_tabs(connection: sqlite3.Connection, names: list[str]) -> dict[str, Any]:
    wanted = {str(name) for name in names} | set(FIXED)
    visible = [name for name in TAB_NAMES if name in wanted]
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps({**_read(connection), "visible_tabs": visible, "known_tabs": list(TAB_NAMES)}, separators=(",", ":")), utc_now()),
        )
    return get_preferences(connection)
