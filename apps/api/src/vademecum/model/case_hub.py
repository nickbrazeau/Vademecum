"""The Case Series hub's keeper (ADR 0022): fetch on a timer, write notes when a model is here.

One asyncio task in the owner's process, like the literature watcher and the
build scheduler: no cron entry, no launch agent, nothing left behind. It is
off until the owner turns it on, and it runs only on Domi -- Foris shows what
sync brought it. A refresh fetches every enabled series, keeps what is new,
and then, on the Mac's own model connection, writes teaching points for the
entries that have none, newest first, a bounded number per refresh. "Refresh
now" does the same at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..db import connect
from ..literature.cases import FetchGroup
from ..literature.http import ProviderError
from ..storage import cases as store
from ..storage.common import utc_now
from .cases import MAX_SYNTHESES_PER_REFRESH, WAITING, synthesise_pending

logger = logging.getLogger("vademecum.cases")

MAX_SLEEP_SECONDS = 3600.0
NOT_HERE = (
    "This Vademecum shows the cases Domi gathered. Fetching and the teaching points run on the Mac, "
    "and sync brings them here."
)


class CaseHub:
    def __init__(
        self,
        *,
        database_path: Path,
        groups: Sequence[FetchGroup],
        turn_factory: Any,
        model_mode: str,
        fetches_here: bool,
        default_interval_hours: float = store.DEFAULT_INTERVAL_HOURS,
        initial_delay: float = 30.0,
    ) -> None:
        self._database_path = Path(database_path)
        self._groups = list(groups)
        self._turn_factory = turn_factory
        self._model_mode = model_mode
        self._fetches_here = bool(fetches_here)
        self._default_interval_hours = default_interval_hours
        self._initial_delay = initial_delay
        self._task: asyncio.Task[None] | None = None
        self._refresh_task: asyncio.Task[dict[str, Any]] | None = None
        self._wake: asyncio.Event | None = None
        self._lock = asyncio.Lock()
        self._active = False

    # --- what the routes read ----------------------------------------------------

    @property
    def fetches_here(self) -> bool:
        return self._fetches_here

    @property
    def can_synthesise(self) -> bool:
        return self._model_mode in ("codex", "claude") and self._turn_factory is not None

    @property
    def is_running(self) -> bool:
        return self._active

    def describe(self, connection) -> dict[str, Any]:
        settings = store.get_settings(connection, default_interval_hours=self._default_interval_hours)
        note = ""
        if not self._fetches_here:
            note = NOT_HERE
        elif not self.can_synthesise:
            note = WAITING
        return {
            **settings,
            "fetches_here": self._fetches_here,
            "can_synthesise": self.can_synthesise,
            "running": self.is_running,
            "last_refresh": store.get_last_refresh(connection),
            "counts": store.counts(connection),
            "catalogue": [dict(entry) for entry in store.CATALOGUE],
            "note": note,
        }

    # --- the timer -----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None and self._fetches_here:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        for task in (self._task, self._refresh_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        self._refresh_task = None

    def reschedule(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def refresh_now(self, *, reason: str = "requested") -> bool:
        """Start a refresh in the background; False when one is already running."""
        if self._active or (self._refresh_task is not None and not self._refresh_task.done()):
            return False
        self._refresh_task = asyncio.create_task(self.refresh(reason=reason))
        return True

    async def _loop(self) -> None:
        self._wake = asyncio.Event()
        try:
            await self._pause(self._initial_delay)
            while True:
                connection = connect(self._database_path)
                try:
                    settings = store.get_settings(connection, default_interval_hours=self._default_interval_hours)
                finally:
                    connection.close()
                if settings["enabled"]:
                    try:
                        await self.refresh(reason="scheduled")
                    except Exception as exc:  # noqa: BLE001 - the timer must outlive one bad refresh
                        logger.error("case_refresh_error error=%s", type(exc).__name__)
                    await self._pause(settings["interval_hours"] * 3600.0)
                else:
                    await self._pause(MAX_SLEEP_SECONDS)
        except asyncio.CancelledError:
            raise

    async def _pause(self, seconds: float) -> None:
        """Sleep, in bounded steps, until the time is up or the settings change."""
        assert self._wake is not None
        remaining = max(0.0, seconds)
        while remaining > 0:
            step = min(remaining, MAX_SLEEP_SECONDS)
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=step)
                return
            except asyncio.TimeoutError:
                remaining -= step

    # --- one refresh ---------------------------------------------------------------

    async def refresh(self, *, reason: str = "requested") -> dict[str, Any]:
        async with self._lock:
            self._active = True
            try:
                return await self._refresh(reason)
            finally:
                self._active = False

    async def _refresh(self, reason: str) -> dict[str, Any]:
        connection = connect(self._database_path)
        try:
            settings = store.get_settings(connection, default_interval_hours=self._default_interval_hours)
        finally:
            connection.close()
        wanted_all = {identifier for identifier, on in settings["series"].items() if on}
        fetched: dict[str, dict[str, Any]] = {}
        for group in self._groups:
            wanted = group.series & wanted_all
            if not wanted:
                continue
            try:
                items = await asyncio.to_thread(group.fetch)
            except ProviderError as exc:
                logger.info("case_fetch_failed group=%s category=%s", group.name, exc.category)
                fetched[group.name] = {"new": 0, "error": exc.category}
                continue
            except Exception as exc:  # noqa: BLE001 - one site down is not the hub down
                logger.error("case_fetch_error group=%s error=%s", group.name, type(exc).__name__)
                fetched[group.name] = {"new": 0, "error": "unexpected"}
                continue
            rows = [item.as_dict() for item in items if item.series in wanted]
            connection = connect(self._database_path)
            try:
                new = store.record_items(connection, rows)
            finally:
                connection.close()
            fetched[group.name] = {"new": new, "error": ""}
        tally = {"done": 0, "failed": 0}
        if self.can_synthesise:
            tally = await synthesise_pending(self._database_path, self._turn_factory, limit=MAX_SYNTHESES_PER_REFRESH)
        report = {
            "at": utc_now(),
            "reason": reason,
            "fetched": fetched,
            "synthesised": tally["done"],
            "failed": tally["failed"],
        }
        connection = connect(self._database_path)
        try:
            store.record_refresh(connection, report)
        finally:
            connection.close()
        logger.info(
            "case_refresh reason=%s new=%d synthesised=%d failed=%d",
            reason,
            sum(int(entry["new"]) for entry in fetched.values()),
            tally["done"],
            tally["failed"],
        )
        return report
