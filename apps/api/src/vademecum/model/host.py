"""ChatGPT as the model: pending turns and their submissions (ADR 0009).

In host mode Vademecum's server never calls a model. Every turn the pipeline
would have sent to Codex becomes a *pending turn*: a row holding exactly what
the model may look at (the same instructions, rules, fenced material and output
schema Codex was given) and a deadline. The learner's ChatGPT reads it through
one tool and answers through another. The answer is validated against the
schema -- the same validator, the same ``additionalProperties: false`` -- and
only then does the pipeline continue, exactly where a Codex reply would have
resumed it.

What this module does not do is as deliberate as what it does. It has no field
in which a submission can assert support or certainty; it does not relax a
schema; it does not accept a second submission for a turn; it does not let a
turn outlive its deadline. The mechanical checks downstream -- quotes against
consented text, evidence quotes against retrieved abstracts, PMIDs against
retrieval -- are unchanged and are what make the result trustworthy, not the
identity of the model that produced it.

Two shapes of use:

* A **build** runs as a task on this process and awaits each turn. The runner
  handed to the pipeline (``HostTurns.scoped``) creates the row and parks on an
  in-memory future until ``submit`` resolves it. A restart loses the future, so
  a startup sweep abandons every pending row and the run fails honestly.
* A **grade** is request-scoped, so nothing waits: ``create`` returns the row,
  and a later ``submit`` returns the result together with the context that was
  stored beside it (the snapshot of what was asked).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..appserver.errors import BridgeError
from ..appserver.turns import TurnResult, assert_enforceable, validate_against
from ..db import connect, transaction
from ..storage.common import new_id, utc_now
from . import schemas

logger = logging.getLogger("vademecum.model")

# How long a turn may wait for ChatGPT. Generous: the learner may be reading.
DEFAULT_TURN_TTL_SECONDS = 1800.0

HOW_TO_SUBMIT = (
    "Do this turn's work yourself, following `rules` and using only `material`. "
    "Then submit one JSON object that matches `output_schema` exactly: every "
    "required field, only the listed fields, values within the stated enums and "
    "lengths. Quotes must be copied verbatim from `material`."
)


class HostTurnExpired(BridgeError):
    """The deadline passed with no submission."""

    category = "host_expired"


class HostTurnAbandoned(BridgeError):
    """The waiting task was cancelled or the process stopped."""

    category = "host_abandoned"


class SubmissionRefused(Exception):
    """A submission that cannot be accepted. ``code`` is a closed set."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


REFUSALS = {
    "unknown_turn": "No pending turn has that id. Ask for the pending turns again.",
    "turn_expired": (
        "That turn's deadline passed before a result arrived. The run it belonged "
        "to has stopped; start again."
    ),
    "already_submitted": "That turn already received a result. Nothing changed.",
    "invalid_submission": (
        "The result did not match the required structure, so it was refused and "
        "nothing was recorded. Compare it with output_schema: every required field "
        "present, no extra fields, enum values exact, lengths within limits."
    ),
}


def kind_of(schema: Any) -> str:
    """Which of the four exchanges a schema belongs to. Identity, like the tests."""
    if schema is schemas.SYNTHESIS_SCHEMA:
        return "synthesis"
    if schema is schemas.EVIDENCE_SCHEMA:
        return "evidence"
    if schema is schemas.ASSESSMENT_SCHEMA:
        return "assessment"
    if schema is schemas.GRADING_SCHEMA:
        return "grading"
    if schema is schemas.REPORT_SCHEMA:
        return "report"
    raise ValueError("unknown output schema")


@dataclass(frozen=True)
class PendingTurn:
    id: str
    kind: str
    scope_kind: str
    scope_id: str
    instructions: str
    rules: str
    material: str
    output_schema: dict[str, Any]
    status: str
    created_at: str
    expires_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.id,
            "kind": self.kind,
            "scope": {"kind": self.scope_kind, "id": self.scope_id},
            "instructions": self.instructions,
            "rules": self.rules,
            "material": self.material,
            "output_schema": self.output_schema,
            "expires_at": self.expires_at,
            "how_to_submit": HOW_TO_SUBMIT,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class HostTurns:
    """The registry of pending turns for one database."""

    def __init__(self, database_path: Path, *, ttl_seconds: float = DEFAULT_TURN_TTL_SECONDS) -> None:
        self._database_path = database_path
        self._ttl = ttl_seconds
        self._waiters: dict[str, asyncio.Future[TurnResult]] = {}

    def _open(self) -> sqlite3.Connection:
        return connect(self._database_path)

    # --- the seam the pipeline uses ------------------------------------------

    def scoped(self, scope_kind: str, scope_id: str) -> Callable[[], "HostTurnRunner"]:
        """A turn factory for one run: what ``BuildService`` hands the pipeline."""

        def factory() -> HostTurnRunner:
            return HostTurnRunner(self, scope_kind=scope_kind, scope_id=scope_id)

        return factory

    # --- rows -----------------------------------------------------------------

    def create(
        self,
        *,
        kind: str,
        scope_kind: str,
        scope_id: str,
        instructions: str,
        rules: str,
        material: str,
        output_schema: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> PendingTurn:
        assert_enforceable(output_schema)
        turn_id = new_id("turn")
        created = _now()
        expires = created + timedelta(seconds=self._ttl)
        payload = {
            "instructions": instructions,
            "rules": rules,
            "material": material,
            "output_schema": output_schema,
        }
        connection = self._open()
        try:
            with transaction(connection) as tx:
                tx.execute(
                    "INSERT INTO pending_turns (id, kind, scope_kind, scope_id, payload, context,"
                    " status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (
                        turn_id,
                        kind,
                        scope_kind,
                        scope_id,
                        json.dumps(payload),
                        json.dumps(context or {}),
                        created.isoformat(timespec="seconds"),
                        expires.isoformat(timespec="seconds"),
                    ),
                )
        finally:
            connection.close()
        logger.info("host_turn_created kind=%s scope=%s", kind, scope_kind)
        return self.get(turn_id)  # type: ignore[return-value]

    def get(self, turn_id: str) -> PendingTurn | None:
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT * FROM pending_turns WHERE id = ?", (turn_id,)
            ).fetchone()
        finally:
            connection.close()
        return None if row is None else _turn(row)

    def pending(self, scope_kind: str, scope_id: str) -> list[PendingTurn]:
        """Live pending turns for a scope; stale ones are expired on the way."""
        self._expire_stale()
        connection = self._open()
        try:
            rows = connection.execute(
                "SELECT * FROM pending_turns WHERE scope_kind = ? AND scope_id = ?"
                " AND status = 'pending' ORDER BY created_at, id",
                (scope_kind, scope_id),
            ).fetchall()
        finally:
            connection.close()
        return [_turn(row) for row in rows]

    def submit(self, turn_id: str, result: Any) -> tuple[PendingTurn, dict[str, Any]]:
        """Accept one result for one turn, once, if it matches the schema.

        Returns the turn and the context stored beside it. Resolves the waiter
        if a task is parked on this turn.
        """
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT * FROM pending_turns WHERE id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                raise SubmissionRefused("unknown_turn", REFUSALS["unknown_turn"])
            if row["status"] == "submitted":
                raise SubmissionRefused("already_submitted", REFUSALS["already_submitted"])
            if row["status"] != "pending" or _parse(row["expires_at"]) < _now():
                if row["status"] == "pending":
                    self._mark(connection, turn_id, "expired")
                raise SubmissionRefused("turn_expired", REFUSALS["turn_expired"])
            payload = json.loads(row["payload"])
            if not isinstance(result, dict) or not validate_against(result, payload["output_schema"]):
                logger.info("host_turn_refused kind=%s", row["kind"])
                raise SubmissionRefused("invalid_submission", REFUSALS["invalid_submission"])
            self._mark(connection, turn_id, "submitted")
            context = json.loads(row["context"] or "{}")
            turn = _turn(row)
        finally:
            connection.close()
        logger.info("host_turn_submitted kind=%s", turn.kind)
        waiter = self._waiters.pop(turn_id, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(
                TurnResult(
                    payload=result,
                    raw_chars=len(json.dumps(result)),
                    turn_id=turn_id,
                    duration_ms=0.0,
                )
            )
        return turn, context

    async def wait(self, turn_id: str) -> TurnResult:
        """Park until ``submit`` resolves the turn, its deadline passes, or the
        waiting task is cancelled."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TurnResult] = loop.create_future()
        self._waiters[turn_id] = future
        try:
            return await asyncio.wait_for(future, timeout=self._ttl)
        except asyncio.TimeoutError:
            self._set_status(turn_id, "expired")
            raise HostTurnExpired() from None
        except asyncio.CancelledError:
            self._set_status(turn_id, "abandoned")
            raise
        finally:
            self._waiters.pop(turn_id, None)

    async def wait_for_change(
        self, scope_kind: str, scope_id: str, *, after: str, timeout: float, still_running: Callable[[], bool]
    ) -> list[PendingTurn]:
        """The next pending turn(s) for a scope, or none once the scope is done.

        Polls, because the pipeline moves on its own loop between turns and
        may spend a while on a literature lookup before the next turn exists.
        """
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            turns = [turn for turn in self.pending(scope_kind, scope_id) if turn.id != after]
            if turns or not still_running():
                return turns
            if asyncio.get_running_loop().time() >= deadline:
                return []
            await asyncio.sleep(0.05)

    def abandon_scope(self, scope_kind: str, scope_id: str) -> int:
        connection = self._open()
        try:
            with transaction(connection) as tx:
                cursor = tx.execute(
                    "UPDATE pending_turns SET status = 'abandoned' WHERE scope_kind = ?"
                    " AND scope_id = ? AND status = 'pending'",
                    (scope_kind, scope_id),
                )
                return cursor.rowcount
        finally:
            connection.close()

    def sweep(self) -> int:
        """At startup: every pending turn belonged to a task that is gone."""
        connection = self._open()
        try:
            with transaction(connection) as tx:
                cursor = tx.execute(
                    "UPDATE pending_turns SET status = 'abandoned' WHERE status = 'pending'"
                )
                # Rows older than a day carry nothing anyone will read again.
                tx.execute(
                    "DELETE FROM pending_turns WHERE status != 'pending' AND created_at < ?",
                    ((_now() - timedelta(days=1)).isoformat(timespec="seconds"),),
                )
                return cursor.rowcount
        finally:
            connection.close()

    # --- helpers --------------------------------------------------------------

    def _set_status(self, turn_id: str, status: str) -> None:
        connection = self._open()
        try:
            self._mark(connection, turn_id, status)
        finally:
            connection.close()

    @staticmethod
    def _mark(connection: sqlite3.Connection, turn_id: str, status: str) -> None:
        with transaction(connection) as tx:
            tx.execute(
                "UPDATE pending_turns SET status = ?, submitted_at = ? WHERE id = ?",
                (status, utc_now() if status == "submitted" else None, turn_id),
            )

    def _expire_stale(self) -> None:
        connection = self._open()
        try:
            with transaction(connection) as tx:
                tx.execute(
                    "UPDATE pending_turns SET status = 'expired' WHERE status = 'pending'"
                    " AND expires_at < ?",
                    (_now().isoformat(timespec="seconds"),),
                )
        finally:
            connection.close()


def _turn(row: sqlite3.Row) -> PendingTurn:
    payload = json.loads(row["payload"])
    return PendingTurn(
        id=row["id"],
        kind=row["kind"],
        scope_kind=row["scope_kind"],
        scope_id=row["scope_id"],
        instructions=payload["instructions"],
        rules=payload["rules"],
        material=payload["material"],
        output_schema=payload["output_schema"],
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
    )


class HostTurnRunner:
    """What the build pipeline sees: the same ``run`` signature as the Codex runner."""

    def __init__(self, turns: HostTurns, *, scope_kind: str, scope_id: str) -> None:
        self._turns = turns
        self._scope_kind = scope_kind
        self._scope_id = scope_id
        self._turn_id: str | None = None

    async def run(
        self,
        *,
        instructions: str,
        developer_instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        max_output_chars: int = 200_000,
    ) -> TurnResult:
        del max_output_chars  # the validator bounds every field; a whole-reply cap adds nothing here
        turn = self._turns.create(
            kind=kind_of(output_schema),
            scope_kind=self._scope_kind,
            scope_id=self._scope_id,
            instructions=instructions,
            rules=developer_instructions,
            material=prompt,
            output_schema=output_schema,
        )
        self._turn_id = turn.id
        return await self._turns.wait(turn.id)

    async def cancel(self) -> None:
        if self._turn_id is not None:
            self._turns._set_status(self._turn_id, "abandoned")
