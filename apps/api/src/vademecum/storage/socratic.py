"""Socratic sessions (ADR 0025): a dialogue about one page, and what it found.

A session holds the page it is about, the transcript as it grows (the tutor's
question, the learner's answer, in turn), and at the end an assessment: how
the learner reasoned through the differential, the treatment options and the
underlying knowledge, with the gaps named. The gaps become flags on the page's
topic, so the Improvement Map and the flashcards take them up. The learner's
answers are stored for the session's own record and are never logged.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

MAX_EXCHANGES = 12
MAX_ANSWER_CHARS = 8000
MAX_QUESTION_CHARS = 1200
PROBES = ("differential", "treatment", "knowledge", "wrap_up")


@dataclass(frozen=True)
class Session:
    id: str
    entry_id: str | None
    topic: str
    title: str
    mode: str
    status: str
    transcript: tuple[dict[str, str], ...]
    assessment: dict[str, Any]
    exchanges: int
    created_at: str
    updated_at: str
    finished_at: str | None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["transcript"] = [dict(turn) for turn in self.transcript]
        data["assessment"] = dict(self.assessment)
        # Brought in from ChatGPT, Claude or a paste (ADR 0026), and whether it has been assessed.
        data["origin"] = str(self.assessment.get("origin") or "")
        data["assessed"] = self.status == "done" and bool(self.assessment.get("summary") or self.assessment.get("differential"))
        return data


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        data = json.loads(value or "")
    except ValueError:
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _session(row: sqlite3.Row) -> Session:
    transcript = tuple(
        {"role": str(turn.get("role") or ""), "text": str(turn.get("text") or ""), "probe": str(turn.get("probe") or "")}
        for turn in _loads(row["transcript"], [])
        if isinstance(turn, dict)
    )
    return Session(
        id=row["id"],
        entry_id=row["entry_id"],
        topic=row["topic"],
        title=row["title"],
        mode=row["mode"],
        status=row["status"],
        transcript=transcript,
        assessment=_loads(row["assessment"], {}),
        exchanges=int(row["exchanges"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        finished_at=row["finished_at"],
    )


def create_session(connection: sqlite3.Connection, *, entry_id: str | None, topic: str, title: str, mode: str) -> Session:
    session_id = new_id("soc")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO socratic_sessions (id, entry_id, topic, title, mode, status, transcript, assessment, exchanges, created_at, updated_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, 'open', '[]', '{}', 0, ?, ?, NULL)",
            (session_id, entry_id, topic[:120], title[:120], mode, now, now),
        )
    return get_session(connection, session_id)


def get_session(connection: sqlite3.Connection, session_id: str) -> Session:
    row = connection.execute("SELECT * FROM socratic_sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise NotFoundError("Socratic session", session_id)
    return _session(row)


def list_sessions(connection: sqlite3.Connection, *, limit: int = 20) -> list[Session]:
    rows = connection.execute("SELECT * FROM socratic_sessions ORDER BY updated_at DESC, id LIMIT ?", (max(1, int(limit)),)).fetchall()
    return [_session(row) for row in rows]


def open_session(connection: sqlite3.Connection) -> Session | None:
    row = connection.execute("SELECT * FROM socratic_sessions WHERE status = 'open' ORDER BY updated_at DESC LIMIT 1").fetchone()
    return None if row is None else _session(row)


def append(connection: sqlite3.Connection, session_id: str, *, role: str, text: str, probe: str = "") -> Session:
    """One line of the dialogue: the tutor's question or the learner's answer."""
    session = get_session(connection, session_id)
    if session.status != "open":
        raise ValueError("closed")
    limit = MAX_QUESTION_CHARS if role == "tutor" else MAX_ANSWER_CHARS
    turn = {"role": "tutor" if role == "tutor" else "learner", "text": " ".join(str(text).split())[:limit], "probe": probe if probe in PROBES else ""}
    transcript = [*(dict(t) for t in session.transcript), turn]
    exchanges = sum(1 for t in transcript if t["role"] == "learner")
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE socratic_sessions SET transcript = ?, exchanges = ?, updated_at = ? WHERE id = ?",
            (json.dumps(transcript), exchanges, utc_now(), session_id),
        )
    return get_session(connection, session_id)


def check_assessment(payload: dict[str, Any]) -> dict[str, Any]:
    """The assessment the server keeps: bounded prose, named gaps, no verdict beyond words."""
    def text(key: str, limit: int) -> str:
        return " ".join(str(payload.get(key) or "").split())[:limit]

    gaps = [" ".join(str(gap).split())[:120] for gap in (payload.get("knowledge_gaps") or []) if isinstance(gap, str) and str(gap).strip()][:5]
    return {
        "differential": text("differential", 400),
        "treatment": text("treatment", 400),
        "knowledge_strengths": text("knowledge_strengths", 400),
        "knowledge_gaps": gaps,
        "summary": text("summary", 600),
    }


def finish(connection: sqlite3.Connection, session_id: str, assessment: dict[str, Any]) -> Session:
    session = get_session(connection, session_id)
    if session.status != "open":
        raise ValueError("closed")
    checked = check_assessment(assessment)
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE socratic_sessions SET status = 'done', assessment = ?, updated_at = ?, finished_at = ? WHERE id = ?",
            (json.dumps(checked), now, now, session_id),
        )
    return get_session(connection, session_id)


MAX_IMPORTED_TURNS = 2 * MAX_EXCHANGES + 40


def import_session(
    connection: sqlite3.Connection,
    *,
    title: str,
    topic: str,
    entry_id: str | None,
    origin: str,
    transcript: list[dict[str, str]],
    assessment: dict[str, Any] | None,
) -> Session:
    """A session held elsewhere -- ChatGPT or Claude in voice, say -- brought in
    whole: its dialogue, and its assessment when there is one (ADR 0026)."""
    turns = []
    for turn in transcript[:MAX_IMPORTED_TURNS]:
        role = "tutor" if turn.get("role") == "tutor" else "learner"
        text = " ".join(str(turn.get("text") or "").split())[: MAX_QUESTION_CHARS if role == "tutor" else MAX_ANSWER_CHARS]
        if text:
            turns.append({"role": role, "text": text, "probe": ""})
    if not any(t["role"] == "learner" for t in turns):
        raise ValueError("empty")
    # The table allows only its first statuses and modes: an imported session is
    # 'done' in mode 'host' (an assistant elsewhere was the tutor), its origin is
    # kept with the assessment, and an empty assessment means not yet assessed.
    checked = {**(check_assessment(assessment) if assessment else {}), "origin": origin}
    session_id = new_id("soc")
    now = utc_now()
    status = "done"
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO socratic_sessions (id, entry_id, topic, title, mode, status, transcript, assessment, exchanges, created_at, updated_at, finished_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id, entry_id, topic[:120], title[:120], "host", status, json.dumps(turns), json.dumps(checked),
                sum(1 for t in turns if t["role"] == "learner"), now, now, now,
            ),
        )
    return get_session(connection, session_id)


def set_assessment(connection: sqlite3.Connection, session_id: str, *, assessment: dict[str, Any], title: str = "", topic: str = "", entry_id: str | None = None) -> Session:
    """An imported session, assessed afterwards."""
    session = get_session(connection, session_id)
    checked = {**check_assessment(assessment), "origin": session.assessment.get("origin", "")}
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE socratic_sessions SET status = 'done', assessment = ?, title = ?, topic = ?, entry_id = ?, updated_at = ? WHERE id = ?",
            (json.dumps(checked), (title or session.title)[:120], (topic or session.topic)[:120], entry_id or session.entry_id, utc_now(), session_id),
        )
    return get_session(connection, session_id)


def abandon(connection: sqlite3.Connection, session_id: str) -> Session:
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("UPDATE socratic_sessions SET status = 'abandoned', updated_at = ?, finished_at = ? WHERE id = ? AND status = 'open'", (now, now, session_id))
    return get_session(connection, session_id)
