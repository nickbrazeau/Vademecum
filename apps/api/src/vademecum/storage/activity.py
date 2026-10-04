"""What the owner reviewed, and when (ADR 0026): the dashboard on Today and the Tutor's scorecard.

Read from what is already recorded -- board answers, flashcard reviews, open
answers graded, Socratic sessions -- plus the pages the owner marks reviewed.
Days are the owner's local days. "Days in a row" was asked for by the owner;
it counts what happened and owes nothing: there is no due count, no target,
and a missed day costs nothing but the number.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from ..db import transaction
from .common import new_id, utc_now

KINDS = ("page",)
HISTORY_DAYS = 14


def record(connection: sqlite3.Connection, kind: str, ref_id: str = "") -> dict[str, Any]:
    if kind not in KINDS:
        raise ValueError("kind")
    event_id = new_id("rev")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("INSERT INTO review_events (id, kind, ref_id, created_at) VALUES (?, ?, ?, ?)", (event_id, kind, ref_id[:64], now))
    return {"id": event_id, "kind": kind, "ref_id": ref_id, "created_at": now}


def _local_day(stamp: str | None) -> date | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone().date()


def _events(connection: sqlite3.Connection) -> list[tuple[str, date]]:
    """Every review, as (kind, local day)."""
    sources = (
        ("question", "SELECT created_at FROM board_attempts"),
        ("question", "SELECT created_at FROM tutor_attempts"),
        ("card", "SELECT created_at FROM flashcard_reviews"),
        ("socratic", "SELECT COALESCE(finished_at, updated_at) AS created_at FROM socratic_sessions WHERE exchanges > 0"),
        ("page", "SELECT created_at FROM review_events WHERE kind = 'page'"),
    )
    found: list[tuple[str, date]] = []
    for kind, sql in sources:
        for row in connection.execute(sql).fetchall():
            day = _local_day(row["created_at"])
            if day is not None:
                found.append((kind, day))
    return found


def _runs(days: set[date], today: date) -> tuple[int, int]:
    """The current run of consecutive days (today, or up to yesterday if today has none yet) and the longest."""
    current = 0
    cursor = today if today in days else today - timedelta(days=1)
    while cursor in days:
        current += 1
        cursor -= timedelta(days=1)
    longest = 0
    run = 0
    previous: date | None = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    return current, longest


def dashboard(connection: sqlite3.Connection, *, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now().astimezone().date()
    events = _events(connection)
    days = {day for _, day in events}
    current, longest = _runs(days, today)
    week_start = today - timedelta(days=6)

    def count(since: date, kind: str | None = None) -> int:
        return sum(1 for k, day in events if day >= since and day <= today and (kind is None or k == kind))

    return {
        "days_in_a_row": current,
        "longest_run": longest,
        "reviewed_today_already": today in days,
        "today": {kind: count(today, kind) for kind in ("question", "card", "socratic", "page")},
        "today_total": count(today),
        "week_total": count(week_start),
        "all_time_total": len(events),
        "history": [
            {"day": (today - timedelta(days=offset)).isoformat(), "count": sum(1 for _, d in events if d == today - timedelta(days=offset))}
            for offset in range(HISTORY_DAYS - 1, -1, -1)
        ],
    }


def scorecard(connection: sqlite3.Connection) -> dict[str, Any]:
    """The Tutor's scorecard: board questions, open answers, flashcards and Socratic sessions."""
    week_ago = (datetime.now().astimezone() - timedelta(days=7)).astimezone().isoformat()

    def board(where: str = "", params: tuple = ()) -> dict[str, int]:
        row = connection.execute(f"SELECT COUNT(*) AS n, COALESCE(SUM(correct), 0) AS right FROM board_attempts {where}", params).fetchone()
        return {"answered": int(row["n"]), "correct": int(row["right"])}

    overall = board()
    recent = board("WHERE created_at >= ?", (week_ago,))
    by_topic = connection.execute(
        "SELECT q.topic, COUNT(*) AS n, COALESCE(SUM(a.correct), 0) AS right FROM board_attempts a"
        " JOIN board_questions q ON q.id = a.question_id GROUP BY q.topic HAVING n > 0"
    ).fetchall()
    topics = sorted(
        ({"topic": row["topic"], "answered": int(row["n"]), "correct": int(row["right"])} for row in by_topic),
        key=lambda item: (item["correct"] / item["answered"], -item["answered"]),
    )
    open_answers = connection.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN outcome = 'correct' THEN 1 ELSE 0 END) AS right FROM tutor_attempts"
    ).fetchone()
    cards = connection.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN rating = 'good' THEN 1 ELSE 0 END) AS good FROM flashcard_reviews"
    ).fetchone()
    socratic = connection.execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(exchanges), 0) AS exchanges FROM socratic_sessions WHERE status = 'done'"
    ).fetchone()
    return {
        "board": {**overall, "last_7_days": recent},
        "weakest_topics": topics[:5],
        "strongest_topics": [t for t in reversed(topics) if t["correct"] > 0][:5],
        "open_answers": {"answered": int(open_answers["n"] or 0), "correct": int(open_answers["right"] or 0)},
        "flashcards": {"reviewed": int(cards["n"] or 0), "got_it": int(cards["good"] or 0)},
        "socratic": {"sessions": int(socratic["n"] or 0), "exchanges": int(socratic["exchanges"] or 0)},
        "dashboard": dashboard(connection),
    }
