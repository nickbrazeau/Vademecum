"""Generation runs: what was attempted, what happened, and what survived.

A run is a row before it is anything else. That ordering is deliberate: a
process killed halfway through a build must leave evidence that a build was
happening, not an absence that looks like nothing was ever tried.

Two properties this module is responsible for:

* **A failed rerun costs nothing.** Points and questions are upserted by
  content, so a run that fails after writing three points has written three
  points; a run that fails before writing any has changed nothing. The previous
  run's output is never deleted first "to make room".
* **An interrupted run is visible as interrupted.** Startup sweeps any run still
  marked ``running`` into ``failed`` with the category ``interrupted``, because
  a run whose process is gone is not still running, and a spinner that never
  stops is the worst of the available lies.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"

# Plain language for the categories a run can fail with. The App Server's own
# error text never reaches here (appserver/errors.py); these are keyed by
# category, like every other failure surface in this codebase.
FAILURE_DETAIL = {
    "interrupted": (
        "This build was interrupted — Vademecum stopped while it was running. "
        "Anything it had already written was kept. Run it again when you are ready."
    ),
    "cancelled": "You stopped this build. Anything already written was kept.",
    "timeout": (
        "A model request timed out. Some content may already have been sent; "
        "we cannot tell how far it got. No new learning content or coverage was "
        "saved. Previous reference text and history are unchanged; newly "
        "reported safety notices may still hold affected questions."
    ),
    "unavailable": (
        "The Codex connection dropped. If the request had already left this Mac "
        "we cannot tell how far it got; nothing was written, and previous "
        "material for this pile is unchanged."
    ),
    "signed_out": (
        "Codex needs a ChatGPT sign-in to finish this build. Earlier stages may "
        "already have sent content. No learning material or coverage was "
        "replaced; newly reported safety notices may still hold affected "
        "questions. Sign in on the Model page and try again."
    ),
    "rate_limited": (
        "Your Codex usage limit has been reached, so the build stopped. Previous "
        "material for this pile is unchanged."
    ),
    "invalid_output": (
        "The model's reply did not match the required structure, so it was "
        "rejected rather than stored. Previous material for this pile is unchanged."
    ),
    "no_sources": (
        "This pile has no readable, included sources to build from."
    ),
    "evidence_unavailable": (
        "The literature search could not finish, so this build stopped before "
        "saving any learning material. Previous questions and references are "
        "kept, and the same source passages are available to retry. Any newly "
        "reported correction or retraction still puts affected questions on hold."
    ),
    "evidence_changed": (
        "A paper was corrected, retracted, or identified as a notice while this "
        "build ran. No new learning material or coverage was saved. Review the "
        "evidence and retry; the previous references and history are kept."
    ),
    "protocol": "Codex answered with something this version of Vademecum could not read.",
    # Host mode (ADR 0009): the model work was ChatGPT's, and it did not arrive.
    "host_expired": (
        "This build waited for ChatGPT to return its result and nothing arrived "
        "in time. No learning material or coverage was saved; previous material "
        "for this pile is unchanged. Start the build again from ChatGPT."
    ),
    "host_abandoned": (
        "This build was stopped while it was waiting for ChatGPT. No learning "
        "material or coverage was saved; previous material for this pile is unchanged."
    ),
    "refused": "Codex refused the request.",
    "internal": "Something went wrong on this Mac while building. Nothing was lost.",
}


@dataclass(frozen=True)
class GenerationRun:
    id: str
    pile_id: str
    kind: str
    status: str
    stage: str
    failure_category: str
    source_count: int
    excerpt_count: int
    excerpt_chars: int
    point_count: int
    question_count: int
    held_count: int
    started_at: str
    heartbeat_at: str
    finished_at: str | None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_detail"] = (
            FAILURE_DETAIL.get(self.failure_category, FAILURE_DETAIL["internal"])
            if self.failure_category
            else ""
        )
        return data


def _run(row: sqlite3.Row) -> GenerationRun:
    return GenerationRun(
        id=row["id"],
        pile_id=row["pile_id"],
        kind=row["kind"],
        status=row["status"],
        stage=row["stage"],
        failure_category=row["failure_category"],
        source_count=row["source_count"],
        excerpt_count=row["excerpt_count"],
        excerpt_chars=row["excerpt_chars"],
        point_count=row["point_count"],
        question_count=row["question_count"],
        held_count=row["held_count"],
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
        finished_at=row["finished_at"],
    )


def start_run(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    source_count: int,
    excerpt_count: int,
    excerpt_chars: int,
) -> GenerationRun:
    run_id = new_id("gen")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO generation_runs (id, pile_id, kind, status, stage,"
            " failure_category, source_count, excerpt_count, excerpt_chars,"
            " point_count, question_count, held_count, started_at, heartbeat_at, finished_at)"
            " VALUES (?, ?, 'synthesis', 'running', 'starting', '', ?, ?, ?, 0, 0, 0, ?, ?, NULL)",
            (run_id, pile_id, source_count, excerpt_count, excerpt_chars, now, now),
        )
    return get_run(connection, run_id)


def get_run(connection: sqlite3.Connection, run_id: str) -> GenerationRun:
    row = connection.execute(
        "SELECT * FROM generation_runs WHERE id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("generation run", run_id)
    return _run(row)


def latest_run(connection: sqlite3.Connection, pile_id: str) -> GenerationRun | None:
    row = connection.execute(
        "SELECT * FROM generation_runs WHERE pile_id = ?"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (pile_id,),
    ).fetchone()
    return None if row is None else _run(row)


def running_run(connection: sqlite3.Connection, pile_id: str) -> GenerationRun | None:
    row = connection.execute(
        "SELECT * FROM generation_runs WHERE pile_id = ? AND status = 'running'"
        " ORDER BY started_at DESC LIMIT 1",
        (pile_id,),
    ).fetchone()
    return None if row is None else _run(row)


def list_runs(connection: sqlite3.Connection, *, limit: int = 20) -> list[GenerationRun]:
    rows = connection.execute(
        "SELECT * FROM generation_runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_run(row) for row in rows]


def set_stage(connection: sqlite3.Connection, run_id: str, stage: str) -> None:
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE generation_runs SET stage = ?, heartbeat_at = ? WHERE id = ?",
            (stage, utc_now(), run_id),
        )


def finish_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    status: str,
    failure_category: str = "",
    point_count: int = 0,
    question_count: int = 0,
    held_count: int = 0,
) -> GenerationRun:
    if status not in {SUCCEEDED, FAILED, CANCELLED}:
        raise ValueError(f"unknown run status {status!r}")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE generation_runs SET status = ?, failure_category = ?, stage = '',"
            " point_count = ?, question_count = ?, held_count = ?,"
            " heartbeat_at = ?, finished_at = ? WHERE id = ?",
            (
                status,
                failure_category,
                point_count,
                question_count,
                held_count,
                now,
                now,
                run_id,
            ),
        )
    return get_run(connection, run_id)


def sweep_interrupted(connection: sqlite3.Connection) -> int:
    """Close out runs whose process is gone. Called once, at startup.

    Safe to run unconditionally: a run is only ``running`` while a task in this
    process is driving it, and at startup there are no such tasks. Nothing this
    touches deletes any output the run had already written.
    """
    now = utc_now()
    with transaction(connection) as tx:
        cursor = tx.execute(
            "UPDATE generation_runs SET status = 'failed', failure_category = 'interrupted',"
            " stage = '', heartbeat_at = ?, finished_at = ? WHERE status = 'running'",
            (now, now),
        )
        rows = cursor.rowcount
        # A literature check is the same kind of claim: "running" is only true
        # while a task is driving it.
        tx.execute(
            "UPDATE literature_checks SET status = 'failed', failure_category = 'interrupted',"
            " finished_at = ? WHERE status = 'running'",
            (now,),
        )
    return rows
