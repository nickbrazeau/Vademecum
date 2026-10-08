"""The encyclopedia and the board bank (ADR 0023).

A page is one topic's learning points, compiled: a title, a summary and a few
sections whose every paragraph names the points it rests on, and through them
the sources and quotes a Build recorded. A page is current for one set of
points; when a Build adds to the topic the page is stale and is compiled
again. The board bank holds single-best-answer questions written from a page,
each citing the page's points; the cycle shuffles the eligible ones and the
answers are recorded, both as the Tutor's are. Grading a choice is local.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from ..db import transaction
from .common import NotFoundError, drop_disclaimers, new_id, utc_now
from .learning import LearningPoint, SUPPORT_LABEL, points_for_topic

KEY_CYCLE = "board.cycle_number"
KEY_LAST_QUESTION = "board.last_advanced_question"
KEY_LAST_REFRESH = "encyclopedia_last_refresh"

MAX_SECTIONS = 6
MAX_PARAGRAPHS = 4
OPTION_COUNT = 5
LETTERS = ("A", "B", "C", "D", "E")

NO_PAGES = (
    "There are no encyclopedia pages yet. Pages are compiled from the learning points a Build "
    "makes from your own sources, one page per topic."
)
NO_BOARD = (
    "There are no board questions yet. They are written from the encyclopedia's pages, so the "
    "bank stays empty until a page has been compiled."
)
ALL_BOARD_HELD = (
    "Every board question is on hold: a point it cites is held, or its page has been rewritten "
    "since. Nothing is being asked rather than something unchecked being asked."
)


# --- pages ----------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    id: str
    topic: str
    title: str
    specialty_id: str | None
    summary: str
    sections: tuple[dict[str, Any], ...]
    point_ids: tuple[str, ...]
    points_hash: str
    status: str
    status_detail: str
    version: int
    compiled_at: str | None
    created_at: str
    updated_at: str
    question_count: int = 0
    literature_checked_at: str | None = None
    literature_note: str = ""
    # The owner's own edit, in Markdown (ADR 0026); shown instead of the compiled text when present.
    body_md: str = ""
    edited_at: str | None = None

    def as_dict(self, *, include_sections: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data["point_ids"] = list(self.point_ids)
        data["point_count"] = len(self.point_ids)
        data["edited"] = bool(self.body_md.strip())
        # The compiled page moved on after the owner's edit: the edit still shows, and says so.
        data["edit_outdated"] = bool(self.edited_at and self.compiled_at and self.compiled_at > self.edited_at)
        if include_sections:
            data["sections"] = [dict(section) for section in self.sections]
        else:
            del data["sections"]
            del data["body_md"]
        return data


def _json_list(value: str | None) -> list[Any]:
    try:
        data = json.loads(value or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _entry(connection: sqlite3.Connection, row: sqlite3.Row) -> Entry:
    count = connection.execute(
        "SELECT COUNT(*) AS n FROM board_questions WHERE entry_id = ? AND status = 'eligible'", (row["id"],)
    ).fetchone()["n"]
    sections = tuple(
        {
            "heading": str(section.get("heading") or ""),
            "paragraphs": [
                {
                    "text": str(p.get("text") or ""),
                    "point_ids": [str(x) for x in (p.get("point_ids") or [])],
                    "record_ids": [str(x) for x in (p.get("record_ids") or [])],
                    "figures": [dict(f) for f in (p.get("figures") or []) if isinstance(f, dict) and f.get("image_id")],
                }
                for p in (section.get("paragraphs") or [])
                if isinstance(p, dict)
            ],
        }
        for section in _json_list(row["sections"])
        if isinstance(section, dict)
    )
    keys = row.keys()
    return Entry(
        id=row["id"],
        topic=row["topic"],
        title=row["title"],
        specialty_id=row["specialty_id"],
        summary=row["summary"],
        sections=sections,
        point_ids=tuple(str(x) for x in _json_list(row["point_ids"])),
        points_hash=row["points_hash"],
        status=row["status"],
        status_detail=row["status_detail"],
        version=int(row["version"]),
        compiled_at=row["compiled_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        question_count=int(count),
        literature_checked_at=row["literature_checked_at"] if "literature_checked_at" in keys else None,
        literature_note=row["literature_note"] if "literature_note" in keys else "",
        body_md=row["body_md"] if "body_md" in keys else "",
        edited_at=row["edited_at"] if "edited_at" in keys else None,
    )


def points_hash(points: list[LearningPoint]) -> str:
    """One hash for one set of points: ids and their last change."""
    digest = hashlib.sha256()
    for point in sorted(points, key=lambda p: p.id):
        digest.update(f"{point.id}:{point.updated_at}\n".encode())
    return digest.hexdigest()


def topics_with_points(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT DISTINCT t.topic FROM learning_point_topics t JOIN learning_points p ON p.id = t.learning_point_id"
        " WHERE p.held = 0 AND p.review_state = 'machine_reviewed' ORDER BY t.topic"
    ).fetchall()
    return [row["topic"] for row in rows]


def topics_to_compile(connection: sqlite3.Connection) -> list[str]:
    """Topics with no page, or a page compiled from a different set of points."""
    current = {
        row["topic"]: row["points_hash"]
        for row in connection.execute("SELECT topic, points_hash FROM encyclopedia_entries WHERE status = 'current'").fetchall()
    }
    stale: list[str] = []
    for topic in topics_with_points(connection):
        if current.get(topic) != points_hash(points_for_topic(connection, topic)):
            stale.append(topic)
    return stale


def list_entries(connection: sqlite3.Connection, *, q: str | None = None, limit: int = 300) -> list[Entry]:
    params: list[Any] = []
    where = ""
    if q and q.strip():
        where = " WHERE (title || ' ' || topic || ' ' || summary || ' ' || sections) LIKE ? ESCAPE '\\'"
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        params.append(f"%{escaped}%")
    rows = connection.execute(
        f"SELECT * FROM encyclopedia_entries{where} ORDER BY title COLLATE NOCASE, topic LIMIT ?", (*params, max(1, int(limit)))
    ).fetchall()
    return [_entry(connection, row) for row in rows]


def get_entry(connection: sqlite3.Connection, entry_id: str) -> Entry:
    row = connection.execute("SELECT * FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotFoundError("encyclopedia page", entry_id)
    return _entry(connection, row)


def entry_for_topic(connection: sqlite3.Connection, topic: str) -> Entry | None:
    row = connection.execute("SELECT * FROM encyclopedia_entries WHERE topic = ?", (topic,)).fetchone()
    return None if row is None else _entry(connection, row)


def _current_ids(connection: sqlite3.Connection, *, fuller: bool) -> list[str]:
    """Current pages by topic. A fuller page rests on several points or drew on the literature."""
    where = (
        " AND (json_array_length(e.point_ids) >= 2"
        " OR EXISTS (SELECT 1 FROM encyclopedia_records r WHERE r.entry_id = e.id AND r.cited = 1))"
        if fuller
        else ""
    )
    rows = connection.execute(f"SELECT e.id FROM encyclopedia_entries e WHERE e.status = 'current'{where} ORDER BY e.topic").fetchall()
    return [row["id"] for row in rows]


def page_of_the_day(connection: sqlite3.Connection, *, on_day: date | None = None) -> Entry | None:
    """The same page all day, a different one tomorrow; nothing is owed on it.

    Chosen from the fuller pages, so the day's page reads as a page and not as one point
    restated; from any current page while there are none.
    """
    ids = _current_ids(connection, fuller=True) or _current_ids(connection, fuller=False)
    if not ids:
        return None
    day = (on_day or date.today()).isoformat()
    index = int(hashlib.sha256(day.encode()).hexdigest(), 16) % len(ids)
    return get_entry(connection, ids[index])


def random_page(connection: sqlite3.Connection, *, rng: random.Random | None = None, not_id: str | None = None) -> Entry | None:
    """Another page: a fuller one other than `not_id` if there is one, else any other, else any."""
    everything = _current_ids(connection, fuller=False)
    for pool in (_current_ids(connection, fuller=True), everything):
        ids = [entry_id for entry_id in pool if entry_id != not_id]
        if ids:
            return get_entry(connection, (rng or random.SystemRandom()).choice(ids))
    return get_entry(connection, everything[0]) if everything else None


def upsert_entry(
    connection: sqlite3.Connection,
    *,
    topic: str,
    title: str,
    specialty_id: str | None,
    summary: str,
    sections: list[dict[str, Any]],
    point_ids: list[str],
    points_hash_value: str,
) -> Entry:
    """Write the page. A rewrite bumps the version and holds questions whose points left the page."""
    now = utc_now()
    existing = entry_for_topic(connection, topic)
    kept = set(point_ids)
    with transaction(connection) as tx:
        if existing is None:
            entry_id = new_id("ency")
            tx.execute(
                "INSERT INTO encyclopedia_entries (id, topic, title, specialty_id, summary, sections, point_ids, points_hash,"
                " status, status_detail, version, compiled_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'current', '', 1, ?, ?, ?)",
                (entry_id, topic, title[:120], specialty_id, summary[:600], json.dumps(sections), json.dumps(point_ids), points_hash_value, now, now, now),
            )
        else:
            entry_id = existing.id
            tx.execute(
                "UPDATE encyclopedia_entries SET title = ?, specialty_id = ?, summary = ?, sections = ?, point_ids = ?,"
                " points_hash = ?, status = 'current', status_detail = '', version = version + 1, compiled_at = ?, updated_at = ?"
                " WHERE id = ?",
                (title[:120], specialty_id, summary[:600], json.dumps(sections), json.dumps(point_ids), points_hash_value, now, now, entry_id),
            )
            for row in tx.execute("SELECT id, point_ids FROM board_questions WHERE entry_id = ? AND status = 'eligible'", (entry_id,)).fetchall():
                cited = {str(x) for x in _json_list(row["point_ids"])}
                if not cited <= kept:
                    tx.execute(
                        "UPDATE board_questions SET status = 'held', hold_reason = ?, updated_at = ? WHERE id = ?",
                        ("The page was rewritten and a point this question cites is no longer on it.", now, row["id"]),
                    )
            from .flashcards import hold_cards_whose_points_left

            hold_cards_whose_points_left(tx, entry_id, kept)
    return get_entry(connection, entry_id)


def set_entry_literature(
    connection: sqlite3.Connection, entry_id: str, record_ids: list[str], *, cited: set[str], note: str = ""
) -> None:
    """The records reviewed for a page, in the order the provider ranked them, and which were drawn on."""
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("DELETE FROM encyclopedia_records WHERE entry_id = ?", (entry_id,))
        for ordinal, record_id in enumerate(dict.fromkeys(record_ids)):
            tx.execute(
                "INSERT INTO encyclopedia_records (id, entry_id, record_id, ordinal, cited, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (new_id("encr"), entry_id, record_id, ordinal, 1 if record_id in cited else 0, now),
            )
        tx.execute(
            "UPDATE encyclopedia_entries SET literature_checked_at = ?, literature_note = ?, updated_at = ? WHERE id = ?",
            (now, note[:300], now, entry_id),
        )


def records_for_entry(connection: sqlite3.Connection, entry_id: str, *, with_abstracts: bool = False) -> list[dict[str, Any]]:
    """What the page's literature review shows: the record, its status flags, whether a paragraph drew on it."""
    rows = connection.execute(
        "SELECT er.record_id, er.cited, r.pmid, r.doi, r.title, r.journal, r.published_on, r.url, r.priority,"
        " r.retracted, r.corrected, r.is_notice, r.abstract"
        " FROM encyclopedia_records er JOIN literature_records r ON r.id = er.record_id"
        " WHERE er.entry_id = ? ORDER BY er.ordinal",
        (entry_id,),
    ).fetchall()
    return [
        {
            "record_id": row["record_id"],
            "cited": bool(row["cited"]),
            "pmid": row["pmid"] or "",
            "doi": row["doi"] or "",
            "title": row["title"],
            "journal": row["journal"],
            "published_on": row["published_on"],
            "url": row["url"],
            "priority": row["priority"],
            "retracted": bool(row["retracted"]),
            "corrected": bool(row["corrected"]),
            "is_notice": bool(row["is_notice"]),
            **({"abstract": str(row["abstract"] or "")[:1500]} if with_abstracts else {}),
        }
        for row in rows
    ]


def drop_stored_disclaimers(connection: sqlite3.Connection) -> int:
    """Take 'not an endorsement' clauses out of prose kept before they were refused at the door.

    Returns how many rows changed. Idempotent, and plain updates, so the change log carries them.
    """
    changed = 0
    with transaction(connection) as tx:
        for table, column in (("learning_points", "detail"), ("encyclopedia_entries", "summary"), ("board_questions", "explanation")):
            for row in tx.execute(f"SELECT id, {column} AS text FROM {table} WHERE {column} LIKE '%endorsement%'").fetchall():
                cleaned = drop_disclaimers(row["text"])
                if cleaned and cleaned != " ".join(row["text"].split()):
                    tx.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (cleaned, row["id"]))
                    changed += 1
        for row in tx.execute("SELECT id, sections FROM encyclopedia_entries WHERE sections LIKE '%endorsement%'").fetchall():
            sections = []
            for section in _json_list(row["sections"]):
                paragraphs = [{**p, "text": drop_disclaimers(str(p.get("text") or ""))} for p in section.get("paragraphs") or []]
                paragraphs = [p for p in paragraphs if p["text"]]
                if paragraphs:
                    sections.append({**section, "paragraphs": paragraphs})
            if sections and sections != _json_list(row["sections"]):
                tx.execute("UPDATE encyclopedia_entries SET sections = ? WHERE id = ?", (json.dumps(sections), row["id"]))
                changed += 1
    return changed


# --- the dissection agent's record ------------------------------------------------

KEY_DISSECTION = "dissection"


def get_dissection(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY_DISSECTION,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["value"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def set_dissection(connection: sqlite3.Connection, state: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY_DISSECTION, json.dumps(state, separators=(",", ":")), utc_now()),
        )


def mark_entry_failed(connection: sqlite3.Connection, topic: str, detail: str) -> None:
    """A page that exists keeps its text and notes the failure; a page that does not is not invented."""
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE encyclopedia_entries SET status_detail = ?, updated_at = ? WHERE topic = ?",
            (detail[:500], utc_now(), topic),
        )


def cited_points(connection: sqlite3.Connection, point_ids: list[str]) -> list[dict[str, Any]]:
    """What a citation shows: the point's claim, its support and its sources."""
    from .learning import get_point

    shown: list[dict[str, Any]] = []
    for point_id in point_ids:
        try:
            point = get_point(connection, point_id)
        except NotFoundError:
            continue
        shown.append(
            {
                "id": point.id,
                "claim": point.claim,
                "support": point.support,
                "support_label": SUPPORT_LABEL.get(point.support, point.support),
                "held": point.held,
                "sources": [
                    {"display_name": c.display_name, "locator": c.locator, "quote": c.quote, "source_id": c.source_id}
                    for c in point.citations
                ],
            }
        )
    return shown


def entry_counts(connection: sqlite3.Connection) -> dict[str, Any]:
    entries = connection.execute("SELECT COUNT(*) AS n FROM encyclopedia_entries WHERE status = 'current'").fetchone()["n"]
    questions = connection.execute(
        "SELECT status, COUNT(*) AS n FROM board_questions GROUP BY status"
    ).fetchall()
    by_status = {row["status"]: int(row["n"]) for row in questions}
    return {
        "entries": int(entries),
        "stale": len(topics_to_compile(connection)),
        "questions_eligible": len(eligible_board_ids(connection)),
        "questions_held": by_status.get("held", 0),
        "questions_total": sum(by_status.values()),
    }


def get_last_refresh(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY_LAST_REFRESH,)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row["value"])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def record_refresh(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (KEY_LAST_REFRESH, json.dumps(report, separators=(",", ":")), utc_now()),
        )


# --- board questions ------------------------------------------------------------


@dataclass(frozen=True)
class BoardQuestion:
    id: str
    entry_id: str
    topic: str
    stem: str
    options: tuple[str, ...]
    answer_index: int
    explanation: str
    objective: str
    point_ids: tuple[str, ...]
    status: str
    hold_reason: str
    entry_version: int
    created_at: str
    title: str = ""

    def as_dict(self, *, include_answer: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "entry_id": self.entry_id,
            "topic": self.topic,
            "title": self.title,
            "stem": self.stem,
            "options": [{"letter": LETTERS[i], "text": text} for i, text in enumerate(self.options)],
            "objective": self.objective,
            "status": self.status,
            "hold_reason": self.hold_reason,
            "entry_version": self.entry_version,
            "created_at": self.created_at,
            "point_ids": list(self.point_ids),
        }
        if include_answer:
            data["answer_index"] = self.answer_index
            data["answer_letter"] = LETTERS[self.answer_index]
            data["explanation"] = self.explanation
        return data


def _question(row: sqlite3.Row) -> BoardQuestion:
    keys = row.keys()
    return BoardQuestion(
        id=row["id"],
        entry_id=row["entry_id"],
        topic=row["topic"],
        stem=row["stem"],
        options=tuple(str(x) for x in _json_list(row["options"])),
        answer_index=int(row["answer_index"]),
        explanation=row["explanation"],
        objective=row["objective"],
        point_ids=tuple(str(x) for x in _json_list(row["point_ids"])),
        status=row["status"],
        hold_reason=row["hold_reason"],
        entry_version=int(row["entry_version"]),
        created_at=row["created_at"],
        title=row["title"] if "title" in keys else "",
    )


_QUESTION_SELECT = "SELECT q.*, e.title FROM board_questions q JOIN encyclopedia_entries e ON e.id = q.entry_id"


def get_question(connection: sqlite3.Connection, question_id: str) -> BoardQuestion:
    row = connection.execute(f"{_QUESTION_SELECT} WHERE q.id = ?", (question_id,)).fetchone()
    if row is None:
        raise NotFoundError("board question", question_id)
    return _question(row)


def questions_for_entry(connection: sqlite3.Connection, entry_id: str) -> list[BoardQuestion]:
    rows = connection.execute(f"{_QUESTION_SELECT} WHERE q.entry_id = ? ORDER BY q.created_at, q.id", (entry_id,)).fetchall()
    return [_question(row) for row in rows]


def entries_needing_questions(connection: sqlite3.Connection, *, minimum: int) -> list[Entry]:
    rows = connection.execute(
        "SELECT e.* FROM encyclopedia_entries e WHERE e.status = 'current' AND"
        " (SELECT COUNT(*) FROM board_questions q WHERE q.entry_id = e.id AND q.status = 'eligible'"
        "    AND q.entry_version = e.version) < ?"
        " ORDER BY e.updated_at DESC",
        (minimum,),
    ).fetchall()
    return [_entry(connection, row) for row in rows]


def content_hash(stem: str) -> str:
    return hashlib.sha256(" ".join(stem.lower().split()).encode()).hexdigest()[:32]


def insert_questions(connection: sqlite3.Connection, entry: Entry, drafts: list[dict[str, Any]]) -> dict[str, int]:
    """Write checked drafts. A stem already in the bank for this page is not written twice."""
    now = utc_now()
    written = held = 0
    with transaction(connection) as tx:
        for draft in drafts:
            digest = content_hash(draft["stem"])
            exists = tx.execute(
                "SELECT 1 FROM board_questions WHERE entry_id = ? AND content_hash = ?", (entry.id, digest)
            ).fetchone()
            if exists is not None:
                continue
            status = "held" if draft.get("hold_reason") else "eligible"
            tx.execute(
                "INSERT INTO board_questions (id, entry_id, topic, stem, options, answer_index, explanation, objective,"
                " point_ids, status, hold_reason, entry_version, content_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("bq"),
                    entry.id,
                    entry.topic,
                    draft["stem"],
                    json.dumps(list(draft["options"])),
                    int(draft["answer_index"]),
                    draft["explanation"],
                    str(draft.get("objective") or "")[:240],
                    json.dumps(list(draft.get("point_ids") or [])),
                    status,
                    str(draft.get("hold_reason") or "")[:300],
                    entry.version,
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


def eligible_board_ids(connection: sqlite3.Connection) -> list[str]:
    """Every board question the Tutor may ask, re-proved from the database each call.

    Eligible means: not held or retired, written for the page's current version, and every
    point it cites still unheld and machine reviewed. A cited point that was held since
    takes the question with it, the moment it is held.
    """
    rows = connection.execute(
        """
        SELECT q.id FROM board_questions q
          JOIN encyclopedia_entries e ON e.id = q.entry_id
         WHERE q.status = 'eligible' AND e.status = 'current' AND q.entry_version = e.version
           AND NOT EXISTS (
                SELECT 1 FROM json_each(q.point_ids) j
                  LEFT JOIN learning_points p ON p.id = j.value
                 WHERE p.id IS NULL OR p.held = 1 OR p.review_state != 'machine_reviewed'
           )
         ORDER BY q.created_at, q.id
        """
    ).fetchall()
    return [row["id"] for row in rows]


# --- the cycle (as the Tutor's) --------------------------------------------------


@dataclass(frozen=True)
class CycleState:
    cycle_number: int
    position: int
    total: int
    remaining: int
    exhausted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BoardAttempt:
    id: str
    question_id: str | None
    chosen_index: int
    correct: bool
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chosen_letter"] = LETTERS[self.chosen_index] if 0 <= self.chosen_index < OPTION_COUNT else ""
        return data


@dataclass(frozen=True)
class NextBoard:
    question: BoardQuestion | None
    cycle: CycleState
    last_attempt: BoardAttempt | None = None
    history_count: int = 0
    empty_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "question": None if self.question is None else self.question.as_dict(),
            "cycle": self.cycle.as_dict(),
            "last_attempt": None if self.last_attempt is None else self.last_attempt.as_dict(),
            "history_count": self.history_count,
            "empty_reason": self.empty_reason,
        }


def _current_cycle(connection: sqlite3.Connection) -> int:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY_CYCLE,)).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except ValueError:
        return 0


def _set_state(tx: sqlite3.Connection, key: str, value: str) -> None:
    tx.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, utc_now()),
    )


def _draw_cycle(connection: sqlite3.Connection, question_ids: list[str], *, rng: random.Random | None = None) -> int:
    shuffled = list(question_ids)
    randomizer = rng or random.SystemRandom()
    randomizer.shuffle(shuffled)
    last = connection.execute("SELECT value FROM app_state WHERE key = ?", (KEY_LAST_QUESTION,)).fetchone()
    if len(shuffled) > 1 and last is not None and shuffled[0] == last["value"]:
        replacement = randomizer.randrange(1, len(shuffled))
        shuffled[0], shuffled[replacement] = shuffled[replacement], shuffled[0]
    number = _current_cycle(connection) + 1
    now = utc_now()
    with transaction(connection) as tx:
        _set_state(tx, KEY_CYCLE, str(number))
        for position, question_id in enumerate(shuffled):
            tx.execute(
                "INSERT INTO board_cycle_entries (id, cycle_number, position, question_id, served_at, created_at)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
                (new_id("bcyc"), number, position, question_id, now),
            )
        tx.execute("DELETE FROM board_cycle_entries WHERE cycle_number < ?", (number - 1,))
    return number


def _fold_in_new(connection: sqlite3.Connection, number: int, eligible: list[str], *, rng: random.Random | None = None) -> None:
    """Questions written since the pass was drawn join it now, shuffled in among the
    ones not yet asked (feedback of 5 October): a pass drawn when there were forty
    questions would otherwise hold back the next two thousand until it ended. The
    question on screen, the first not yet asked, keeps its place."""
    present = {row["question_id"] for row in connection.execute("SELECT question_id FROM board_cycle_entries WHERE cycle_number = ?", (number,))}
    new = [question_id for question_id in eligible if question_id not in present]
    if not new:
        return
    unserved = connection.execute(
        "SELECT id, question_id FROM board_cycle_entries WHERE cycle_number = ? AND served_at IS NULL ORDER BY position", (number,)
    ).fetchall()
    keep = unserved[:1]
    rest = [row["question_id"] for row in unserved[1:]] + new
    (rng or random.SystemRandom()).shuffle(rest)
    top = connection.execute("SELECT COALESCE(MAX(position), -1) AS top FROM board_cycle_entries WHERE cycle_number = ?", (number,)).fetchone()["top"]
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "DELETE FROM board_cycle_entries WHERE cycle_number = ? AND served_at IS NULL AND id NOT IN (" + ",".join("?" * len(keep)) + ")"
            if keep
            else "DELETE FROM board_cycle_entries WHERE cycle_number = ? AND served_at IS NULL",
            (number, *[row["id"] for row in keep]),
        )
        position = int(top) + 1
        for question_id in rest:
            tx.execute(
                "INSERT INTO board_cycle_entries (id, cycle_number, position, question_id, served_at, created_at) VALUES (?, ?, ?, ?, NULL, ?)",
                (new_id("bcyc"), number, position, question_id, now),
            )
            position += 1


def _cycle_state(connection: sqlite3.Connection, number: int) -> CycleState:
    row = connection.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN served_at IS NULL THEN 0 ELSE 1 END) AS served"
        " FROM board_cycle_entries WHERE cycle_number = ?",
        (number,),
    ).fetchone()
    total = row["total"] or 0
    served = row["served"] or 0
    return CycleState(cycle_number=number, position=served, total=total, remaining=max(0, total - served), exhausted=total > 0 and served >= total)


def next_question(connection: sqlite3.Connection, *, rng: random.Random | None = None) -> NextBoard:
    """Idempotent: the same question until an explicit advance."""
    with transaction(connection):
        return _next_question(connection, rng=rng)


def _next_question(connection: sqlite3.Connection, *, rng: random.Random | None = None) -> NextBoard:
    eligible = eligible_board_ids(connection)
    if not eligible:
        total = connection.execute("SELECT COUNT(*) AS n FROM board_questions WHERE status != 'retired'").fetchone()["n"]
        return NextBoard(question=None, cycle=CycleState(_current_cycle(connection), 0, 0, 0), empty_reason=ALL_BOARD_HELD if total else NO_BOARD)
    eligible_set = set(eligible)
    number = _current_cycle(connection)
    for _ in range(2):
        if number == 0:
            number = _draw_cycle(connection, eligible, rng=rng)
        else:
            _fold_in_new(connection, number, eligible, rng=rng)
        rows = connection.execute(
            "SELECT id, question_id FROM board_cycle_entries WHERE cycle_number = ? AND served_at IS NULL ORDER BY position",
            (number,),
        ).fetchall()
        for row in rows:
            if row["question_id"] in eligible_set:
                question = get_question(connection, row["question_id"])
                return NextBoard(
                    question=question,
                    cycle=_cycle_state(connection, number),
                    last_attempt=last_attempt(connection, question.id),
                    history_count=attempt_count(connection, question.id),
                )
            with transaction(connection) as tx:
                tx.execute("UPDATE board_cycle_entries SET served_at = ? WHERE id = ?", (utc_now(), row["id"]))
        number = _draw_cycle(connection, eligible, rng=rng)
    return NextBoard(question=None, cycle=_cycle_state(connection, number), empty_reason=ALL_BOARD_HELD)


def mark_served(connection: sqlite3.Connection, question_id: str) -> CycleState:
    number = _current_cycle(connection)
    with transaction(connection) as tx:
        current = tx.execute(
            "SELECT id, question_id FROM board_cycle_entries WHERE cycle_number = ? AND served_at IS NULL ORDER BY position LIMIT 1",
            (number,),
        ).fetchone()
        if current is None or current["question_id"] != question_id:
            return _cycle_state(connection, number)
        tx.execute("UPDATE board_cycle_entries SET served_at = ? WHERE id = ?", (utc_now(), current["id"]))
        _set_state(tx, KEY_LAST_QUESTION, question_id)
    return _cycle_state(connection, number)


def advance_question(connection: sqlite3.Connection, question_id: str, *, rng: random.Random | None = None) -> NextBoard:
    with transaction(connection):
        mark_served(connection, question_id)
        return _next_question(connection, rng=rng)


# --- answers --------------------------------------------------------------------


def record_attempt(connection: sqlite3.Connection, question: BoardQuestion, chosen_index: int) -> BoardAttempt:
    if not 0 <= chosen_index < len(question.options):
        raise ValueError("choice out of range")
    attempt_id = new_id("batt")
    now = utc_now()
    correct = chosen_index == question.answer_index
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO board_attempts (id, question_id, asked_stem, chosen_index, correct, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (attempt_id, question.id, question.stem, chosen_index, 1 if correct else 0, now),
        )
    return BoardAttempt(id=attempt_id, question_id=question.id, chosen_index=chosen_index, correct=correct, created_at=now)


def _attempt(row: sqlite3.Row) -> BoardAttempt:
    return BoardAttempt(
        id=row["id"], question_id=row["question_id"], chosen_index=int(row["chosen_index"]), correct=bool(row["correct"]), created_at=row["created_at"]
    )


def last_attempt(connection: sqlite3.Connection, question_id: str) -> BoardAttempt | None:
    row = connection.execute(
        "SELECT * FROM board_attempts WHERE question_id = ? ORDER BY created_at DESC, id DESC LIMIT 1", (question_id,)
    ).fetchone()
    return None if row is None else _attempt(row)


def attempt_count(connection: sqlite3.Connection, question_id: str) -> int:
    return int(connection.execute("SELECT COUNT(*) AS n FROM board_attempts WHERE question_id = ?", (question_id,)).fetchone()["n"])


def recent_attempts(connection: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT a.*, q.topic FROM board_attempts a LEFT JOIN board_questions q ON q.id = a.question_id"
        " ORDER BY a.created_at DESC, a.id DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [{**_attempt(row).as_dict(), "asked_stem": row["asked_stem"], "topic": row["topic"] or ""} for row in rows]


def board_overview(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = entry_counts(connection)
    answered = connection.execute("SELECT COUNT(*) AS n, SUM(correct) AS right FROM board_attempts").fetchone()
    return {
        "eligible": counts["questions_eligible"],
        "held": counts["questions_held"],
        "total": counts["questions_total"],
        "pages": counts["entries"],
        "answered_total": int(answered["n"] or 0),
        "answered_correct": int(answered["right"] or 0),
        "cycle": _cycle_state(connection, _current_cycle(connection)).as_dict(),
    }



# --- pages for a topic in words (feedback of 6 October) ------------------------------

_MATCH_STOP = {
    "the", "and", "for", "with", "about", "on", "of", "in", "a", "an", "to", "how", "what", "approach", "episode",
    "podcast", "vs", "versus", "management", "treatment", "diagnosis", "disease", "disorder", "syndrome", "care",
    "diagnostic", "testing", "test", "tests", "criteria", "pathophysiology", "outpatient", "inpatient", "assessment",
    "evaluation", "therapy", "use", "adult", "adults", "patients", "patient", "approach", "workup", "overview", "basics",
}


def _words(text: str) -> set[str]:
    import re

    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _MATCH_STOP}


def current_page_index(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every current page, light: id, title, topic, specialty and its points, for matching."""
    rows = connection.execute(
        "SELECT id, title, topic, specialty_id, point_ids FROM encyclopedia_entries WHERE status = 'current'"
    ).fetchall()
    index = []
    for row in rows:
        try:
            points = [str(p) for p in json.loads(row["point_ids"] or "[]")]
        except ValueError:
            points = []
        index.append(
            {
                "id": row["id"],
                "title": row["title"],
                "topic": row["topic"],
                "specialty_id": row["specialty_id"],
                "point_ids": points,
                "words": _words(f"{row['title']} {row['topic']}"),
                "names": {" ".join(str(row["title"]).lower().split()), " ".join(str(row["topic"]).lower().split())},
            }
        )
    return index


def pages_matching(index: list[dict[str, Any]], text: str, *, limit: int = 3, floor: float = 0.34) -> list[dict[str, Any]]:
    """The pages a topic or request names: an exact title or topic first, then the most
    words in common, above a floor so one shared word does not make a match."""
    name = " ".join(text.lower().split())
    exact = [page for page in index if name in page["names"]]
    if exact:
        return exact[:limit]
    wanted = _words(text)
    if not wanted:
        return []
    scored = []
    for page in index:
        overlap = len(wanted & page["words"])
        if not overlap:
            continue
        score = overlap / len(wanted | page["words"])
        # A page whose whole title the topic contains ("Cirrhosis" for "Cirrhosis
        # definition and staging") is its page, however long the topic.
        named = page["words"] <= wanted and all(len(w) >= 5 for w in page["words"])
        if score >= floor or overlap >= 2 or named:
            score = max(score, 0.5) if named else score
            scored.append((score, page["title"], page))
    return [page for _score, _title, page in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]]



def next_for_page(connection: sqlite3.Connection, entry_id: str, *, not_id: str | None = None) -> NextBoard:
    """A question from one page (feedback of 6 October: Tutor mode from the Improvement
    Map). Outside the shuffled pass, which it leaves alone: the page's question asked
    longest ago, or never, comes first; the one just asked is skipped while others remain."""
    eligible = set(eligible_board_ids(connection))
    ids = [q.id for q in questions_for_entry(connection, entry_id) if q.id in eligible]
    if not ids:
        return NextBoard(question=None, cycle=CycleState(0, 0, 0, 0), empty_reason="This page has no board questions ready yet.")
    last: dict[str, str] = {}
    for row in connection.execute(
        f"SELECT question_id, MAX(created_at) AS at FROM board_attempts WHERE question_id IN ({','.join('?' * len(ids))}) GROUP BY question_id",
        ids,
    ):
        last[row["question_id"]] = row["at"]
    order = sorted(ids, key=lambda qid: (last.get(qid) or "", ids.index(qid)))
    candidates = [qid for qid in order if qid != not_id] or order
    question = get_question(connection, candidates[0])
    asked = sum(1 for qid in ids if qid in last)
    return NextBoard(
        question=question,
        cycle=CycleState(cycle_number=0, position=asked, total=len(ids), remaining=len(ids) - asked),
        last_attempt=last_attempt(connection, question.id),
        history_count=attempt_count(connection, question.id),
    )
