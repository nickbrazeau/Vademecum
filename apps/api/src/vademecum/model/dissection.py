"""The dissection agent (ADR 0023, second part): one pile, worked through until it is done.

Given a pile -- a textbook, say -- the agent builds batch after batch exactly as
the Build button would, and every few batches compiles the encyclopedia from
what was built: pages for the new topics, a literature review per page, board
questions per page. It does not stop on a failure: a failed turn, a timed-out
run or an unreachable provider is recorded, waited out with a growing pause,
and tried again. When the pile is fully built and every page is current it
says so and keeps watching, so a file added to the pile later is taken up
without being asked. Its state is written after every step, so a restart of
Vademecum resumes it where it was.

Only the Mac's own model connection can do this unattended (codex or claude
mode); in host mode it refuses and says why. Starting it is a standing consent
to what each batch and each compile send, given once, in the dashboard or
through a tool, having read the disclosure, and withdrawn by stopping it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..db import connect
from ..storage import encyclopedia as store
from ..storage import jobs
from ..storage import piles as pile_store
from ..storage import sources as source_store
from ..storage.common import utc_now

logger = logging.getLogger("vademecum.dissection")

NEEDS_MODEL = (
    "Dissecting a pile needs the Mac's own model connection -- Codex or Claude -- which does the "
    "model turns while nobody is in a conversation. This Vademecum is in host mode."
)
BATCHES_BETWEEN_COMPILES = 5
RUN_TIMEOUT_SECONDS = 3600.0
POLL_SECONDS = 2.0
IDLE_SECONDS = 600.0
BACKOFF_BASE_SECONDS = 60.0
BACKOFF_MAX_SECONDS = 1800.0


class Dissector:
    def __init__(
        self,
        *,
        database_path: Path,
        service: Any,
        refresh: Callable[..., Awaitable[dict[str, Any]]] | None,
        model_mode: str,
        idle_seconds: float = IDLE_SECONDS,
        backoff_base: float = BACKOFF_BASE_SECONDS,
        run_timeout: float = RUN_TIMEOUT_SECONDS,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._database_path = Path(database_path)
        self._service = service
        self._refresh = refresh
        self._model_mode = model_mode
        self._idle_seconds = idle_seconds
        self._backoff_base = backoff_base
        self._run_timeout = run_timeout
        self._poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None

    # --- what the routes read ----------------------------------------------------

    @property
    def can_run(self) -> bool:
        return self._model_mode in ("codex", "claude") and self._refresh is not None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def describe(self, connection) -> dict[str, Any]:
        state = store.get_dissection(connection) or {"status": "idle"}
        pile_title = ""
        pile_coverage: dict[str, Any] | None = None
        pile_id = state.get("pile_id")
        if pile_id:
            try:
                pile = pile_store.get_pile(connection, pile_id)
                pile_title = pile.title
                pile_coverage = pile.as_dict().get("coverage")
            except Exception:  # noqa: BLE001 - a pile removed under the agent is reported as such
                pile_title = "(pile no longer exists)"
        return {
            **state,
            "pile_title": pile_title,
            "coverage": pile_coverage,
            "running": self.is_running,
            "can_run": self.can_run,
            "blocked_reason": "" if self.can_run else NEEDS_MODEL,
            "encyclopedia": store.entry_counts(connection),
        }

    # --- control -------------------------------------------------------------------

    def start(self, pile_id: str) -> dict[str, Any]:
        """Begin, or resume, on one pile. The state is the consent record."""
        connection = connect(self._database_path)
        try:
            pile_store.get_pile(connection, pile_id)
            current = store.get_dissection(connection) or {}
            fresh = current.get("pile_id") != pile_id
            state = {
                "pile_id": pile_id,
                "status": "running",
                "phase": "starting",
                "started_at": utc_now() if fresh or current.get("status") != "running" else current.get("started_at"),
                "consent_at": utc_now() if fresh or not current.get("consent_at") else current.get("consent_at"),
                "batches_done": 0 if fresh else int(current.get("batches_done") or 0),
                "points_built": 0 if fresh else int(current.get("points_built") or 0),
                "pages_compiled": 0 if fresh else int(current.get("pages_compiled") or 0),
                "questions_written": 0 if fresh else int(current.get("questions_written") or 0),
                "failures": 0,
                "last_error": "",
                "last_activity_at": utc_now(),
                "next_retry_at": None,
                "completed_at": None,
            }
            store.set_dissection(connection, state)
        finally:
            connection.close()
        if not self.is_running:
            self._task = asyncio.create_task(self._loop(pile_id))
        else:
            self._kick()
        return state

    def resume(self) -> bool:
        """At startup: carry on with a dissection that was running when the process stopped."""
        if not self.can_run:
            return False
        connection = connect(self._database_path)
        try:
            state = store.get_dissection(connection)
        finally:
            connection.close()
        if not state or state.get("status") != "running" or not state.get("pile_id"):
            return False
        self._task = asyncio.create_task(self._loop(str(state["pile_id"])))
        logger.info("dissection_resumed")
        return True

    async def stop(self, *, by_owner: bool = False) -> dict[str, Any] | None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if by_owner:
            connection = connect(self._database_path)
            try:
                state = store.get_dissection(connection) or {}
                state.update(status="stopped", phase="stopped", next_retry_at=None, last_activity_at=utc_now())
                store.set_dissection(connection, state)
                return state
            finally:
                connection.close()
        return None

    def _kick(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # --- the loop --------------------------------------------------------------------

    def _update(self, **changes: Any) -> dict[str, Any]:
        connection = connect(self._database_path)
        try:
            state = store.get_dissection(connection) or {}
            state.update(changes)
            state["last_activity_at"] = utc_now()
            store.set_dissection(connection, state)
            return state
        finally:
            connection.close()

    async def _pause(self, seconds: float) -> None:
        assert self._wake is not None
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.0, seconds))
        except asyncio.TimeoutError:
            pass

    async def _loop(self, pile_id: str) -> None:
        self._wake = asyncio.Event()
        failures = 0
        since_compile = 0
        while True:
            try:
                built = await self._build_one(pile_id)
                if built["outcome"] == "built":
                    failures = 0
                    since_compile += 1
                    state = self._update(
                        phase="building",
                        batches_done=int(self._state_value("batches_done")) + 1,
                        points_built=int(self._state_value("points_built")) + built["points"],
                        failures=0,
                        last_error="",
                        next_retry_at=None,
                    )
                    logger.info("dissection_batch batches=%d points=%d", state["batches_done"], built["points"])
                elif built["outcome"] == "busy":
                    self._update(phase="waiting", last_error="")
                    await self._pause(self._poll_seconds * 5)
                    continue
                elif built["outcome"] == "failed":
                    failures += 1
                    await self._back_off(failures, built["detail"])
                    continue
                if built["outcome"] == "nothing" or since_compile >= BATCHES_BETWEEN_COMPILES:
                    since_compile = 0
                    self._update(phase="compiling")
                    report = await self._refresh(reason="dissection")  # type: ignore[misc]
                    pages = report.get("pages", {}) if isinstance(report, dict) else {}
                    questions = report.get("questions", {}) if isinstance(report, dict) else {}
                    self._update(
                        pages_compiled=int(self._state_value("pages_compiled")) + int(pages.get("compiled", 0) or 0),
                        questions_written=int(self._state_value("questions_written")) + int(questions.get("written", 0) or 0),
                    )
                    if int(pages.get("failed", 0) or 0) and not int(pages.get("compiled", 0) or 0):
                        failures += 1
                        await self._back_off(failures, "Compiling failed; the model connection may be unavailable.")
                        continue
                    failures = 0
                    if built["outcome"] == "nothing":
                        produced = int(pages.get("compiled", 0) or 0) + int(questions.get("written", 0) or 0)
                        if int(pages.get("remaining", 0) or 0) == 0 and produced == 0:
                            # Nothing left to build, nothing left to compile, and the last
                            # pass wrote nothing new: done, and watching. A page whose
                            # questions keep coming back as duplicates is tried again on
                            # the next watch, not in a tight loop.
                            state = self._update(phase="complete", completed_at=self._state_value("completed_at") or utc_now())
                            logger.info("dissection_complete batches=%s pages=%s", state.get("batches_done"), state.get("pages_compiled"))
                            await self._pause(self._idle_seconds)
                        # Otherwise the last pass produced something: go round again at once.
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the agent outlives any one failure
                failures += 1
                logger.error("dissection_error error=%s", type(exc).__name__)
                await self._back_off(failures, f"Something failed on this Mac ({type(exc).__name__}).")

    def _state_value(self, key: str) -> Any:
        connection = connect(self._database_path)
        try:
            return (store.get_dissection(connection) or {}).get(key) or 0
        finally:
            connection.close()

    async def _back_off(self, failures: int, detail: str) -> None:
        seconds = min(self._backoff_base * (2 ** max(0, failures - 1)), BACKOFF_MAX_SECONDS)
        from datetime import datetime, timedelta, timezone

        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._update(phase="backing_off", failures=failures, last_error=detail[:300], next_retry_at=retry_at)
        logger.info("dissection_backoff failures=%d seconds=%d", failures, int(seconds))
        await self._pause(seconds)

    async def _build_one(self, pile_id: str) -> dict[str, Any]:
        """One batch, as the Build button would do it; the outcome says what happened."""
        connection = connect(self._database_path)
        try:
            if self._service.is_running(pile_id):
                return {"outcome": "busy", "points": 0, "detail": ""}
            batch = source_store.next_batch(connection, pile_id)
            if not batch.excerpts:
                return {"outcome": "nothing", "points": 0, "detail": ""}
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
        self._update(phase="building")
        await self._service.start(pile_id=pile_id, run_id=run_id, batch_id=batch_id)
        deadline = asyncio.get_running_loop().time() + self._run_timeout
        while self._service.is_running(pile_id):
            if asyncio.get_running_loop().time() > deadline:
                await self._service.cancel(pile_id)
                return {"outcome": "failed", "points": 0, "detail": "A build did not finish in time and was cancelled."}
            await asyncio.sleep(self._poll_seconds)
        connection = connect(self._database_path)
        try:
            finished = jobs.get_run(connection, run_id)
        finally:
            connection.close()
        if finished.status != "succeeded":
            return {"outcome": "failed", "points": 0, "detail": finished.as_dict().get("failure_detail", "") or "The build failed."}
        return {"outcome": "built", "points": int(getattr(finished, "point_count", 0) or 0), "detail": ""}
