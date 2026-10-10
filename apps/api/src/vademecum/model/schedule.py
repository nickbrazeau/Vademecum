"""Builds in the background (ADR 0033, feedback of 10 October; the timer of ADR 0018 remains
as an option): the Mac works through the piles by itself.

Continuous, by default: whenever the Mac is awake and the owner's standing consent is
given, one batch at a time from the pile with the most text still to build, the
encyclopedia compiled every few batches, backing off after a failure or when the model's
allowance is used up, and carrying on at once when the Mac wakes. Progress is saved per
batch, so sleep costs at most the batch in flight.

The timer, when chosen instead:

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
# Continuous building (ADR 0033).
BETWEEN_BATCHES_SECONDS = 5.0
IDLE_SECONDS = 600.0  # nothing to build: look again in ten minutes, or when woken
LIMITED_SECONDS = 900.0  # the model's allowance is used up: look again in a quarter of an hour
COMPILE_EVERY = 5
BACKOFF_START = 60.0
BACKOFF_MAX = 1800.0
WOKE_AFTER_SECONDS = 120.0
RUN_TIMEOUT_SECONDS = 1800.0
POLL_SECONDS = 1.0

NEEDS_CODEX = (
    "Scheduled builds need the Mac's own model connection -- Codex or Claude -- which does the "
    "model turns while nobody is in a conversation. This Vademecum is in host mode; start it with "
    "`mcp.sh setup login --model codex` or `--model claude` and sign in on the Model page."
)


def _piles_by_unbuilt(connection) -> list[tuple[str, str]]:
    """Piles with text still to build, the most first."""
    rows = connection.execute(
        """
        SELECT p.id, p.title,
               SUM(MAX(0, g.char_count - MIN(g.covered_upto, g.char_count))) AS left_to_build
          FROM source_segments g
          JOIN sources s ON s.id = g.source_id
          JOIN piles p ON p.id = s.pile_id
         GROUP BY p.id
        HAVING left_to_build > 0
         ORDER BY left_to_build DESC
        """
    ).fetchall()
    return [(row["id"], row["title"]) for row in rows]


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
        # What the continuous builder is doing, said plainly for Foundation (feedback of 10 October).
        self._status: dict[str, Any] = {"state": "starting", "reason": "", "next_attempt_at": None, "last_error": "", "batches": 0}
        self._failures = 0

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
            "next_run_at": due.isoformat(timespec="minutes") if due and not schedule["continuous"] else None,
            "last_run": store.get_last_run(connection),
            "builder": dict(self._status),
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

    def _say(self, state: str, reason: str, wait: float | None = None, error: str = "") -> None:
        self._status.update(
            state=state,
            reason=reason,
            next_attempt_at=(datetime.now().astimezone() + timedelta(seconds=wait)).isoformat(timespec="seconds") if wait else None,
        )
        if error:
            self._status["last_error"] = error

    async def _continuous_step(self, schedule: dict[str, Any]) -> float:
        """One step of building in the background; returns how long to wait before the next."""
        paused = schedule.get("paused_until")
        if paused:
            try:
                until = datetime.fromisoformat(str(paused))
                if until.tzinfo is None:
                    until = until.astimezone()
            except ValueError:
                until = None
            if until is not None and until > self._now():
                wait = min((until - self._now()).total_seconds(), MAX_SLEEP_SECONDS)
                self._say("paused", f"Paused by you until {until.strftime('%H:%M')}.", wait)
                return wait
        from ..storage import model_seen

        connection = connect(self._database_path)
        try:
            seen = model_seen.read(connection) or {}
            order = _piles_by_unbuilt(connection)
        finally:
            connection.close()
        if seen.get("limited"):
            self._say("limited", "The model connection's allowance is used up for now; building resumes when it resets.", LIMITED_SECONDS)
            return LIMITED_SECONDS
        if not order:
            self._say("idle", "Everything read in has been built. New files are picked up as they arrive.", IDLE_SECONDS)
            return IDLE_SECONDS
        for pile_id, title in order:
            self._say("building", f"Building from {title}.")
            async with self._running:
                self._active = True
                try:
                    outcome = await self._run_pile(pile_id, title, 1)
                finally:
                    self._active = False
            if outcome["status"] == "nothing_to_build":
                continue
            if outcome["status"] in ("succeeded", "busy"):
                self._failures = 0
                if outcome["status"] == "succeeded":
                    self._status["batches"] += 1
                    if self._status["batches"] % COMPILE_EVERY == 0 and self._after is not None:
                        self._say("building", "Writing encyclopedia pages from what was built.")
                        try:
                            await self._after()
                        except Exception as exc:  # noqa: BLE001 - recorded, never fatal
                            logger.error("background_compile_failed error=%s", type(exc).__name__)
                self._say("building", f"Building from {title}.", BETWEEN_BATCHES_SECONDS)
                return BETWEEN_BATCHES_SECONDS
            self._failures += 1
            wait = min(BACKOFF_START * 2 ** (self._failures - 1), BACKOFF_MAX)
            detail = outcome.get("detail") or outcome["status"]
            self._say("waiting", f"The last batch from {title} did not finish; trying again shortly.", wait, error=str(detail)[:300])
            logger.info("background_build_backoff seconds=%d", int(wait))
            return wait
        self._say("idle", "Everything read in has been built. New files are picked up as they arrive.", IDLE_SECONDS)
        return IDLE_SECONDS

    async def _loop(self) -> None:
        import time

        wall, mono = time.time(), time.monotonic()
        while True:
            # The Mac slept if the wall clock moved on much further than the monotonic one:
            # carry on at once from where building stopped (saved per batch).
            now_wall, now_mono = time.time(), time.monotonic()
            if (now_wall - wall) - (now_mono - mono) > WOKE_AFTER_SECONDS:
                logger.info("background_build_woke slept_seconds=%d", int((now_wall - wall) - (now_mono - mono)))
                self._failures = 0
            wall, mono = now_wall, now_mono
            connection = connect(self._database_path)
            try:
                schedule = store.get_schedule(connection)
            finally:
                connection.close()
            if schedule["enabled"] and self.can_run and schedule["continuous"]:
                try:
                    wait = await self._continuous_step(schedule)
                except Exception as exc:  # noqa: BLE001 - the builder reports and continues
                    logger.error("background_build_failed error=%s", type(exc).__name__)
                    wait = BACKOFF_START
                assert self._wake is not None
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
                continue
            if not schedule["enabled"]:
                self._say("off", "Building in the background is off.")
            elif not self.can_run:
                self._say("blocked", NEEDS_CODEX)
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
