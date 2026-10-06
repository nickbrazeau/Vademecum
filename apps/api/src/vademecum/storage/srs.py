"""Spaced repetition for flashcards (feedback of 5 October).

A card's schedule is read from its own review history, so it needs no table of its
own and follows the reviews wherever they sync. The rule is SM-2 with two answers:
"Got it" lengthens the gap (one day, then three, then the last gap times the card's
ease); "Again" brings the card back in ten minutes and makes it a little harder,
lowering its ease. Cards ready now come first, most overdue first; then a few new
cards a day; then nothing, until the next one is ready, unless the owner asks to keep
practising.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

START_EASE = 2.5
MIN_EASE = 1.3
EASE_STEP = 0.2
RELEARN_MINUTES = 10
NEW_PER_DAY = 20


@dataclass(frozen=True)
class Schedule:
    reps: int  # "Got it" in a row since the last "Again"
    ease: float
    interval_days: float
    last_at: datetime | None
    due_at: datetime | None  # None: never reviewed, a new card
    lapses: int

    @property
    def is_new(self) -> bool:
        return self.last_at is None

    def as_dict(self) -> dict[str, object]:
        return {
            "state": "new" if self.is_new else ("relearning" if self.reps == 0 else "review"),
            "reps": self.reps,
            "ease": round(self.ease, 2),
            "interval_days": round(self.interval_days, 2),
            "due_at": None if self.due_at is None else self.due_at.isoformat().replace("+00:00", "Z"),
            "lapses": self.lapses,
        }


def parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def step(schedule: Schedule, rating: str, at: datetime) -> Schedule:
    """The schedule after one answer."""
    if rating == "again":
        return Schedule(
            reps=0,
            ease=max(MIN_EASE, schedule.ease - EASE_STEP),
            interval_days=0.0,
            last_at=at,
            due_at=at + timedelta(minutes=RELEARN_MINUTES),
            lapses=schedule.lapses + (0 if schedule.is_new else 1),
        )
    reps = schedule.reps + 1
    if reps == 1:
        interval = 1.0
    elif reps == 2:
        interval = 3.0
    else:
        interval = max(schedule.interval_days * schedule.ease, schedule.interval_days + 1)
    return Schedule(reps=reps, ease=schedule.ease, interval_days=interval, last_at=at, due_at=at + timedelta(days=interval), lapses=schedule.lapses)


NEW = Schedule(reps=0, ease=START_EASE, interval_days=0.0, last_at=None, due_at=None, lapses=0)


def schedules(connection: sqlite3.Connection) -> dict[str, Schedule]:
    """Every reviewed card's schedule, replayed from its answers in order."""
    found: dict[str, Schedule] = {}
    for row in connection.execute(
        "SELECT card_id, rating, created_at FROM flashcard_reviews WHERE card_id IS NOT NULL ORDER BY created_at, id"
    ):
        try:
            at = parse(row["created_at"])
        except ValueError:
            continue
        found[row["card_id"]] = step(found.get(row["card_id"], NEW), row["rating"], at)
    return found


def preview(schedule: Schedule, now: datetime) -> dict[str, str]:
    """What each answer would do, said plainly, for the buttons."""
    return {rating: gap_label(step(schedule, rating, now).due_at - now) for rating in ("again", "good")}  # type: ignore[operator]


def gap_label(gap: timedelta) -> str:
    minutes = gap.total_seconds() / 60
    if minutes < 60:
        return f"{max(1, round(minutes))} min"
    hours = minutes / 60
    if hours < 24:
        return f"{round(hours)} h"
    days = hours / 24
    if days < 30:
        return f"{round(days)} day{'s' if round(days) != 1 else ''}"
    months = days / 30
    return f"{round(months, 1):g} month{'s' if round(months, 1) != 1 else ''}"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_started_today(connection: sqlite3.Connection, now: datetime) -> int:
    """Cards whose first answer was today, by the owner's own clock."""
    local_midnight = now.astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM (SELECT card_id, MIN(created_at) AS first FROM flashcard_reviews WHERE card_id IS NOT NULL GROUP BY card_id)"
        " WHERE first >= ?",
        (local_midnight.isoformat().replace("+00:00", "Z"),),
    ).fetchone()
    return int(row["n"] or 0)
