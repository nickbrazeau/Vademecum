"""Flashcards (ADR 0024): a front and a back from a page, drawn where the map says to look.

A card is written from an encyclopedia page and cites its points, so it is
eligible on the same terms as a board question: current page, same version,
every cited point unheld. The next card is a weighted draw, not a queue: a
card on a topic the learner flagged, on an area an exam report put below the
mark, on a page whose board question was missed lately, or one the learner
asked to see again, is drawn more often. Nothing is due and nothing is
counted against the learner.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now
from .encyclopedia import cited_points

RATINGS = ("again", "good")
RECENT_DAYS = 14
RESTED_HOURS = 24

NO_CARDS = (
    "There are no flashcards yet. Cards are written from the encyclopedia's pages, so the deck "
    "stays empty until a page has been compiled."
)


@dataclass(frozen=True)
class Flashcard:
    id: str
    entry_id: str
    topic: str
    title: str
    front: str
    back: str
    point_ids: tuple[str, ...]
    status: str
    hold_reason: str
    entry_version: int
    created_at: str

    def as_dict(self, *, include_back: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["point_ids"] = list(self.point_ids)
        if not include_back:
            del data["back"]
        return data


def _json_list(value: str | None) -> list[Any]:
    try:
        data = json.loads(value or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _card(row: sqlite3.Row) -> Flashcard:
    keys = row.keys()
    return Flashcard(
        id=row["id"],
        entry_id=row["entry_id"],
        topic=row["topic"],
        title=row["title"] if "title" in keys else "",
        front=row["front"],
        back=row["back"],
        point_ids=tuple(str(x) for x in _json_list(row["point_ids"])),
        status=row["status"],
        hold_reason=row["hold_reason"],
        entry_version=int(row["entry_version"]),
        created_at=row["created_at"],
    )


_SELECT = "SELECT c.*, e.title FROM flashcards c JOIN encyclopedia_entries e ON e.id = c.entry_id"


def get_card(connection: sqlite3.Connection, card_id: str) -> Flashcard:
    row = connection.execute(f"{_SELECT} WHERE c.id = ?", (card_id,)).fetchone()
    if row is None:
        raise NotFoundError("flashcard", card_id)
    return _card(row)


def cards_for_entry(connection: sqlite3.Connection, entry_id: str) -> list[Flashcard]:
    rows = connection.execute(f"{_SELECT} WHERE c.entry_id = ? ORDER BY c.created_at, c.id", (entry_id,)).fetchall()
    return [_card(row) for row in rows]


def entries_needing_cards(connection: sqlite3.Connection, *, minimum: int) -> list[str]:
    rows = connection.execute(
        "SELECT e.id FROM encyclopedia_entries e WHERE e.status = 'current' AND"
        " (SELECT COUNT(*) FROM flashcards c WHERE c.entry_id = e.id AND c.status = 'eligible' AND c.entry_version = e.version) < ?"
        " ORDER BY e.updated_at DESC",
        (minimum,),
    ).fetchall()
    return [row["id"] for row in rows]


def content_hash(front: str) -> str:
    return hashlib.sha256(" ".join(front.lower().split()).encode()).hexdigest()[:32]


def insert_cards(connection: sqlite3.Connection, *, entry_id: str, topic: str, entry_version: int, drafts: list[dict[str, Any]]) -> dict[str, int]:
    now = utc_now()
    written = held = 0
    if connection.execute("SELECT 1 FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone() is None:
        return {"written": 0, "held": 0}  # the page was deleted while its cards were written
    with transaction(connection) as tx:
        for draft in drafts:
            digest = content_hash(draft["front"])
            if tx.execute("SELECT 1 FROM flashcards WHERE entry_id = ? AND content_hash = ?", (entry_id, digest)).fetchone():
                continue
            status = "held" if draft.get("hold_reason") else "eligible"
            tx.execute(
                "INSERT INTO flashcards (id, entry_id, topic, front, back, point_ids, status, hold_reason, entry_version, content_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("card"),
                    entry_id,
                    topic,
                    draft["front"][:240],
                    draft["back"][:600],
                    json.dumps(list(draft.get("point_ids") or [])),
                    status,
                    str(draft.get("hold_reason") or "")[:300],
                    entry_version,
                    digest,
                    now,
                    now,
                ),
            )
            if status == "eligible":
                written += 1
            else:
                held += 1
    return {"written": written, "held": held}


def hold_cards_whose_points_left(tx: sqlite3.Connection, entry_id: str, kept: set[str]) -> None:
    now = utc_now()
    for row in tx.execute("SELECT id, point_ids FROM flashcards WHERE entry_id = ? AND status = 'eligible'", (entry_id,)).fetchall():
        if not {str(x) for x in _json_list(row["point_ids"])} <= kept:
            tx.execute(
                "UPDATE flashcards SET status = 'held', hold_reason = ?, updated_at = ? WHERE id = ?",
                ("The page was rewritten and a point this card cites is no longer on it.", now, row["id"]),
            )


def eligible_card_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT c.id FROM flashcards c
          JOIN encyclopedia_entries e ON e.id = c.entry_id
         WHERE c.status = 'eligible' AND e.status = 'current' AND c.entry_version = e.version
           AND NOT EXISTS (
                SELECT 1 FROM json_each(c.point_ids) j
                  LEFT JOIN learning_points p ON p.id = j.value
                 WHERE p.id IS NULL OR p.held = 1 OR p.review_state != 'machine_reviewed'
           )
         ORDER BY c.created_at, c.id
        """
    ).fetchall()
    return [row["id"] for row in rows]


# --- the draw --------------------------------------------------------------------


def _since(days: float = 0, hours: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days, hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def improvement_weights(connection: sqlite3.Connection) -> dict[str, Any]:
    """What the map knows: flagged topics, areas below the mark, pages whose questions were missed."""
    from .flags import list_flags
    from .reports import areas_for_map

    flagged = {(flag.topic or "").strip().casefold() for flag in list_flags(connection, status="open") if flag.topic}
    flagged.discard("")
    below = {str(area["topic"]).strip().casefold() for area in areas_for_map(connection) if area.get("standing") == "below"}
    missed = {
        row["entry_id"]
        for row in connection.execute(
            "SELECT DISTINCT q.entry_id FROM board_attempts a JOIN board_questions q ON q.id = a.question_id"
            " WHERE a.correct = 0 AND a.created_at >= ?",
            (_since(days=RECENT_DAYS),),
        ).fetchall()
    }
    return {"flagged": flagged, "below": below, "missed": missed}


def _last_reviews(connection: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    rows = connection.execute(
        "SELECT card_id, rating, created_at FROM flashcard_reviews WHERE card_id IS NOT NULL ORDER BY created_at"
    ).fetchall()
    latest: dict[str, tuple[str, str]] = {}
    for row in rows:
        latest[row["card_id"]] = (row["rating"], row["created_at"])
    return latest


def weigh(card: Flashcard, *, weights: dict[str, Any], last: tuple[str, str] | None) -> tuple[float, list[str]]:
    """A card's weight in the draw and the reasons the learner is shown."""
    weight = 1.0
    reasons: list[str] = []
    topic = card.topic.strip().casefold()
    if topic in weights["flagged"]:
        weight += 3.0
        reasons.append("You flagged this topic as a gap.")
    if topic in weights["below"]:
        weight += 3.0
        reasons.append("An exam report put this area below the mark.")
    if card.entry_id in weights["missed"]:
        weight += 2.0
        reasons.append("A board question from this page was missed recently.")
    if last is None:
        weight += 1.0
        reasons.append("New card.")
    else:
        rating, at = last
        if rating == "again":
            weight += 2.0
            reasons.append("You asked to see this again.")
        elif at >= _since(hours=RESTED_HOURS):
            weight *= 0.2
    return weight, reasons


def next_card(
    connection: sqlite3.Connection,
    *,
    not_id: str | None = None,
    rng: random.Random | None = None,
    practise: bool = False,
    now: Any = None,
) -> dict[str, Any] | None:
    """Spaced repetition (feedback of 5 October): the card ready longest, as a share of
    its gap, comes first, with the Improvement Map's weight behind it; then a few new
    cards a day, the map's gaps first; then rest until the next is ready, unless the
    owner asks to keep practising, when the weighted draw of before returns."""
    from . import srs

    all_ids = eligible_card_ids(connection)
    if not all_ids:
        return None
    moment = now or srs.now_utc()
    plans = srs.schedules(connection)
    weights = improvement_weights(connection)
    latest = _last_reviews(connection)
    randomizer = rng or random.SystemRandom()
    eligible = set(all_ids)

    ready = [i for i in all_ids if i in plans and plans[i].due_at is not None and plans[i].due_at <= moment]
    fresh = [i for i in all_ids if i not in plans]
    later = sorted(plans[i].due_at for i in all_ids if i in plans and plans[i].due_at is not None and plans[i].due_at > moment)
    new_left = max(0, srs.NEW_PER_DAY - srs.new_started_today(connection, moment))
    counts = {
        "ready": len(ready),
        "new_left_today": min(new_left, len(fresh)),
        "new_total": len(fresh),
        "learned": len([i for i in plans if i in eligible]),
        "next_ready_at": later[0].isoformat().replace("+00:00", "Z") if later else None,
        "new_per_day": srs.NEW_PER_DAY,
    }

    def pick_weighted(ids: list[str]) -> str:
        cards = [get_card(connection, card_id) for card_id in ids]
        weighed = [weigh(card, weights=weights, last=latest.get(card.id))[0] for card in cards]
        return ids[randomizer.choices(range(len(ids)), weights=weighed, k=1)[0]]

    kind = ""
    choice: str | None = None
    candidates = [i for i in ready if i != not_id] or ready
    if candidates:
        def urgency(card_id: str) -> float:
            plan = plans[card_id]
            gap = max(plan.interval_days, 1 / 144)  # ten minutes, for a card being relearned
            overdue = (moment - plan.due_at).total_seconds() / 86400  # type: ignore[operator]
            card = get_card(connection, card_id)
            return (1 + overdue / gap) * weigh(card, weights=weights, last=latest.get(card_id))[0] + randomizer.random() * 0.01

        choice, kind = max(candidates, key=urgency), "ready"
    elif new_left > 0 and fresh:
        choice, kind = pick_weighted([i for i in fresh if i != not_id] or fresh), "new"
    elif practise:
        choice, kind = pick_weighted([i for i in all_ids if i != not_id] or all_ids), "practice"
    if choice is None:
        return {"card": None, "reasons": [], "citations": [], "deck": len(all_ids), "kind": "rest", "counts": counts, "schedule": None, "intervals": {}}
    card = get_card(connection, choice)
    plan = plans.get(choice, srs.NEW)
    reasons = weigh(card, weights=weights, last=latest.get(choice))[1]
    if kind == "ready" and not plan.is_new:
        reasons = [f"Ready again: last seen {srs.gap_label(moment - plan.last_at)} ago."] + [r for r in reasons if r != "New card."]  # type: ignore[operator]
    if kind == "practice":
        reasons = ["Extra practice: nothing else is ready yet."] + reasons
    return {
        "card": card.as_dict(),
        "reasons": reasons,
        "citations": cited_points(connection, list(card.point_ids)),
        "deck": len(all_ids),
        "kind": kind,
        "counts": counts,
        "schedule": plan.as_dict(),
        "intervals": srs.preview(plan, moment),
    }


def record_review(connection: sqlite3.Connection, card_id: str, rating: str) -> dict[str, Any]:
    if rating not in RATINGS:
        raise ValueError("rating")
    get_card(connection, card_id)
    review_id = new_id("rev")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("INSERT INTO flashcard_reviews (id, card_id, rating, created_at) VALUES (?, ?, ?, ?)", (review_id, card_id, rating, now))
    return {"id": review_id, "card_id": card_id, "rating": rating, "created_at": now}


def overview(connection: sqlite3.Connection) -> dict[str, Any]:
    by_status = {
        row["status"]: int(row["n"]) for row in connection.execute("SELECT status, COUNT(*) AS n FROM flashcards GROUP BY status").fetchall()
    }
    reviews = connection.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN rating = 'again' THEN 1 ELSE 0 END) AS again FROM flashcard_reviews"
    ).fetchone()
    weights = improvement_weights(connection)
    return {
        "eligible": len(eligible_card_ids(connection)),
        "held": by_status.get("held", 0),
        "total": sum(by_status.values()),
        "reviews_total": int(reviews["n"] or 0),
        "reviews_again": int(reviews["again"] or 0),
        "improvement": {"flagged_topics": len(weights["flagged"]), "areas_below": len(weights["below"]), "pages_missed": len(weights["missed"])},
    }
