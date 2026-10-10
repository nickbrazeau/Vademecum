"""The build schedule's settings and its record of runs (ADR 0018).

Two rows in ``app_state``: the schedule itself, and what the last run did.
The schedule carries ``consent_at``: the moment the owner turned it on,
having read what each run will send. Turning it off clears it. Nothing in
this module starts a build.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..db import transaction
from . import app_state
from .common import utc_now

KEY_SCHEDULE = "build_schedule"
KEY_LAST_RUN = "build_schedule_last_run"
DEFAULT_TIMES = ["07:00", "12:00", "18:00"]
MAX_TIMES = 8
MAX_BATCHES_PER_RUN = 10
_TIME = re.compile(r"\A([01]\d|2[0-3]):([0-5]\d)\Z")


class InvalidSchedule(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_read = app_state.read_dict
_write = app_state.write_json


def normalise_times(times: list[str]) -> list[str]:
    """Distinct `HH:MM` values, sorted, at most MAX_TIMES; refused otherwise."""
    cleaned: list[str] = []
    for value in times:
        text = str(value).strip()
        if not _TIME.match(text):
            raise InvalidSchedule("Times are written as HH:MM on a 24-hour clock, for example 07:00.")
        if text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        raise InvalidSchedule("A schedule needs at least one time of day.")
    if len(cleaned) > MAX_TIMES:
        raise InvalidSchedule(f"At most {MAX_TIMES} times a day.")
    return sorted(cleaned)


def get_schedule(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = _read(connection, KEY_SCHEDULE) or {}
    try:
        times = normalise_times(list(stored.get("times") or DEFAULT_TIMES))
    except InvalidSchedule:
        times = list(DEFAULT_TIMES)
    batches = int(stored.get("batches_per_run") or 3)
    return {
        "enabled": bool(stored.get("enabled", False)),
        "times": times,
        "batches_per_run": max(1, min(batches, MAX_BATCHES_PER_RUN)),
        "consent_at": stored.get("consent_at"),
        # Build whenever the Mac is awake (feedback of 10 October, ADR 0033), rather than
        # at the times above; on unless the owner chose the timer.
        "continuous": bool(stored.get("continuous", True)),
        "paused_until": stored.get("paused_until"),
    }


def set_schedule(
    connection: sqlite3.Connection,
    *,
    enabled: bool,
    times: list[str],
    batches_per_run: int,
    continuous: bool | None = None,
    paused_until: str | None | bool = False,
) -> dict[str, Any]:
    cleaned = normalise_times(times)
    if not 1 <= batches_per_run <= MAX_BATCHES_PER_RUN:
        raise InvalidSchedule(f"Between 1 and {MAX_BATCHES_PER_RUN} batches per pile per run.")
    current = get_schedule(connection)
    consent_at = current["consent_at"] if current["enabled"] else None
    if enabled and consent_at is None:
        consent_at = utc_now()
    if not enabled:
        consent_at = None
    keep_continuous = current["continuous"] if continuous is None else bool(continuous)
    # False means "leave the pause as it is"; None clears it; a timestamp sets it.
    pause = current["paused_until"] if paused_until is False else paused_until
    with transaction(connection) as tx:
        _write(
            tx,
            KEY_SCHEDULE,
            {
                "enabled": enabled,
                "times": cleaned,
                "batches_per_run": batches_per_run,
                "consent_at": consent_at,
                "continuous": keep_continuous,
                "paused_until": pause,
            },
        )
    return get_schedule(connection)


def get_last_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return _read(connection, KEY_LAST_RUN)


def record_run(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        _write(tx, KEY_LAST_RUN, report)
