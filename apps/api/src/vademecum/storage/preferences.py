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
    {"name": "podcasts", "label": "Podcast", "fixed": False},
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


DEFAULT_DAILY_GOAL = 20
MAX_DAILY_GOAL = 200


def daily_goal(connection: sqlite3.Connection) -> int:
    """How many reviews make a day's review enough: the owner's number (ADR 0026)."""
    value = _read(connection).get("daily_goal", DEFAULT_DAILY_GOAL)
    try:
        return max(1, min(int(value), MAX_DAILY_GOAL))
    except (TypeError, ValueError):
        return DEFAULT_DAILY_GOAL


def set_daily_goal(connection: sqlite3.Connection, goal: int) -> dict[str, Any]:
    stored = _read(connection)
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps({**stored, "daily_goal": max(1, min(int(goal), MAX_DAILY_GOAL))}, separators=(",", ":")), utc_now()),
        )
    return get_preferences(connection)


PODCAST_SPEEDS = (1.0, 1.25, 1.5, 1.75, 2.0)


def podcast_speed(connection: sqlite3.Connection) -> float:
    """The owner's listening speed for episodes, shared by every device (ADR 0027)."""
    try:
        value = float(_read(connection).get("podcast_speed", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return value if value in PODCAST_SPEEDS else 1.0


def set_podcast_speed(connection: sqlite3.Connection, speed: float) -> dict[str, Any]:
    if float(speed) not in PODCAST_SPEEDS:
        raise ValueError("speed")
    stored = _read(connection)
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps({**stored, "podcast_speed": float(speed)}, separators=(",", ":")), utc_now()),
        )
    return get_preferences(connection)


def ordered(names: list[str] | None) -> list[str]:
    """The owner's order of the tabs: Today first and Settings last always; any tab
    not in their order (a new one) goes where the catalogue puts it, before Settings."""
    middle = [name for name in TAB_NAMES if name not in FIXED]
    chosen = [str(name) for name in (names or []) if str(name) in middle]
    rest = [name for name in middle if name not in chosen]
    return ["today", *dict.fromkeys(chosen), *rest, "settings"]


def get_preferences(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = _read(connection)
    order = ordered(stored.get("order") if isinstance(stored.get("order"), list) else None)
    chosen = stored.get("visible_tabs")
    if not isinstance(chosen, list):
        visible = list(order)
    else:
        wanted = {str(name) for name in chosen}
        # A tab added after the owner chose is shown until they choose again.
        known = set(stored.get("known_tabs") or [name for name in TAB_NAMES if name != "construction"])
        visible = [name for name in order if name in wanted or name in FIXED or name not in known]
    by_name = {tab["name"]: tab for tab in TABS}
    return {
        "visible_tabs": visible,
        "order": order,
        "tabs": [dict(by_name[name]) for name in order],
        "daily_goal": daily_goal(connection),
        "podcast_speed": podcast_speed(connection),
    }


def set_visible_tabs(connection: sqlite3.Connection, names: list[str], order: list[str] | None = None) -> dict[str, Any]:
    stored = _read(connection)
    new_order = ordered(order if order is not None else (stored.get("order") if isinstance(stored.get("order"), list) else None))
    wanted = {str(name) for name in names} | set(FIXED)
    visible = [name for name in new_order if name in wanted]
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY, json.dumps({**stored, "visible_tabs": visible, "order": new_order, "known_tabs": list(TAB_NAMES)}, separators=(",", ":")), utc_now()),
        )
    return get_preferences(connection)
