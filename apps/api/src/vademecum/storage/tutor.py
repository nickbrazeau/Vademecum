"""The Tutor cycle and the answers given to it.

One idea, made durable: a cycle is a shuffled ordering of every eligible
question, and each is served once before any is served twice. That is the whole
scheduling model. There is no interval, no due date, no count owed and no
streak; a cycle that is never finished is simply a cycle that is never finished,
and nothing anywhere says so.

The cycle lives in a table rather than in memory because "no repeats" that
resets when the process restarts is not the property the owner was promised.

Three cases the cycle has to survive, and does:

* **A question stops being eligible mid-cycle.** Its evidence was retracted, or
  the owner removed the material. It is skipped when it comes up, without
  disturbing the position of anything else.
* **New questions appear mid-cycle.** They join the *next* cycle, not this one.
  Splicing them in would let a question be asked twice before another was asked
  once, which is exactly what a cycle is for.
* **The page is refreshed.** The current question is the earliest unserved entry
  and is served again by the same query -- so a refresh shows the same question
  rather than burning one.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now
from .learning import (
    Question,
    eligible_question_ids,
    get_question,
    hold_reason_for,
    is_still_eligible,
)


class NotEligible(RuntimeError):
    """A question that may not be asked or graded right now, and why.

    Raised at grade time as well as at selection time. Eligibility is re-read
    from the database at both points: a source excluded between being shown a
    question and answering it must stop the grade, and a boolean carried in a
    session would not notice.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def ensure_gradable(connection: sqlite3.Connection, question_id: str) -> Question:
    """The question, if it is still eligible. Checked fresh, every time."""
    question = get_question(connection, question_id)
    if not is_still_eligible(connection, question_id):
        raise NotEligible(hold_reason_for(connection, question_id))
    return question

OUTCOMES: tuple[str, ...] = (
    "correct",
    "partially_correct",
    "incorrect",
    "unable_to_grade",
    "self_assessed",
)

CYCLE_KEY = "tutor.cycle_number"
LAST_QUESTION_KEY = "tutor.last_advanced_question"


@dataclass(frozen=True)
class Attempt:
    id: str
    question_id: str | None
    outcome: str
    graded_by: str
    feedback: str
    strengths: str
    missing_or_unsafe: str
    improved_answer: str
    uncertainty: str
    created_at: str
    question_version: int = 1
    # What was actually asked at the time, so history stays readable after a
    # question is rewritten or retired.
    asked_prompt: str = ""

    def as_dict(self) -> dict[str, Any]:
        """The attempt as the API returns it.

        The learner's own answer is stored and is *not* returned here: it is on
        screen already, it is in the database if they want it, and an API
        response is one more surface it could be cached in.
        """
        return asdict(self)


@dataclass(frozen=True)
class CycleState:
    """Where the cycle is, in the only terms that are honest.

    ``remaining`` is not a target. It is how many questions are left before the
    shuffle is redrawn, which is a fact about the shuffle rather than a demand.
    The interface says "N left in this pass"; it never says a number is owed.
    """

    cycle_number: int
    position: int
    total: int
    remaining: int
    exhausted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NextQuestion:
    question: Question | None
    cycle: CycleState
    last_attempt: Attempt | None = None
    history_count: int = 0
    empty_reason: str = ""

    def as_dict(self, *, include_reference: bool = False) -> dict[str, Any]:
        return {
            "question": (
                None
                if self.question is None
                else self.question.as_dict(include_reference=include_reference)
            ),
            "cycle": self.cycle.as_dict(),
            "last_attempt": None if self.last_attempt is None else self.last_attempt.as_dict(),
            "history_count": self.history_count,
            "empty_reason": self.empty_reason,
        }


NO_BANK = (
    "There are no Tutor questions yet. Tutor only asks questions that were built "
    "from your own sources, so it stays empty until you upload material and run "
    "Build learning material."
)

ALL_HELD = (
    "Every question built so far is on hold — usually because a quoted passage "
    "could not be matched to the page it cited, or because the evidence behind "
    "it changed. Nothing is being asked rather than something unchecked being "
    "asked."
)


def _current_cycle(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM app_state WHERE key = ?", (CYCLE_KEY,)
    ).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except ValueError:  # pragma: no cover - written only by this module
        return 0


def _set_cycle(tx: sqlite3.Connection, number: int) -> None:
    tx.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (CYCLE_KEY, str(number), utc_now()),
    )


def _draw_cycle(
    connection: sqlite3.Connection, question_ids: list[str], *, rng: random.Random | None = None
) -> int:
    """Shuffle every eligible question into a new numbered cycle."""
    shuffled = list(question_ids)
    randomizer = rng or random.SystemRandom()
    randomizer.shuffle(shuffled)
    last = connection.execute(
        "SELECT value FROM app_state WHERE key = ?", (LAST_QUESTION_KEY,)
    ).fetchone()
    if len(shuffled) > 1 and last is not None and shuffled[0] == last["value"]:
        # A new pass contains every question once, but need not begin with the
        # one just left. Preserve the shuffled order apart from this one swap.
        replacement = randomizer.randrange(1, len(shuffled))
        shuffled[0], shuffled[replacement] = shuffled[replacement], shuffled[0]
    number = _current_cycle(connection) + 1
    now = utc_now()
    with transaction(connection) as tx:
        _set_cycle(tx, number)
        for position, question_id in enumerate(shuffled):
            tx.execute(
                "INSERT INTO tutor_cycle_entries"
                " (id, cycle_number, position, question_id, served_at, created_at)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
                (new_id("cyc"), number, position, question_id, now),
            )
        # Cycles before the previous one are of no further use. Two are kept so
        # a cycle that has just rolled over can still be described.
        tx.execute("DELETE FROM tutor_cycle_entries WHERE cycle_number < ?", (number - 1,))
    return number


def _cycle_state(connection: sqlite3.Connection, number: int) -> CycleState:
    row = connection.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN served_at IS NULL THEN 0 ELSE 1 END) AS served"
        " FROM tutor_cycle_entries WHERE cycle_number = ?",
        (number,),
    ).fetchone()
    total = row["total"] or 0
    served = row["served"] or 0
    return CycleState(
        cycle_number=number,
        position=served,
        total=total,
        remaining=max(0, total - served),
        exhausted=total > 0 and served >= total,
    )


def next_question(
    connection: sqlite3.Connection, *, rng: random.Random | None = None
) -> NextQuestion:
    """The question to ask now, drawing a fresh cycle when this one is done.

    Idempotent: calling it twice without answering returns the same question,
    because only an explicit advance marks an entry served -- grading or looking
    at it does not. Refreshing the page keeps your place.
    """
    with transaction(connection):
        return _next_question(connection, rng=rng)


def _next_question(
    connection: sqlite3.Connection, *, rng: random.Random | None = None
) -> NextQuestion:
    eligible = eligible_question_ids(connection)
    if not eligible:
        held = connection.execute(
            "SELECT COUNT(*) AS n FROM tutor_questions WHERE status = 'held'"
        ).fetchone()["n"]
        return NextQuestion(
            question=None,
            cycle=CycleState(_current_cycle(connection), 0, 0, 0),
            empty_reason=ALL_HELD if held else NO_BANK,
        )

    eligible_set = set(eligible)
    number = _current_cycle(connection)

    for _ in range(2):
        if number == 0:
            number = _draw_cycle(connection, eligible, rng=rng)

        rows = connection.execute(
            "SELECT id, question_id FROM tutor_cycle_entries"
            " WHERE cycle_number = ? AND served_at IS NULL ORDER BY position",
            (number,),
        ).fetchall()

        for row in rows:
            if row["question_id"] in eligible_set:
                question = get_question(connection, row["question_id"])
                last = last_attempt(connection, question.id)
                return NextQuestion(
                    question=question,
                    cycle=_cycle_state(connection, number),
                    last_attempt=last,
                    history_count=attempt_count(connection, question.id),
                )
            # No longer eligible: retire the entry so it is skipped for good
            # rather than reconsidered on every call.
            with transaction(connection) as tx:
                tx.execute(
                    "UPDATE tutor_cycle_entries SET served_at = ? WHERE id = ?",
                    (utc_now(), row["id"]),
                )

        # This cycle is exhausted (or contained nothing still eligible). Draw
        # the next one and look again. Bounded to one redraw: if a freshly drawn
        # cycle is also empty, something upstream is wrong and looping would
        # hide it.
        number = _draw_cycle(connection, eligible, rng=rng)

    return NextQuestion(
        question=None,
        cycle=_cycle_state(connection, number),
        empty_reason=ALL_HELD,
    )


def mark_served(connection: sqlite3.Connection, question_id: str) -> CycleState:
    """Advance only the current entry; stale or out-of-order requests are no-ops."""
    number = _current_cycle(connection)
    with transaction(connection) as tx:
        current = tx.execute(
            "SELECT id, question_id FROM tutor_cycle_entries WHERE cycle_number = ?"
            " AND served_at IS NULL ORDER BY position LIMIT 1", (number,)
        ).fetchone()
        if current is None or current["question_id"] != question_id:
            return _cycle_state(connection, number)
        now = utc_now()
        tx.execute(
            "UPDATE tutor_cycle_entries SET served_at = ? WHERE id = ?",
            (now, current["id"]),
        )
        tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (LAST_QUESTION_KEY, question_id, now),
        )
    return _cycle_state(connection, number)


def advance_question(
    connection: sqlite3.Connection, question_id: str, *, rng: random.Random | None = None
) -> NextQuestion:
    """Commit leaving the current question and select its successor together."""
    with transaction(connection):
        mark_served(connection, question_id)
        return _next_question(connection, rng=rng)


def record_attempt(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    outcome: str,
    graded_by: str,
    answer: str,
    feedback: str = "",
    strengths: str = "",
    missing_or_unsafe: str = "",
    improved_answer: str = "",
    uncertainty: str = "",
    asked_version: int | None = None,
    asked_prompt: str | None = None,
    asked_reference: str | None = None,
    asked_rubric: str | None = None,
) -> Attempt:
    """Store one attempt, snapshotting what was actually asked.

    The caller may pass the snapshot it captured before an await (grading does),
    so the record describes the text the model actually saw rather than whatever
    the row says by the time the reply lands.

    The prompt, reference answer and rubric are copied in as they stood. A later
    run may rewrite any of them; without the snapshot the history would silently
    re-describe itself, and would become unreadable if the question were ever
    retired.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}")
    if graded_by not in {"model", "self"}:
        raise ValueError(f"unknown grader {graded_by!r}")
    question = get_question(connection, question_id)  # 404 beats an orphan attempt
    attempt_id = new_id("att")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO tutor_attempts (id, question_id, question_version, asked_prompt,"
            " asked_reference, asked_rubric, outcome, graded_by, answer, feedback,"
            " strengths, missing_or_unsafe, improved_answer, uncertainty, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt_id,
                question_id,
                question.version if asked_version is None else asked_version,
                question.prompt if asked_prompt is None else asked_prompt,
                question.reference_answer if asked_reference is None else asked_reference,
                question.rubric if asked_rubric is None else asked_rubric,
                outcome,
                graded_by,
                answer,
                feedback,
                strengths,
                missing_or_unsafe,
                improved_answer,
                uncertainty,
                now,
            ),
        )
    return get_attempt(connection, attempt_id)


def _attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        id=row["id"],
        question_id=row["question_id"],
        question_version=row["question_version"],
        asked_prompt=row["asked_prompt"],
        outcome=row["outcome"],
        graded_by=row["graded_by"],
        feedback=row["feedback"],
        strengths=row["strengths"],
        missing_or_unsafe=row["missing_or_unsafe"],
        improved_answer=row["improved_answer"],
        uncertainty=row["uncertainty"],
        created_at=row["created_at"],
    )


def get_attempt(connection: sqlite3.Connection, attempt_id: str) -> Attempt:
    row = connection.execute(
        "SELECT * FROM tutor_attempts WHERE id = ?", (attempt_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("attempt", attempt_id)
    return _attempt(row)


def last_attempt(connection: sqlite3.Connection, question_id: str) -> Attempt | None:
    row = connection.execute(
        "SELECT * FROM tutor_attempts WHERE question_id = ?"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (question_id,),
    ).fetchone()
    return None if row is None else _attempt(row)


def attempt_count(connection: sqlite3.Connection, question_id: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) AS n FROM tutor_attempts WHERE question_id = ?", (question_id,)
    ).fetchone()["n"]


def recent_attempts(connection: sqlite3.Connection, limit: int = 20) -> list[Attempt]:
    rows = connection.execute(
        "SELECT * FROM tutor_attempts ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_attempt(row) for row in rows]


@dataclass(frozen=True)
class TutorOverview:
    eligible: int
    held: int
    answered_total: int
    cycle: CycleState
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["cycle"] = self.cycle.as_dict()
        data["reasons"] = list(self.reasons)
        return data


def overview(connection: sqlite3.Connection) -> TutorOverview:
    eligible = len(eligible_question_ids(connection))
    held_rows = connection.execute(
        "SELECT hold_reason, COUNT(*) AS n FROM tutor_questions WHERE status = 'held'"
        " GROUP BY hold_reason ORDER BY n DESC LIMIT 5"
    ).fetchall()
    held = connection.execute(
        "SELECT COUNT(*) AS n FROM tutor_questions WHERE status = 'held'"
    ).fetchone()["n"]
    answered = connection.execute("SELECT COUNT(*) AS n FROM tutor_attempts").fetchone()["n"]
    return TutorOverview(
        eligible=eligible,
        held=held,
        answered_total=answered,
        cycle=_cycle_state(connection, _current_cycle(connection)),
        reasons=tuple(row["hold_reason"] for row in held_rows if row["hold_reason"]),
    )
