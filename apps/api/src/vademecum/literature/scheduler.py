"""The in-app watcher: an asyncio task, not an external timer.

One long-lived backend process (AGENTS.md) means the weekly check is a task
inside it. There is no cron entry, no launch agent and no second process to
explain, which also means there is nothing to leave behind when Vademecum is
deleted.

**Two switches, not one.** The periodic sweep is opt-in and governed by
``literature.weekly_enabled``. A manual "Check now" is an explicit action by the
owner and works whenever a provider is configured, whatever the sweep is set to.
Collapsing the two made the button dead on a fresh install, where the sweep is
off by default. ``provider=None`` is the third state: nothing is configured, and
a manual check says so rather than pretending it ran.

Four properties the loop is built around:

* **Never uninvited.** The sweep is opt-in twice over: the feature has to be
  configured on *and* ``literature.weekly_enabled`` has to be set by the owner,
  which nothing here writes -- :func:`storage.literature.seed_settings` has no
  way to say "on". Topics are the owner's too; the watcher checks the ones that
  are enabled and never enables one. Nothing outside this process is created:
  no cron entry, no launch agent, nothing left behind when Vademecum is
  deleted.
* **Never twice.** Two gates, because one is not enough. Every run takes a
  *fresh* lease identity in ``app_state``, so a second acquire fails even when
  it comes from this same watcher -- a per-watcher holder re-acquired its own
  lease and let a manual check and a sweep search the same topic at once. In
  this process an :class:`asyncio.Lock` refuses a second cycle before it can
  reach the provider at all. The lease carries an expiry, so a run that dies
  holding it costs one TTL rather than the watcher forever, and it is released
  by holder identity, so a cancelled run cannot free somebody else's. Catch-up
  is idempotent because a finished check moves ``next_due_at`` forward.
* **Never silent, and never all-or-nothing.** A failure ends as a ``failed``
  check row with its category, a topic that shows the category and a backed-off
  due time, and an unchanged ``last_checked_at``. One topic's failure is
  persisted and the sweep continues with the next topic. A process that stops
  mid-sweep leaves a ``running`` check row, which ``jobs.sweep_interrupted``
  closes out as ``failed``/``interrupted`` at the next startup -- that is its
  job and this module does not duplicate or fight it.
* **Never chatty.** The logger records event names, topic ids and categories.
  No query text, no titles, no abstracts, no URLs.

Each cycle also runs a bounded refresh of records already cited as evidence,
because a date-sorted search never returns them again and would never notice
their retraction. Anything that newly became retracted, corrected or a notice
reaches ``on_status_change`` *after* the write that set the flags committed, so
the caller that invalidates dependent learning points never acts on a change
that a rollback then undid.

Provider work is blocking (``http.client``) and so is SQLite, so each cycle runs
in a worker thread with its own connection; the event loop keeps serving.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import uuid
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from ..db import connect
from ..storage import literature as store
from ..storage.common import NotFoundError, utc_now
from .http import ProviderError
from .pubmed import PubMedProvider, QueryError

logger = logging.getLogger("vademecum.literature")

# How long a run may hold the lease before another may take over. Generous
# against a slow provider, short enough that a crash costs one interval at most.
LOCK_TTL_SECONDS = 900.0

# The loop wakes on a bounded sleep rather than sleeping a whole interval, so a
# machine that was asleep for a week still catches up within a quarter hour.
MAX_SLEEP_SECONDS = 900.0
MIN_SLEEP_SECONDS = 1.0

# Evidence-linked records re-read per cycle. Small: this is a background sweep
# behind a shared rate limit, not a backfill.
REFRESH_LIMIT = 25

# How long a settings change made from another thread waits for the loop to
# start or stop before it gives up and just logs. Bounded so a stuck loop
# cannot hang the request that toggled the setting.
APPLY_TIMEOUT_SECONDS = 30.0

# How long a stop waits for a cycle already inside the provider to finish and
# free its lease. Bounded for the same reason: shutdown must not wait out a
# slow provider.
STOP_DRAIN_SECONDS = 5.0


def _first_not_none(*values: bool | None) -> bool:
    for value in values:
        if value is not None:
            return bool(value)
    return False


class LiteratureWatcher:
    """Owns the periodic check. Safe to construct with no provider at all."""

    def __init__(
        self,
        *,
        database_path: Path,
        provider: PubMedProvider | None,
        interval_hours: float,
        weekly_enabled: bool | None = None,
        enabled: bool | None = None,
        on_status_change: Callable[[list[dict[str, Any]]], None] | None = None,
    ) -> None:
        """``weekly_enabled`` governs the sweep only; ``provider`` governs both.

        ``enabled`` is the older spelling of ``weekly_enabled`` and is accepted
        so an existing caller keeps working. Neither of them decides whether a
        manual check runs: that is ``provider`` and nothing else.
        """
        self._database_path = Path(database_path)
        self._provider = provider
        self._interval_hours = float(interval_hours)
        self._weekly_enabled = _first_not_none(weekly_enabled, enabled)
        self._on_status_change = on_status_change
        # A prefix, not an identity: each run mints its own lease below.
        self._holder = f"watcher-{uuid.uuid4().hex[:12]}"
        self._task: asyncio.Task[None] | None = None
        self._stopping: asyncio.Event | None = None
        self._reschedule: asyncio.Event | None = None
        self._gate: asyncio.Lock | None = None
        self._gate_loop: asyncio.AbstractEventLoop | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        # Leases held by worker threads that are still executing. Touched from
        # those threads, so it has a plain lock rather than an asyncio one.
        self._leases: set[str] = set()
        self._leases_guard = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._bind_loop()

    # -- what is switched on --------------------------------------------------

    @property
    def available(self) -> bool:
        """Is there a provider at all? What a manual check depends on."""
        return self._provider is not None

    @property
    def weekly_enabled(self) -> bool:
        """Is the periodic sweep switched on? The owner's setting, not ours."""
        return self._weekly_enabled

    @property
    def interval_hours(self) -> float:
        return self._interval_hours

    @property
    def running(self) -> bool:
        """Is the periodic loop running? Says nothing about manual checks."""
        return self._task is not None and not self._task.done()

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        self._bind_loop()
        # The interval is configuration and is seeded either way; the sweep's
        # on/off default is written off and stays the owner's to change.
        await asyncio.to_thread(self._seed_settings)
        if not self._weekly_enabled:
            # Not "disabled": manual checks still work. Only the loop is off.
            logger.info("literature_sweep_off provider=%s", self.available)
            return
        self._start_loop()

    async def aclose(self) -> None:
        await self._stop_loop()
        logger.info("literature_watcher_stopped")

    def apply_settings(self, *, weekly_enabled: bool, interval_hours: float) -> None:
        """Adopt a settings change now, rather than at the next restart.

        Synchronous on purpose: the settings route is a sync handler, so this is
        called from a worker thread, and it hands the actual start or stop to
        the event loop. Safe to call repeatedly (switching on something already
        on only re-times the sleep) and safe to call while a check is in flight
        (the running cycle finishes and frees its own lease).
        """
        self._interval_hours = float(interval_hours)
        self._weekly_enabled = bool(weekly_enabled)
        logger.info(
            "literature_settings_applied weekly=%s interval_hours=%s",
            self._weekly_enabled,
            self._interval_hours,
        )
        self._dispatch(self._apply())

    async def _apply(self) -> None:
        if not self._weekly_enabled:
            if self.running:
                await self._stop_loop()
            return
        if self.running:
            # Already sweeping: wake the sleeper so the new interval applies.
            # Nothing is dropped -- a topic's due time lives in the database,
            # so an early wake finds it and a late one still finds it.
            self._wake()
            return
        await self.start()

    def _start_loop(self) -> None:
        if self.running:
            return
        self._stopping = asyncio.Event()
        self._reschedule = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="literature-watcher")
        logger.info("literature_watcher_started interval_hours=%s", self._interval_hours)

    async def _stop_loop(self) -> None:
        if self._stopping is not None:
            self._stopping.set()
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        # A cycle whose awaiter was cancelled is still running in its thread and
        # still using its lease; it frees it itself. Wait a bounded moment for
        # that so a stop leaves no lease behind, and give up rather than hang.
        if not self._idle.is_set():
            await asyncio.to_thread(self._idle.wait, STOP_DRAIN_SECONDS)
            if not self._idle.is_set():
                logger.info("literature_lease_in_flight_at_stop")

    def _wake(self) -> None:
        event = self._reschedule
        if event is not None:
            event.set()

    # -- talking to the loop from another thread ------------------------------

    def _bind_loop(self) -> None:
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Constructed outside a loop. Whichever coroutine runs first binds.
            pass

    def _dispatch(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run a lifecycle coroutine on the loop, from wherever we are."""
        try:
            current: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        if current is not None:
            # On the loop already: scheduling is all we may do without blocking
            # the very loop we would be waiting for.
            self._loop = current
            current.create_task(coroutine)
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            logger.info("literature_settings_deferred reason=no_loop")
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            future.result(timeout=APPLY_TIMEOUT_SECONDS)
        except Exception as exc:
            future.cancel()
            logger.error("literature_settings_apply_failed error=%s", type(exc).__name__)

    # -- public operations ----------------------------------------------------

    async def check_now(self, topic_id: str | None = None) -> dict[str, Any]:
        """Manual trigger: runs a topic even if it is not due yet.

        Deliberately not gated on ``weekly_enabled``. The sweep being off means
        "do not go looking on your own", not "ignore the owner pressing the
        button". Without a provider there is nothing to ask, and that is said
        plainly rather than reported as a check that found nothing.
        """
        self._bind_loop()
        if self._provider is None:
            logger.info("literature_manual_check_unavailable")
            return {
                "status": "unavailable",
                "reason": "provider_unavailable",
                "results": [],
            }
        return await self._guarded("manual", topic_id)

    # -- the loop -------------------------------------------------------------

    async def _run(self) -> None:
        stopping = self._stopping
        assert stopping is not None
        try:
            await self._guarded("catch_up", None)
            while not stopping.is_set():
                reason = await self._sleep(self._wake_seconds())
                if reason == "stop":
                    break
                if reason == "reschedule":
                    # The interval changed under us; re-time, do not re-check.
                    continue
                await self._guarded("scheduled", None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # the loop reports, it does not die
            logger.error("literature_loop_failed error=%s", type(exc).__name__)

    async def _guarded(self, trigger: str, topic_id: str | None) -> dict[str, Any]:
        """One cycle, on a worker thread, and never two at once in this process.

        The database lease alone cannot stop this: two coroutines here would
        both reach the provider before either wrote anything. Busy rather than
        queued, because the alternative is a manual check that waits out a
        whole sweep before it starts.
        """
        gate = self._gate_for_loop()
        if gate.locked():
            logger.info("literature_cycle_skipped trigger=%s reason=in_flight", trigger)
            return {"status": "busy", "results": []}
        async with gate:
            return await asyncio.to_thread(self._run_cycle, trigger, topic_id)

    def _gate_for_loop(self) -> asyncio.Lock:
        """The in-process gate, bound to whichever loop is actually running."""
        loop = asyncio.get_running_loop()
        if self._gate is None or self._gate_loop is not loop:
            self._gate = asyncio.Lock()
            self._gate_loop = loop
        return self._gate

    async def _sleep(self, seconds: float) -> str:
        """Wake on time, on a settings change, or on shutdown."""
        stopping = self._stopping
        reschedule = self._reschedule
        assert stopping is not None and reschedule is not None
        waiters = [
            asyncio.ensure_future(stopping.wait()),
            asyncio.ensure_future(reschedule.wait()),
        ]
        try:
            await asyncio.wait(
                waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            for waiter in waiters:
                waiter.cancel()
        if stopping.is_set():
            return "stop"
        if reschedule.is_set():
            reschedule.clear()
            return "reschedule"
        return "due"

    def _wake_seconds(self) -> float:
        return min(
            MAX_SLEEP_SECONDS, max(MIN_SLEEP_SECONDS, self._interval_hours * 3600.0)
        )

    # -- one cycle, on a worker thread ---------------------------------------

    def _run_cycle(self, trigger: str, topic_id: str | None) -> dict[str, Any]:
        connection = connect(self._database_path)
        try:
            return self._cycle(connection, trigger, topic_id)
        except NotFoundError:
            # A manual check for a topic that does not exist is the caller's
            # mistake, not a watcher failure; it becomes a 404 upstairs.
            raise
        except Exception as exc:  # never propagate out of a background thread
            logger.error(
                "literature_cycle_failed trigger=%s error=%s", trigger, type(exc).__name__
            )
            return {"status": "failed", "results": []}
        finally:
            connection.close()

    def _cycle(
        self, connection: sqlite3.Connection, trigger: str, topic_id: str | None
    ) -> dict[str, Any]:
        now = utc_now()
        settings = store.get_settings(connection)
        if trigger != "manual" and not settings["weekly_enabled"]:
            logger.info("literature_cycle_skipped trigger=%s reason=weekly_off", trigger)
            return {"status": "off", "results": []}

        lease = self._new_lease()
        if not store.acquire_lock(
            connection, holder=lease, ttl_seconds=LOCK_TTL_SECONDS, now=now
        ):
            # Someone else is mid-run -- possibly this very watcher, which is
            # why the lease is per run and not per watcher. Skipping is the
            # correct outcome: the alternative is two searches for one topic.
            logger.info("literature_cycle_skipped trigger=%s reason=locked", trigger)
            return {"status": "busy", "results": []}
        self._claim(lease)

        try:
            topics = self._topics(connection, trigger, topic_id, now)
            results = [self._check_topic(connection, topic, trigger) for topic in topics]
            refresh = self._refresh(connection)
        finally:
            self._surrender(connection, lease)

        changed = [
            change
            for result in (*results, refresh)
            for change in result.get("changed_status", [])
        ]
        if changed:
            # After the writes, never before: every entry here is already
            # committed, so a caller acting on one cannot outrun a rollback.
            self._notify(changed)
        return {
            "status": "ok",
            "results": results,
            "refresh": refresh,
            "unread": store.unread_count(connection),
        }

    # -- the lease ------------------------------------------------------------

    def _new_lease(self) -> str:
        """A fresh identity per run, so a second acquire fails even from here."""
        return f"{self._holder}:{uuid.uuid4().hex[:12]}"

    def _claim(self, lease: str) -> None:
        with self._leases_guard:
            self._leases.add(lease)
            self._idle.clear()

    def _surrender(self, connection: sqlite3.Connection, lease: str) -> None:
        """Free this exact lease, and nothing else.

        Conditional on identity in SQL. A cancelled ``to_thread`` leaves this
        thread running; if its lease has since expired and another run has
        taken over, the delete matches nothing and the new run keeps the lease
        it holds.
        """
        try:
            store.release_lock(connection, lease)
        except Exception as exc:
            logger.error("literature_lock_release_failed error=%s", type(exc).__name__)
        finally:
            with self._leases_guard:
                self._leases.discard(lease)
                if not self._leases:
                    self._idle.set()

    def _refresh(self, connection: sqlite3.Connection) -> dict[str, Any]:
        """Re-read what is already cited. Its failure is not the sweep's."""
        if self._provider is None:
            return {"status": "skipped", "reason": "provider_unavailable"}
        try:
            outcome = store.refresh_records(
                connection, self._provider, limit=REFRESH_LIMIT
            )
        except ProviderError as exc:
            logger.warning("literature_refresh_failed category=%s", exc.category)
            return {"status": "failed", "failure_category": exc.category}
        except Exception as exc:
            logger.error("literature_refresh_error error=%s", type(exc).__name__)
            return {"status": "failed", "failure_category": "unexpected"}
        logger.info(
            "literature_refresh_ok considered=%d refreshed=%d changed=%d",
            outcome["considered"],
            outcome["refreshed"],
            len(outcome["changed_status"]),
        )
        return {"status": "ok", **outcome}

    def _topics(
        self,
        connection: sqlite3.Connection,
        trigger: str,
        topic_id: str | None,
        now: str,
    ) -> list[store.Topic]:
        if topic_id is not None:
            return [store.get_topic(connection, topic_id)]
        if trigger == "manual":
            return [topic for topic in store.list_topics(connection) if topic.enabled]
        return store.due_topics(connection, now=now)

    def _check_topic(
        self, connection: sqlite3.Connection, topic: store.Topic, trigger: str
    ) -> dict[str, Any]:
        if self._provider is None:
            # Honest, and recorded nowhere as a successful check: no provider
            # means no search happened, and the topic stays due.
            logger.info("literature_check_skipped topic=%s reason=no_provider", topic.id)
            return {
                "topic_id": topic.id,
                "status": "skipped",
                "reason": "provider_unavailable",
            }

        try:
            check = store.begin_check(connection, topic.id, trigger)
        except Exception as exc:
            # A topic deleted between listing and checking, say. There is no row
            # to record the failure on, so it is reported and the sweep moves on
            # to the next topic rather than ending here.
            logger.error(
                "literature_check_unstarted topic=%s error=%s",
                topic.id,
                type(exc).__name__,
            )
            return {
                "topic_id": topic.id,
                "status": "failed",
                "failure_category": "unstarted",
            }

        # Once a topic's newest results are all known, read further back (feedback of 10 October).
        depth = store.search_depth(connection, topic.id)
        try:
            articles = self._provider.search(topic.query, offset=depth) if depth else self._provider.search(topic.query)
        except QueryError:
            return self._fail(connection, check.id, topic, "invalid_query")
        except ProviderError as exc:
            return self._fail(connection, check.id, topic, exc.category)
        except Exception as exc:
            logger.error(
                "literature_check_error topic=%s error=%s", topic.id, type(exc).__name__
            )
            return self._fail(connection, check.id, topic, "unexpected")

        try:
            counts = store.record_articles(
                connection, topic_id=topic.id, check_id=check.id, articles=list(articles)
            )
        except Exception as exc:
            # Writing the results failed. The check is still closed out as
            # failed so it cannot sit at `running` forever, and the sweep
            # continues: one topic's bad row is not the other topics' problem.
            logger.error(
                "literature_record_failed topic=%s error=%s", topic.id, type(exc).__name__
            )
            return self._fail(connection, check.id, topic, "storage")

        try:
            store.note_search_depth(
                connection, topic.id, found_new=int(counts.get("new_to_topic", 0)), step=max(1, int(counts.get("returned", 0)))
            )
        except Exception as exc:  # noqa: BLE001 - a depth not noted only means the same window next time
            logger.error("literature_depth_failed topic=%s error=%s", topic.id, type(exc).__name__)
        try:
            store.finish_check(
                connection,
                check.id,
                status="ok",
                result_count=counts["returned"],
                new_count=counts["new_to_library"],
            )
        except Exception as exc:
            logger.error(
                "literature_finish_failed topic=%s error=%s", topic.id, type(exc).__name__
            )
            # The records were written and committed even though the check row
            # could not be closed properly, so the status changes still travel:
            # a retraction that reached the database must reach the caller.
            return self._fail(
                connection,
                check.id,
                topic,
                "storage",
                changed_status=counts["changed_status"],
            )
        logger.info(
            "literature_check_ok topic=%s returned=%d new=%d recent=%d",
            topic.id,
            counts["returned"],
            counts["new_to_library"],
            counts["recently_published"],
        )
        return {
            "topic_id": topic.id,
            "check_id": check.id,
            "status": "ok",
            **counts,
        }

    def _fail(
        self,
        connection: sqlite3.Connection,
        check_id: str,
        topic: store.Topic,
        category: str,
        *,
        changed_status: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        try:
            store.finish_check(
                connection, check_id, status="failed", failure_category=category
            )
        except Exception as exc:  # the sweep continues even if this cannot be written
            logger.error(
                "literature_failure_unrecorded topic=%s error=%s",
                topic.id,
                type(exc).__name__,
            )
        logger.warning(
            "literature_check_failed topic=%s category=%s", topic.id, category
        )
        return {
            "topic_id": topic.id,
            "check_id": check_id,
            "status": "failed",
            "failure_category": category,
            "changed_status": list(changed_status or []),
        }

    def _notify(self, changes: list[dict[str, Any]]) -> None:
        """Hand changed records to the caller; a bad callback is not our crash.

        Each entry says whether the record is now retracted, corrected or a
        notice, and which of those newly became true. The watcher does not act
        on any of it: releasing or holding dependent learning material is the
        caller's decision, and a correction in particular must hold pending an
        explicit re-review rather than release anything.

        Called only after the writes committed, and never for a record that did
        not change. A callback that raises is logged and swallowed: the sweep
        continues and the database keeps what it already wrote.
        """
        if self._on_status_change is None:
            return
        try:
            self._on_status_change(changes)
        except Exception as exc:
            logger.error("literature_status_callback_failed error=%s", type(exc).__name__)

    # -- small connection-owning helpers -------------------------------------

    def _seed_settings(self) -> None:
        """Seed the interval only. Turning the sweep on is the owner's to do."""
        connection = connect(self._database_path)
        try:
            store.seed_settings(connection, interval_hours=self._interval_hours)
        except Exception as exc:
            logger.error("literature_settings_seed_failed error=%s", type(exc).__name__)
        finally:
            connection.close()


__all__ = [
    "LiteratureWatcher",
    "APPLY_TIMEOUT_SECONDS",
    "LOCK_TTL_SECONDS",
    "REFRESH_LIMIT",
    "STOP_DRAIN_SECONDS",
]
