"""The learner model behind the Improvement Map (ADR 0031).

Every page in the encyclopedia is a unit of knowledge, and so is every flagged topic or
exam-report area that no page covers yet. For each unit this gathers what the learner
has already done and estimates two things:

- **How well it is understood**: a Beta estimate over every answer and signal about
  the unit, recent ones counting for more. Board answers, flashcard answers and
  Socratic sessions are retrieval: the learner produced the answer. Open flags and
  exam-report areas are prior evidence: what the learner, or an exam, said about it.
- **How well it is holding now**: a forgetting curve, recall = 2^(-days / half-life).
  The half-life starts at a day and is replayed from the learner's retrievals. A
  success lengthens it, and lengthens it more after a longer gap (the spacing effect).
  A failure shortens it.

From those two estimates, each unit gets a plain state (not yet tried, still forming,
fading, holding) and a **need**: how much the next ten minutes would help there. Need
weighs what is not known by how much it matters to this learner (open flags, an exam
area below the mark) and adds a little for what is barely known at all. Each unit also
gets a suggested next step chosen for its state. The plan interleaves specialties so
that consecutive steps are not on the same subject.

Nothing is stored: the model is recomputed from records that already sync, so the Mac and
the phone agree. It describes and suggests. Nothing is owed, nothing counts down, and the
learner can ignore it.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import encyclopedia as pages_store
from . import map as map_store
from .flags import UNSORTED_TOPIC
from .srs import parse

# --- the model's constants, each with its reason (ADR 0031) ---------------------------

# How much each kind of evidence counts. Producing an answer to a vignette is the
# strongest retrieval signal, and a Socratic session counts more again because it asks for
# explanation. A flashcard is a narrower cue, so it counts less.
WEIGHT = {"board": 1.0, "card": 0.5, "socratic": 1.5, "flag": 0.75, "exam": 2.0}
# Older evidence counts for less: its weight halves every EVIDENCE_HALF_LIFE_DAYS.
EVIDENCE_HALF_LIFE_DAYS = 60.0
# The forgetting curve's half-life, in days, before any retrieval and its floor.
START_HALF_LIFE = 1.0
MIN_HALF_LIFE = 0.5
# A success multiplies the half-life by 1 + SPACING_GAIN x min(1, gap / half-life),
# and at least by MIN_GROWTH. Retrieval spaced out to about the half-life gains the most;
# massed repetition gains little.
SPACING_GAIN = 1.5
MIN_GROWTH = 1.1
# A failure halves it.
LAPSE_FACTOR = 0.5
# An exam area's standing, as an outcome.
EXAM_OUTCOME = {"below": 0.2, "at": 0.6, "above": 0.9}
# At most this many open flags count towards a unit.
MAX_FLAGS = 3

UNDERSTOOD = 0.6  # mastery at or above this: understood, given the evidence
HOLDING = 0.75  # recall now at or above this: holding

PLAN_LENGTH = 5

STATE_LABEL = {
    "untried": "Not yet tried",
    "forming": "Still forming",
    "fading": "Fading",
    "holding": "Holding",
}


@dataclass
class Evidence:
    kind: str  # board, card, socratic, flag, exam, read
    outcome: float | None  # 0..1, or None for a read
    at: datetime
    note: str = ""


@dataclass
class Unit:
    key: str
    title: str
    topic: str
    entry_id: str | None
    specialty_id: str | None
    names: set[str] = field(default_factory=set)
    evidence: list[Evidence] = field(default_factory=list)
    open_flags: int = 0
    exam_below: bool = False
    exam_at: bool = False
    board_ready: int = 0
    cards_ready: int = 0


# --- gathering -------------------------------------------------------------------------


def _at(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        moment = parse(stamp)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _gaps(assessment: str) -> list[str]:
    try:
        data = json.loads(assessment or "{}")
    except ValueError:
        return []
    gaps = data.get("knowledge_gaps") if isinstance(data, dict) else None
    return [str(gap) for gap in gaps if str(gap).strip()] if isinstance(gaps, list) else []


def _count_by_entry(connection: sqlite3.Connection, table: str, ids: set[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in connection.execute(f"SELECT id, entry_id FROM {table}"):  # noqa: S608 - a fixed table name
        if row["id"] in ids:
            counts[row["entry_id"]] += 1
    return counts


def _gather(connection: sqlite3.Connection) -> dict[str, Unit]:
    units: dict[str, Unit] = {}
    by_entry: dict[str, Unit] = {}
    by_name: dict[str, Unit] = {}
    index = pages_store.current_page_index(connection)
    for page in index:
        unit = Unit(
            key=f"page:{page['id']}",
            title=page["title"],
            topic=page["topic"],
            entry_id=page["id"],
            specialty_id=page["specialty_id"],
            names={page["topic"].casefold(), page["title"].casefold()},
        )
        units[unit.key] = unit
        by_entry[page["id"]] = unit
        for name in unit.names:
            by_name.setdefault(name, unit)

    assigned = map_store.topic_specialties(connection)
    specialties = map_store.list_specialties(connection)

    def unit_for_topic(topic: str) -> list[Unit]:
        """The pages a flagged topic or exam area names, or a unit of its own if none."""
        name = topic.strip().casefold()
        if not name or name == UNSORTED_TOPIC:
            return []
        if name in by_name:
            return [by_name[name]]
        matched = [by_entry[page["id"]] for page in pages_store.pages_matching(index, topic, limit=2) if page["id"] in by_entry]
        if matched:
            for unit in matched:
                unit.names.add(name)
            return matched
        key = f"topic:{name}"
        if key not in units:
            specialty = map_store.specialty_for(topic, assigned, specialties)
            units[key] = Unit(key=key, title=topic.strip(), topic=topic.strip(), entry_id=None, specialty_id=specialty.id if specialty else None, names={name})
            by_name[name] = units[key]
        return [units[key]]

    def unit_for(entry_id: str | None, topic: str | None) -> list[Unit]:
        if entry_id and entry_id in by_entry:
            return [by_entry[entry_id]]
        return unit_for_topic(topic or "")

    for row in connection.execute(
        "SELECT q.entry_id, q.topic, q.stem, a.correct, a.created_at FROM board_attempts a"
        " JOIN board_questions q ON q.id = a.question_id ORDER BY a.created_at"
    ):
        at = _at(row["created_at"])
        if at is not None:
            for unit in unit_for(row["entry_id"], row["topic"]):
                unit.evidence.append(Evidence("board", 1.0 if row["correct"] else 0.0, at, row["stem"] or ""))

    for row in connection.execute(
        "SELECT c.entry_id, c.topic, r.rating, r.created_at FROM flashcard_reviews r"
        " JOIN flashcards c ON c.id = r.card_id ORDER BY r.created_at"
    ):
        at = _at(row["created_at"])
        if at is not None:
            for unit in unit_for(row["entry_id"], row["topic"]):
                unit.evidence.append(Evidence("card", 1.0 if row["rating"] == "good" else 0.0, at))

    for row in connection.execute(
        "SELECT entry_id, topic, assessment, finished_at, updated_at FROM socratic_sessions WHERE status = 'done'"
    ):
        at = _at(row["finished_at"] or row["updated_at"])
        if at is None:
            continue
        gaps = _gaps(row["assessment"])
        # Each gap named takes a fifth off; a session with no gaps named is a success.
        outcome = max(0.15, 1.0 - 0.2 * len(gaps))
        for unit in unit_for(row["entry_id"], row["topic"]):
            unit.evidence.append(Evidence("socratic", outcome, at, "; ".join(gaps[:3])))

    for row in connection.execute("SELECT ref_id, created_at FROM review_events WHERE kind = 'page'"):
        at = _at(row["created_at"])
        if at is not None and row["ref_id"] in by_entry:
            by_entry[row["ref_id"]].evidence.append(Evidence("read", None, at))

    for row in connection.execute(
        "SELECT topic, text, created_at FROM knowledge_gap_flags WHERE status = 'open' AND topic IS NOT NULL AND topic != ''"
        " ORDER BY created_at DESC"
    ):
        at = _at(row["created_at"])
        if at is None:
            continue
        for unit in unit_for_topic(row["topic"]):
            unit.open_flags += 1
            if unit.open_flags <= MAX_FLAGS:
                unit.evidence.append(Evidence("flag", 0.0, at, row["text"] or ""))

    for row in connection.execute(
        "SELECT a.topic, a.standing, a.quote, r.created_at FROM exam_areas a JOIN exam_reports r ON r.id = a.report_id"
        " WHERE r.status = 'parsed'"
    ):
        at = _at(row["created_at"])
        if at is None or row["standing"] not in EXAM_OUTCOME:
            continue
        for unit in unit_for_topic(row["topic"]):
            unit.exam_below = unit.exam_below or row["standing"] == "below"
            unit.exam_at = unit.exam_at or row["standing"] == "at"
            unit.evidence.append(Evidence("exam", EXAM_OUTCOME[row["standing"]], at, row["quote"] or ""))

    from .flashcards import eligible_card_ids

    board_ready = _count_by_entry(connection, "board_questions", set(pages_store.eligible_board_ids(connection)))
    cards_ready = _count_by_entry(connection, "flashcards", set(eligible_card_ids(connection)))
    for entry_id, unit in by_entry.items():
        unit.board_ready = board_ready.get(entry_id, 0)
        unit.cards_ready = cards_ready.get(entry_id, 0)
    return units


# --- estimating ------------------------------------------------------------------------


RETRIEVAL = ("board", "card", "socratic")


def mastery(evidence: list[Evidence], now: datetime) -> tuple[float, float]:
    """How well the unit is understood (0..1) and how much evidence that rests on (0..1).

    A Beta(1, 1) prior updated by each outcome, weighted by its kind and halved for every
    EVIDENCE_HALF_LIFE_DAYS of age. Confidence is the effective amount of evidence, n,
    as n / (n + 3).
    """
    successes = failures = 0.0
    for item in evidence:
        if item.outcome is None:
            continue
        age = max(0.0, (now - item.at).total_seconds() / 86400)
        weight = WEIGHT[item.kind] * 0.5 ** (age / EVIDENCE_HALF_LIFE_DAYS)
        successes += weight * item.outcome
        failures += weight * (1.0 - item.outcome)
    amount = successes + failures
    return (1.0 + successes) / (2.0 + amount), amount / (amount + 3.0)


def half_life(evidence: list[Evidence]) -> tuple[float, datetime | None]:
    """The forgetting curve's half-life in days, replayed from retrievals in order, and
    the moment of the last retrieval (None if the learner has never retrieved it)."""
    h = START_HALF_LIFE
    last: datetime | None = None
    for item in sorted((e for e in evidence if e.kind in RETRIEVAL and e.outcome is not None), key=lambda e: e.at):
        gap = 0.0 if last is None else max(0.0, (item.at - last).total_seconds() / 86400)
        if item.outcome >= 0.6:  # type: ignore[operator]
            h *= max(MIN_GROWTH, 1.0 + SPACING_GAIN * min(1.0, gap / h)) if last is not None else 1.0
        elif item.outcome < 0.4:  # type: ignore[operator]
            h = max(MIN_HALF_LIFE, h * LAPSE_FACTOR)
        last = item.at
    return h, last


def recall_now(h: float, last: datetime | None, now: datetime) -> float | None:
    if last is None:
        return None
    days = max(0.0, (now - last).total_seconds() / 86400)
    return 2.0 ** (-days / h)


def _state(tried: bool, understood: float, recall: float | None) -> str:
    if not tried:
        return "untried"
    if understood < UNDERSTOOD:
        return "forming"
    if recall is not None and recall < HOLDING:
        return "fading"
    return "holding"


def _days(delta_seconds: float) -> str:
    days = delta_seconds / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{round(days)} days ago"


def _step(unit: Unit, state: str) -> dict[str, str]:
    """The next step suited to the unit's state, and why, in a sentence."""
    if unit.entry_id is None:
        return {
            "kind": "add_source",
            "label": "Add a source",
            "why": "There is no page on this yet. Add a source and Vademecum will build one.",
        }
    read = any(item.kind == "read" for item in unit.evidence)
    missed = any(item.kind in ("board", "socratic") and (item.outcome or 0) < 0.5 for item in unit.evidence)
    if state == "untried" and not read:
        return {"kind": "read", "label": "Read the page", "why": "Read it once first, then test yourself."}
    if state == "forming" and missed:
        return {
            "kind": "socratic",
            "label": "Talk it through",
            "why": "Explaining why, out loud, builds the understanding that the misses point to.",
        }
    if state == "fading" and unit.cards_ready:
        return {
            "kind": "flashcards",
            "label": "Flashcards",
            "why": "It is slipping. Recalling it now, just as it fades, makes it last longer.",
        }
    if unit.board_ready:
        why = {
            "untried": "Test yourself: recalling it fixes it better than reading it again.",
            "forming": "A few more questions will show what has settled.",
            "fading": "It is slipping. Recalling it now, just as it fades, makes it last longer.",
            "holding": "Holding. An occasional question, mixed in with other topics, keeps it there.",
        }[state]
        return {"kind": "board", "label": "Board questions", "why": why}
    if unit.cards_ready:
        return {"kind": "flashcards", "label": "Flashcards", "why": "Test yourself: recalling it fixes it better than reading it again."}
    return {"kind": "socratic", "label": "Talk it through", "why": "No questions are written for this page yet. Talk it through instead."}


def _summary(unit: Unit, now: datetime) -> list[str]:
    """What the estimate rests on, in plain words."""
    lines: list[str] = []
    board = [e for e in unit.evidence if e.kind == "board"]
    if board:
        right = sum(1 for e in board if (e.outcome or 0) >= 0.5)
        lines.append(f"Board questions: {right} of {len(board)} right.")
    cards = [e for e in unit.evidence if e.kind == "card"]
    if cards:
        lines.append(f"Flashcards: {sum(1 for e in cards if (e.outcome or 0) >= 0.5)} of {len(cards)} recalled.")
    for session in (e for e in unit.evidence if e.kind == "socratic"):
        lines.append(f"Socratic session: {'gaps named — ' + session.note if session.note else 'no gaps named'}.")
    if unit.open_flags:
        lines.append(f"{unit.open_flags} open flag{'s' if unit.open_flags != 1 else ''}.")
    if unit.exam_below:
        lines.append("An exam report put this area below the mark.")
    retrievals = [e.at for e in unit.evidence if e.kind in RETRIEVAL]
    if retrievals:
        lines.append(f"Last recalled {_days((now - max(retrievals)).total_seconds())}.")
    reads = [e.at for e in unit.evidence if e.kind == "read"]
    if reads:
        lines.append(f"Page last read {_days((now - max(reads)).total_seconds())}.")
    return lines


def assess(unit: Unit, now: datetime) -> dict[str, Any]:
    understood, confidence = mastery(unit.evidence, now)
    h, last = half_life(unit.evidence)
    recall = recall_now(h, last, now)
    tried = last is not None
    state = _state(tried, understood, recall)
    knowing = understood * (recall if recall is not None else 1.0)
    importance = 1.0 + 0.5 * min(unit.open_flags, MAX_FLAGS) + (1.0 if unit.exam_below else 0.3 if unit.exam_at else 0.0)
    signal = bool(unit.open_flags or unit.exam_below or unit.exam_at or tried)
    # Barely known at all: a little for exploring it, more if something already points here.
    exploration = 0.25 * (1.0 - confidence) if signal else 0.05
    need = (1.0 - knowing) * importance * (1.0 if signal else 0.2) + exploration
    if unit.entry_id is None:
        need *= 0.8  # nothing to study here until a source is added; a page you can open comes first
    return {
        "key": unit.key,
        "title": unit.title,
        "topic": unit.topic,
        "entry_id": unit.entry_id,
        "specialty_id": unit.specialty_id,
        "state": state,
        "state_label": STATE_LABEL[state],
        "understood": round(understood, 3),
        "recall": None if recall is None else round(recall, 3),
        "half_life_days": round(h, 2),
        "confidence": round(confidence, 3),
        "need": round(need, 4),
        "open_flags": unit.open_flags,
        "board_ready": unit.board_ready,
        "cards_ready": unit.cards_ready,
        "names": sorted(unit.names),
        "evidence": _summary(unit, now),
        "next": _step(unit, state),
    }


def _daily_order(key: str, now: datetime) -> str:
    """A tie-break that changes once a day, so equal suggestions rotate."""
    return hashlib.sha256(f"{now.astimezone().date().isoformat()}:{key}".encode()).hexdigest()


def interleave(assessed: list[dict[str, Any]], length: int = PLAN_LENGTH) -> list[dict[str, Any]]:
    """The most needed units, ordered so that consecutive steps are not on the same
    subject where another is available (interleaving). Only units above a small floor of
    need are taken, and at most one gap that has no page yet."""
    pool = sorted((item for item in assessed if item["need"] >= 0.1), key=lambda item: -item["need"])
    chosen: list[dict[str, Any]] = []
    sourceless = 0
    for item in pool:
        if item["next"]["kind"] == "add_source":
            # A gap with no page asks for a source, not ten minutes.
            if sourceless:
                continue
            sourceless += 1
        chosen.append(item)
        if len(chosen) == length:
            break
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in chosen:
        groups[item["specialty_id"] or "other"].append(item)
    plan: list[dict[str, Any]] = []
    previous: str | None = None
    while len(plan) < len(chosen):
        live = [key for key, items in groups.items() if items]
        options = [key for key in live if key != previous] or live
        key = max(options, key=lambda k: groups[k][0]["need"])
        plan.append(groups[key].pop(0))
        previous = key
    return plan


def model(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, Any]:
    """Every unit's estimate, the plan for the next few minutes, and topic names to units."""
    moment = now or datetime.now(timezone.utc)
    units = _gather(connection)
    assessed = [assess(unit, moment) for unit in units.values()]
    assessed.sort(key=lambda item: (-item["need"], _daily_order(item["key"], moment)))
    counts = {state: 0 for state in STATE_LABEL}
    for item in assessed:
        counts[item["state"]] += 1
    by_name: dict[str, str] = {}
    for item in assessed:
        for name in item["names"]:
            by_name.setdefault(name, item["key"])
    tracked = [item for item in assessed if item["state"] != "untried" or item["open_flags"] or item["entry_id"] is None]
    return {
        "plan": interleave(assessed),
        "units": tracked,
        "states": [{"state": state, "label": STATE_LABEL[state], "count": counts[state]} for state in STATE_LABEL],
        "by_name": by_name,
        "by_entry": {item["entry_id"]: item["state"] for item in assessed if item["entry_id"] and item["state"] != "untried"},
    }


def factor_for_entry(connection: sqlite3.Connection, *, now: datetime | None = None) -> dict[str, tuple[float, str | None]]:
    """For the flashcard draw: a multiplier per page from its state, and a reason to show.
    Fading pages come forward; holding ones step back. Flags and exam areas are already
    weighed by the draw itself, so only retention is added here."""
    moment = now or datetime.now(timezone.utc)
    out: dict[str, tuple[float, str | None]] = {}
    reasons = {
        "fading": (1.8, "Fading: recalling it now helps it last."),
        "forming": (1.4, "Still forming on this page."),
        "holding": (0.7, None),
        "untried": (1.0, None),
    }
    for unit in _gather(connection).values():
        if unit.entry_id is not None:
            out[unit.entry_id] = reasons[assess(unit, moment)["state"]]
    return out



def most_needed_entry(connection: sqlite3.Connection, *, kind: str, not_entry: str | None = None, now: datetime | None = None) -> str | None:
    """The page that needs it most among those with board questions (kind "board") or
    flashcards (kind "cards") ready: what "where you need it most" asks next. The page
    just practised is passed over while another qualifies, so topics interleave."""
    moment = now or datetime.now(timezone.utc)
    best: tuple[float, str, str] | None = None
    for unit in _gather(connection).values():
        ready = unit.board_ready if kind == "board" else unit.cards_ready
        if unit.entry_id is None or not ready:
            continue
        if unit.entry_id == not_entry:
            need = assess(unit, moment)["need"] - 1000.0  # last resort only
            candidate = (-need, _daily_order(unit.key, moment), unit.entry_id)
            if best is None or candidate < best:
                best = candidate
            continue
        need = assess(unit, moment)["need"]
        candidate = (-need, _daily_order(unit.key, moment), unit.entry_id)
        if best is None or candidate < best:
            best = candidate
    return None if best is None else best[2]
