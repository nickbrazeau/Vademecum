"""Builds on a timer (ADR 0018): the Mac works through the piles by itself.

At each scheduled time of day, while enabled, every pile with passages not
yet built from is taken through up to ``batches_per_run`` batches, one build
at a time, exactly as the Build button would: propose a batch, record it,
start the run, wait for it. The owner's consent is standing: given once, in
the dashboard or through a tool, having read what each run sends, and
withdrawn by turning the schedule off. Each run is recorded so Today can say
what happened.

Only the Mac's own Codex connection can do this unattended (ADR 0006). In
host mode the model is the assistant in a conversation, and on a timer there
is no conversation, so the schedule refuses to run and says why.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import connect
from ..storage import jobs
from ..storage import piles as pile_store
from ..storage import schedule as store
from ..storage import sources as source_store
from ..storage.common import utc_now

logger = logging.getLogger("vademecum.schedule")

MAX_SLEEP_SECONDS = 600.0
RUN_TIMEOUT_SECONDS = 1800.0
POLL_SECONDS = 1.0

NEEDS_CODEX = (
    "Scheduled builds need the Mac's own model connection -- Codex or Claude -- which does the "
    "model turns while nobody is in a conversation. This Vademecum is in host mode; start it with "
    "`mcp.sh setup login --model codex` or `--model claude` and sign in on the Model page."
)


def next_due(times: list[str], now: datetime) -> datetime:
    """The next of *times* (local HH:MM) strictly after *now*."""
    candidates: list[datetime] = []
    for value in times:
        hour, minute = (int(part) for part in value.split(":"))
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidates.append(today if today > now else today + timedelta(days=1))
    return min(candidates)


class BuildScheduler:
    """Owns the timer and the one-at-a-time runs. Safe to construct in any mode."""

    def __init__(
        self,
        *,
        database_path: Path,
        service: Any,
        model_mode: str,
        now: Callable[[], datetime] | None = None,
        before: Callable[[], Awaitable[Any]] | None = None,
        after: Callable[[], Awaitable[Any]] | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._service = service
        self._model_mode = model_mode
        # Work that goes before the builds in a run: reading any exam report
        # still waiting (ADR 0020). And after them: compiling the encyclopedia
        # and writing board questions from what was built (ADR 0023).
        self._before = before
        self._after = after
        self._now = now or (lambda: datetime.now().astimezone())
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._running = asyncio.Lock()
        self._active = False

    # --- what the routes read ----------------------------------------------------

    @property
    def can_run(self) -> bool:
        return self._model_mode in ("codex", "claude")

    @property
    def is_running(self) -> bool:
        return self._active

    def describe(self, connection) -> dict[str, Any]:
        schedule = store.get_schedule(connection)
        due = next_due(schedule["times"], self._now()) if schedule["enabled"] else None
        return {
            **schedule,
            "model_mode": self._model_mode,
            "can_run": self.can_run,
            "blocked_reason": "" if self.can_run else NEEDS_CODEX,
            "running": self._active,
            "next_run_at": due.isoformat(timespec="minutes") if due else None,
            "last_run": store.get_last_run(connection),
        }

    # --- the timer -------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._wake = asyncio.Event()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def reschedule(self) -> None:
        """Settings changed: compute the next due time again now."""
        if self._wake is not None:
            self._wake.set()

    async def _loop(self) -> None:
        while True:
            connection = connect(self._database_path)
            try:
                schedule = store.get_schedule(connection)
            finally:
                connection.close()
            now = self._now()
            if schedule["enabled"] and self.can_run:
                due = next_due(schedule["times"], now)
                wait = min(max((due - now).total_seconds(), 1.0), MAX_SLEEP_SECONDS)
            else:
                due = None
                wait = MAX_SLEEP_SECONDS
            assert self._wake is not None
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
                continue  # settings changed; look again
            except asyncio.TimeoutError:
                pass
            if due is not None and self._now() >= due:
                try:
                    await self.run_all(reason="scheduled")
                except Exception as exc:  # noqa: BLE001 - the timer reports and continues
                    logger.error("scheduled_build_failed error=%s", type(exc).__name__)

    # --- a run -----------------------------------------------------------------------

    async def run_all(self, *, reason: str = "requested") -> dict[str, Any]:
        """Every pile, up to batches_per_run batches each, one build at a time."""
        if not self.can_run:
            report = {"at": utc_now(), "reason": reason, "ran": False, "note": NEEDS_CODEX, "piles": []}
            self._record(report)
            return report
        if self._running.locked():
            return {"at": utc_now(), "reason": reason, "ran": False, "note": "A scheduled run is already in progress.", "piles": []}
        async with self._running:
            self._active = True
            try:
                return await self._run_all(reason)
            finally:
                self._active = False

    async def _run_all(self, reason: str) -> dict[str, Any]:
        connection = connect(self._database_path)
        try:
            schedule = store.get_schedule(connection)
            piles = pile_store.list_piles(connection)
        finally:
            connection.close()
        reports_read = 0
        if self._before is not None:
            try:
                reports_read = int(await self._before() or 0)
            except Exception as exc:  # noqa: BLE001 - a report that cannot be read is recorded on itself
                logger.error("scheduled_reports_failed error=%s", type(exc).__name__)
        results: list[dict[str, Any]] = []
        for pile in piles:
            outcome = await self._run_pile(pile.id, pile.title, schedule["batches_per_run"])
            results.append(outcome)
        after: Any = None
        if self._after is not None:
            try:
                after = await self._after()
            except Exception as exc:  # noqa: BLE001 - recorded on the run, never fatal
                logger.error("scheduled_after_failed error=%s", type(exc).__name__)
        report = {"at": utc_now(), "reason": reason, "ran": True, "note": "", "piles": results, "reports_read": reports_read, "encyclopedia": after}
        self._record(report)
        logger.info("scheduled_build piles=%d batches=%d", len(results), sum(r["batches"] for r in results))
        return report

    async def _run_pile(self, pile_id: str, title: str, batches_per_run: int) -> dict[str, Any]:
        outcome = {"pile_id": pile_id, "title": title, "batches": 0, "points": 0, "status": "nothing_to_build", "detail": ""}
        for _ in range(batches_per_run):
            connection = connect(self._database_path)
            try:
                if self._service.is_running(pile_id):
                    outcome.update(status="busy", detail="A build was already running for this pile.")
                    return outcome
                batch = source_store.next_batch(connection, pile_id)
                if not batch.excerpts:
                    return outcome
                batch_id = source_store.record_batch(connection, pile_id, batch)
                run = jobs.start_run(
                    connection,
                    pile_id=pile_id,
                    source_count=source_store.pile_source_summary(connection, pile_id)["usable"],
                    excerpt_count=len(batch.excerpts),
                    excerpt_chars=batch.excerpt_chars,
                )
                run_id = run.id
            finally:
                connection.close()
            await self._service.start(pile_id=pile_id, run_id=run_id, batch_id=batch_id)
            finished = await self._wait(pile_id)
            connection = connect(self._database_path)
            try:
                run = jobs.get_run(connection, run_id)
            finally:
                connection.close()
            outcome["batches"] += 1
            outcome["points"] += int(getattr(run, "point_count", 0) or 0)
            outcome["status"] = run.status if finished else "timed_out"
            if run.status != "succeeded":
                outcome["detail"] = run.as_dict().get("failure_detail", "")
                return outcome
        return outcome

    async def _wait(self, pile_id: str) -> bool:
        deadline = asyncio.get_running_loop().time() + RUN_TIMEOUT_SECONDS
        while self._service.is_running(pile_id):
            if asyncio.get_running_loop().time() > deadline:
                return False
            await asyncio.sleep(POLL_SECONDS)
        return True

    def _record(self, report: dict[str, Any]) -> None:
        connection = connect(self._database_path)
        try:
            store.record_run(connection, report)
        finally:
            connection.close()
