"""The in-app watcher: opt-in, isolated per topic, and readable when it stops.

Nothing here opens a socket. The watcher takes an injected provider, and the
providers used are the scripted fakes in ``fake_pubmed``; a test that reached
the network would have to construct an ``HttpsFetcher`` itself, and none does.

The properties under test are the ones that make an unattended background loop
acceptable at all: it does not start itself, the owner's own button still works
when it is off, one topic's failure is written down and does not take the sweep
with it, two runs cannot both search a topic even when they come from the same
watcher, a run that dies releases its lease by expiry, and a process stopped
mid-sweep leaves a state someone can read.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import fake_pubmed
from vademecum.db import apply_migrations, connect
from vademecum.literature import scheduler
from vademecum.literature.http import ProviderError
from vademecum.literature.pubmed import Article, PubMedProvider, QueryError
from vademecum.literature.scheduler import LOCK_TTL_SECONDS, LiteratureWatcher
from vademecum.storage import jobs, literature as store


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> datetime:
    return datetime.now(timezone.utc)


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


async def until(predicate: Any, *, timeout: float = 5.0) -> None:
    """Wait for something the loop does in its own time, or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the watcher never reached the expected state")


@pytest.fixture()
def db(database_path: Path) -> Path:
    """A migrated database the watcher can open its own connections to."""
    connection = connect(database_path)
    try:
        apply_migrations(connection)
    finally:
        connection.close()
    return database_path


@pytest.fixture()
def conn(db: Path) -> Any:
    connection = connect(db)
    try:
        yield connection
    finally:
        connection.close()


def enable_weekly(connection: sqlite3.Connection) -> None:
    """What the owner does when they choose to switch the sweep on."""
    store.set_settings(connection, weekly_enabled=True, interval_hours=168.0)


def articles_for(*records: str) -> list[Article]:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(xml=fake_pubmed.article_set(*records)), max_results=25
    )
    return provider.search("sepsis")


def watcher(db: Path, provider: Any, **kwargs: Any) -> LiteratureWatcher:
    options: dict[str, Any] = {
        "database_path": db,
        "provider": provider,
        "interval_hours": 168.0,
        "weekly_enabled": True,
    }
    options.update(kwargs)
    return LiteratureWatcher(**options)


# -- opt-in --------------------------------------------------------------------


def test_a_watcher_with_no_provider_does_nothing_at_all(db: Path, conn: Any) -> None:
    """No provider configured means no task and nothing to ask."""
    store.create_topic(conn, label="Sepsis", query="sepsis")
    enable_weekly(conn)

    watch = watcher(db, None, weekly_enabled=False)
    run(watch.start())
    assert watch.running is False
    assert watch.available is False

    assert store.list_updates(conn) == []
    run(watch.aclose())


# -- defect 1: the sweep switch is not the manual-check switch ------------------


def test_a_manual_check_works_while_the_weekly_sweep_is_off(
    db: Path, conn: Any
) -> None:
    """The defect: Check now was dead on every fresh install.

    ``weekly_enabled`` is off by default and correctly so -- an unattended loop
    that reaches the network is opt-in. Pressing the button is not unattended,
    and it is the owner opting in to this one check.
    """
    topic = store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider, weekly_enabled=False)
    run(watch.start())

    outcome = run(watch.check_now())
    assert outcome["status"] == "ok"
    assert provider.queries == ["sepsis"]
    assert [result["topic_id"] for result in outcome["results"]] == [topic.id]
    assert len(store.list_updates(conn)) == 1
    assert store.get_settings(conn)["weekly_enabled"] is False, "still off, still opt-in"
    run(watch.aclose())


def test_the_periodic_loop_does_not_run_while_the_weekly_sweep_is_off(
    db: Path, conn: Any
) -> None:
    """The other half of the same split: only the *loop* is opt-in."""
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider, weekly_enabled=False)

    async def scenario() -> None:
        await watch.start()
        assert watch.running is False
        assert watch.weekly_enabled is False
        # Long enough for a loop that started anyway to have swept once.
        await asyncio.sleep(0.05)
        assert provider.queries == [], "nothing may go looking on its own"
        await watch.aclose()

    run(scenario())
    assert store.list_updates(conn) == []


def test_a_manual_check_says_so_when_no_provider_is_configured(
    db: Path, conn: Any
) -> None:
    """Honest rather than a check that found nothing."""
    store.create_topic(conn, label="Sepsis", query="sepsis")
    enable_weekly(conn)
    watch = watcher(db, None)

    outcome = run(watch.check_now())
    assert outcome == {
        "status": "unavailable",
        "reason": "provider_unavailable",
        "results": [],
    }
    assert store.list_checks(conn, store.list_topics(conn)[0].id) == []
    assert store.list_updates(conn) == []


def test_the_older_enabled_argument_still_means_the_sweep(db: Path) -> None:
    """``enabled=`` is the old spelling; it may not start meaning the button."""
    provider = fake_pubmed.RecordingProvider()
    assert LiteratureWatcher(
        database_path=db, provider=provider, interval_hours=1.0, enabled=True
    ).weekly_enabled is True
    off = LiteratureWatcher(
        database_path=db, provider=provider, interval_hours=1.0, enabled=False
    )
    assert off.weekly_enabled is False
    assert off.available is True, "a provider is configured either way"


def test_starting_the_watcher_never_switches_weekly_checking_on(
    db: Path, conn: Any
) -> None:
    """The defect this guards: seeding the configured default enabled it.

    Configuration may say how often. Only the owner may say whether.
    """
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider, interval_hours=24.0)
    run(watch.start())
    run(watch.aclose())

    settings = store.get_settings(conn)
    assert settings["weekly_enabled"] is False, "nothing may turn the sweep on for you"
    assert settings["interval_hours"] == 24.0, "the interval is configuration"


def test_a_scheduled_sweep_does_nothing_while_weekly_checking_is_off(
    db: Path, conn: Any
) -> None:
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)
    assert watch._cycle(conn, "scheduled", None) == {"status": "off", "results": []}
    assert provider.queries == []

    enable_weekly(conn)
    outcome = watch._cycle(conn, "scheduled", None)
    assert outcome["status"] == "ok"
    assert provider.queries == ["sepsis"]


def test_the_sweep_checks_only_the_topics_the_owner_enabled(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    wanted = store.create_topic(conn, label="Sepsis", query="sepsis")
    unwanted = store.create_topic(conn, label="Fluids", query="crystalloid")
    store.update_topic(conn, unwanted.id, enabled=False)

    provider = fake_pubmed.RecordingProvider(
        {
            "sepsis": articles_for(fake_pubmed.WITH_ABSTRACT),
            "crystalloid": articles_for(fake_pubmed.GUIDELINE),
        }
    )
    outcome = watcher(db, provider)._cycle(conn, "scheduled", None)

    assert provider.queries == ["sepsis"]
    assert [result["topic_id"] for result in outcome["results"]] == [wanted.id]
    assert store.get_topic(conn, unwanted.id).enabled is False, "still the owner's call"


def test_the_watcher_creates_no_external_automation() -> None:
    """No cron entry, no launch agent, no second process to leave behind."""
    from vademecum.literature import scheduler

    text = Path(scheduler.__file__).read_text(encoding="utf-8")
    for banned in ("crontab", "launchctl", "LaunchAgents", "subprocess", "os.system"):
        assert banned not in text


# -- one topic's failure is not the sweep's ------------------------------------


def test_a_failing_topic_is_recorded_and_the_others_still_run(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    bad = store.create_topic(conn, label="Bad", query="explodes")
    good = store.create_topic(conn, label="Good", query="sepsis")

    provider = fake_pubmed.RecordingProvider(
        {
            "explodes": ProviderError("timeout"),
            "sepsis": articles_for(fake_pubmed.WITH_ABSTRACT),
        }
    )
    outcome = watcher(db, provider)._cycle(conn, "scheduled", None)

    by_topic = {result["topic_id"]: result for result in outcome["results"]}
    assert by_topic[bad.id]["status"] == "failed"
    assert by_topic[bad.id]["failure_category"] == "timeout"
    assert by_topic[good.id]["status"] == "ok"

    failed = store.get_topic(conn, bad.id)
    assert failed.last_status == "failed"
    assert failed.last_failure == "timeout"
    assert failed.consecutive_failures == 1
    assert failed.last_checked_at is None, "a failure is not a stale success"
    assert store.list_checks(conn, bad.id)[0].status == "failed"

    assert store.get_topic(conn, good.id).last_status == "ok"
    assert len(store.list_updates(conn)) == 1


@pytest.mark.parametrize(
    ("raised", "category"),
    [
        (ProviderError("connection"), "connection"),
        (QueryError("a topic query must be a single line"), "invalid_query"),
        (RuntimeError("something else entirely"), "unexpected"),
    ],
)
def test_every_kind_of_topic_failure_ends_as_a_category(
    db: Path, conn: Any, raised: Exception, category: str
) -> None:
    enable_weekly(conn)
    bad = store.create_topic(conn, label="Bad", query="explodes")
    good = store.create_topic(conn, label="Good", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"explodes": raised, "sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )

    outcome = watcher(db, provider)._cycle(conn, "scheduled", None)
    by_topic = {result["topic_id"]: result for result in outcome["results"]}
    assert by_topic[bad.id]["failure_category"] == category
    assert by_topic[good.id]["status"] == "ok"
    assert store.get_topic(conn, bad.id).last_failure == category


def test_a_failure_category_never_carries_provider_text(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Bad", query="explodes")
    secret = "upstream said: term=<the owner's private topic>"
    provider = fake_pubmed.RecordingProvider({"explodes": RuntimeError(secret)})

    watcher(db, provider)._cycle(conn, "scheduled", None)
    stored = store.get_topic(conn, topic.id)
    assert stored.last_failure == "unexpected"
    assert secret not in stored.last_failure
    assert secret not in store.list_checks(conn, topic.id)[0].failure_category


def test_a_storage_failure_closes_the_check_and_the_sweep_continues(
    db: Path, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad row for one topic must not leave a check `running` forever."""
    enable_weekly(conn)
    bad = store.create_topic(conn, label="Bad", query="broken")
    good = store.create_topic(conn, label="Good", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {
            "broken": articles_for(fake_pubmed.WITH_ABSTRACT),
            "sepsis": articles_for(fake_pubmed.GUIDELINE),
        }
    )

    real = store.record_articles

    def sometimes_broken(connection: Any, **kwargs: Any) -> Any:
        if kwargs["topic_id"] == bad.id:
            raise sqlite3.OperationalError("disk gone")
        return real(connection, **kwargs)

    monkeypatch.setattr(store, "record_articles", sometimes_broken)
    outcome = watcher(db, provider)._cycle(conn, "scheduled", None)

    by_topic = {result["topic_id"]: result for result in outcome["results"]}
    assert by_topic[bad.id]["failure_category"] == "storage"
    assert by_topic[good.id]["status"] == "ok"
    assert store.list_checks(conn, bad.id)[0].status == "failed"
    assert store.get_topic(conn, good.id).last_status == "ok"


def test_a_topic_deleted_mid_sweep_does_not_abort_the_rest(
    db: Path, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_weekly(conn)
    doomed = store.create_topic(conn, label="Doomed", query="gone")
    good = store.create_topic(conn, label="Good", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )

    real = store.begin_check

    def vanish(connection: Any, topic_id: str, trigger: str) -> Any:
        if topic_id == doomed.id:
            store.delete_topic(connection, doomed.id)
        return real(connection, topic_id, trigger)

    monkeypatch.setattr(store, "begin_check", vanish)
    outcome = watcher(db, provider)._cycle(conn, "scheduled", None)

    statuses = {result["topic_id"]: result["status"] for result in outcome["results"]}
    assert statuses[doomed.id] == "failed"
    assert statuses[good.id] == "ok"


# -- the lock ------------------------------------------------------------------


def test_two_watchers_cannot_sweep_at_once(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    first = watcher(db, fake_pubmed.RecordingProvider())
    second = watcher(db, fake_pubmed.RecordingProvider())

    assert store.acquire_lock(
        conn, holder=first._holder, ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )
    other = connect(db)
    try:
        assert second._cycle(other, "scheduled", None) == {"status": "busy", "results": []}
    finally:
        other.close()


def test_a_run_that_dies_holding_the_lock_costs_one_ttl_and_no_more(
    db: Path, conn: Any
) -> None:
    """Expiry is what stops a crash wedging the watcher forever."""
    crashed = iso(now() - timedelta(seconds=LOCK_TTL_SECONDS * 2))
    assert store.acquire_lock(
        conn, holder="crashed-run", ttl_seconds=LOCK_TTL_SECONDS, now=crashed
    )
    assert store.watch_status(conn)["locked"] is False, "an expired lock is not held"

    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    assert watcher(db, provider)._cycle(conn, "scheduled", None)["status"] == "ok"

    # And it cannot be double-held: nobody else may take it while it is live.
    live = watcher(db, provider)
    assert store.acquire_lock(
        conn, holder=live._holder, ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )
    assert not store.acquire_lock(
        conn, holder="somebody-else", ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )


def test_the_lock_is_released_even_when_a_topic_blows_up(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Bad", query="explodes")
    provider = fake_pubmed.RecordingProvider({"explodes": ProviderError("timeout")})

    watcher(db, provider)._cycle(conn, "scheduled", None)
    assert store.watch_status(conn)["locked"] is False
    assert store.acquire_lock(
        conn, holder="next-run", ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )


def test_a_manual_check_also_takes_the_lock(db: Path, conn: Any) -> None:
    """A manual check and a sweep must not both search one topic."""
    store.create_topic(conn, label="Sepsis", query="sepsis")
    watch = watcher(db, fake_pubmed.RecordingProvider())
    assert store.acquire_lock(
        conn, holder="a-sweep", ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )
    other = connect(db)
    try:
        assert watch._cycle(other, "manual", None)["status"] == "busy"
    finally:
        other.close()


# -- defect 3: one run at a time, from anywhere ---------------------------------


def blocking_watcher(db: Path) -> tuple[LiteratureWatcher, Any]:
    provider = fake_pubmed.BlockingProvider(articles_for(fake_pubmed.WITH_ABSTRACT))
    return watcher(db, provider), provider


def test_a_manual_check_cannot_overlap_a_sweep_already_running(
    db: Path, conn: Any
) -> None:
    """The defect: the watcher re-acquired its own lease and searched twice."""
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    watch, provider = blocking_watcher(db)

    async def scenario() -> None:
        sweep = asyncio.create_task(watch._guarded("scheduled", None))
        await until(provider.started.is_set)

        manual = await watch.check_now()
        assert manual == {"status": "busy", "results": []}, "not a second search"

        provider.release.set()
        outcome = await sweep
        assert outcome["status"] == "ok"

    run(scenario())
    assert provider.queries == ["sepsis"], "one search, not two"
    assert provider.most_at_once == 1, "two coroutines reached the provider at once"


def test_a_second_lease_from_the_same_watcher_is_refused(db: Path, conn: Any) -> None:
    """A per-watcher holder refreshed its own lease. A per-run one cannot."""
    watch = watcher(db, fake_pubmed.RecordingProvider())
    moment = iso(now())

    first = watch._new_lease()
    second = watch._new_lease()
    assert first != second
    assert first.startswith(watch._holder), "still recognisably this watcher"

    assert store.acquire_lock(
        conn, holder=first, ttl_seconds=LOCK_TTL_SECONDS, now=moment
    )
    assert not store.acquire_lock(
        conn, holder=second, ttl_seconds=LOCK_TTL_SECONDS, now=moment
    )


def test_a_cycle_will_not_join_a_run_this_watcher_is_already_making(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)

    # Exactly the state a run in another thread of this same watcher leaves.
    held = watch._new_lease()
    assert store.acquire_lock(
        conn, holder=held, ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )
    watch._claim(held)

    other = connect(db)
    try:
        assert watch._cycle(other, "manual", None) == {"status": "busy", "results": []}
        assert watch._cycle(other, "scheduled", None) == {"status": "busy", "results": []}
    finally:
        other.close()
    assert provider.queries == []

    # And the wedge is temporary: the lease expires rather than lasting forever.
    store.release_lock(conn, held)
    watch._surrender(conn, held)
    assert watch._cycle(conn, "manual", None)["status"] == "ok"


def test_a_watchers_own_lease_expires_so_a_crashed_run_cannot_wedge_it(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)

    crashed = iso(now() - timedelta(seconds=LOCK_TTL_SECONDS * 2))
    assert store.acquire_lock(
        conn, holder=watch._new_lease(), ttl_seconds=LOCK_TTL_SECONDS, now=crashed
    )
    assert store.watch_status(conn)["locked"] is False
    assert watch._cycle(conn, "scheduled", None)["status"] == "ok"


def test_a_late_finishing_run_never_releases_someone_elses_lease(
    db: Path, conn: Any
) -> None:
    """A cancelled ``to_thread`` leaves its thread running. It may free only its own."""
    watch = watcher(db, fake_pubmed.RecordingProvider())
    abandoned = watch._new_lease()
    watch._claim(abandoned)
    assert store.acquire_lock(
        conn,
        holder=abandoned,
        ttl_seconds=LOCK_TTL_SECONDS,
        now=iso(now() - timedelta(seconds=LOCK_TTL_SECONDS * 2)),
    )

    # The TTL passes and the next run takes over while the old thread grinds on.
    successor = "another-run"
    assert store.acquire_lock(
        conn, holder=successor, ttl_seconds=LOCK_TTL_SECONDS, now=iso(now())
    )

    watch._surrender(conn, abandoned)  # the old thread finally finishes

    status = store.watch_status(conn)
    assert status["locked"] is True, "the successor still holds its lease"
    assert status["lock_holder"] == successor


def test_a_cancelled_run_leaves_its_lease_to_the_thread_that_holds_it(
    db: Path, conn: Any
) -> None:
    """Cancellation must not hand the database to a second searcher."""
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    watch, provider = blocking_watcher(db)

    async def scenario() -> None:
        sweep = asyncio.create_task(watch._guarded("scheduled", None))
        await until(provider.started.is_set)
        sweep.cancel()
        await asyncio.gather(sweep, return_exceptions=True)

        # The worker thread is still inside the provider, and still the holder.
        status = store.watch_status(conn)
        assert status["locked"] is True
        assert status["lock_holder"].startswith(watch._holder)

        provider.release.set()
        await until(lambda: store.watch_status(conn)["locked"] is False)

    run(scenario())
    assert provider.queries == ["sepsis"]


def test_a_shutdown_mid_sweep_leaves_a_readable_state(
    db: Path, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Sepsis", query="sepsis")
    watch, provider = blocking_watcher(db)
    # Shutdown waits a moment for a cycle to finish; it must not wait out a
    # provider that never answers.
    monkeypatch.setattr(scheduler, "STOP_DRAIN_SECONDS", 0.05)

    async def scenario() -> None:
        await watch.start()
        await until(provider.started.is_set)
        await watch.aclose()
        assert watch.running is False

        status = store.watch_status(conn)
        assert status["weekly_enabled"] is True
        assert status["lock_holder"].startswith(watch._holder)
        assert [item["id"] for item in status["topics"]] == [topic.id]

        provider.release.set()
        await until(lambda: store.watch_status(conn)["locked"] is False)

    run(scenario())

    # The cycle the shutdown cancelled ran to the end on its own thread, so it
    # closed its own check row. Nothing is left at `running` for the startup
    # sweep to clean up, and running it anyway changes nothing.
    (check,) = store.list_checks(conn, topic.id)
    assert check.status == "ok"
    jobs.sweep_interrupted(conn)
    assert store.get_check(conn, check.id).status == "ok"


# -- defect 2: a settings change takes effect without a restart -----------------


def test_switching_the_weekly_sweep_on_starts_the_loop(db: Path, conn: Any) -> None:
    """The defect: enabling it did nothing until the process was restarted."""
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider, weekly_enabled=False)

    async def scenario() -> None:
        await watch.start()
        assert watch.running is False

        # What the settings route does: write the choice, then tell the watcher.
        enable_weekly(conn)
        await asyncio.to_thread(
            watch.apply_settings, weekly_enabled=True, interval_hours=168.0
        )
        assert watch.running is True
        await until(lambda: provider.queries == ["sepsis"])
        await watch.aclose()

    run(scenario())
    assert len(store.list_updates(conn)) == 1


def test_switching_the_weekly_sweep_off_stops_the_loop_and_frees_the_lease(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)

    async def scenario() -> None:
        await watch.start()
        await until(lambda: provider.queries == ["sepsis"])

        store.set_settings(conn, weekly_enabled=False, interval_hours=168.0)
        await asyncio.to_thread(
            watch.apply_settings, weekly_enabled=False, interval_hours=168.0
        )
        assert watch.running is False
        assert watch._task is None, "no task left behind"
        assert store.watch_status(conn)["locked"] is False, "no lease left behind"

        await asyncio.sleep(0.05)
        assert provider.queries == ["sepsis"], "a stopped loop does not sweep"
        await watch.aclose()

    run(scenario())


def test_applying_the_same_settings_again_changes_nothing(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)

    async def scenario() -> None:
        await watch.start()
        await until(lambda: provider.queries == ["sepsis"])
        task = watch._task

        for _ in range(3):
            await asyncio.to_thread(
                watch.apply_settings, weekly_enabled=True, interval_hours=168.0
            )
            assert watch.running is True
            assert watch._task is task, "one loop, not four"

        await asyncio.sleep(0.05)
        assert provider.queries == ["sepsis"], "re-applying is not a re-check"

        # And off twice in a row is just off.
        for _ in range(2):
            await asyncio.to_thread(
                watch.apply_settings, weekly_enabled=False, interval_hours=168.0
            )
            assert watch.running is False
        await watch.aclose()

    run(scenario())


def test_a_new_interval_reaches_the_sleeping_loop(db: Path, conn: Any) -> None:
    """No restart, no dropped topic: the sleeper re-times where it stands."""
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)}
    )
    watch = watcher(db, provider)

    async def scenario() -> None:
        await watch.start()
        await until(lambda: provider.queries == ["sepsis"])
        task = watch._task
        assert watch._wake_seconds() == scheduler.MAX_SLEEP_SECONDS

        await asyncio.to_thread(
            watch.apply_settings, weekly_enabled=True, interval_hours=0.001
        )
        assert watch.interval_hours == 0.001
        assert watch._wake_seconds() == 3.6, "the new interval, not the old week"
        # The sleeper woke and cleared the flag rather than sleeping out the
        # old week, and it is the same loop that woke.
        await until(lambda: not watch._reschedule.is_set())
        assert watch._task is task

        due = store.get_topic(conn, store.list_topics(conn)[0].id).next_due_at
        assert due is not None, "the topic still has its place in the queue"
        await watch.aclose()

    run(scenario())


def test_settings_can_be_applied_while_a_check_is_in_flight(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Sepsis", query="sepsis")
    watch, provider = blocking_watcher(db)

    async def scenario() -> None:
        await watch.start()
        await until(provider.started.is_set)

        applied = asyncio.create_task(
            asyncio.to_thread(
                watch.apply_settings, weekly_enabled=False, interval_hours=24.0
            )
        )
        # The in-flight cycle finishes on its own thread; the stop waits for it.
        await asyncio.sleep(0.02)
        provider.release.set()
        await applied

        assert watch.running is False
        assert watch.interval_hours == 24.0
        await until(lambda: store.watch_status(conn)["locked"] is False)

    run(scenario())
    assert provider.queries == ["sepsis"], "the in-flight check was not repeated"


def test_applying_settings_with_no_loop_to_talk_to_is_harmless(
    db: Path, conn: Any
) -> None:
    """Constructed outside a loop and never started: record it and move on."""
    watch = watcher(db, fake_pubmed.RecordingProvider(), weekly_enabled=False)
    watch.apply_settings(weekly_enabled=True, interval_hours=42.0)
    assert watch.weekly_enabled is True
    assert watch.interval_hours == 42.0
    assert watch.running is False


# -- defect 4: what the status-change callback is promised ----------------------


def retracting_provider(stage: str) -> Any:
    return fake_pubmed.RecordingProvider(
        {"covid": articles_for(stage)},
    )


def test_a_newly_retracted_record_reaches_the_callback_once(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.NOT_YET_RETRACTED)
    seen: list[list[dict[str, Any]]] = []
    watch = watcher(db, provider, on_status_change=seen.append)

    assert watch._cycle(conn, "manual", None)["status"] == "ok"
    assert seen == [], "a first sighting is not a change"

    provider.searches["covid"] = articles_for(fake_pubmed.RETRACTED)
    assert watch._cycle(conn, "manual", None)["status"] == "ok"
    assert len(seen) == 1
    (change,) = seen[0]
    assert change["newly"] == "retracted"
    assert (change["retracted"], change["corrected"]) == (True, False)
    assert set(change) >= {"record_id", "retracted", "corrected", "newly"}

    # Seen again, unchanged: nothing more is said.
    watch._cycle(conn, "manual", None)
    assert len(seen) == 1


def test_a_newly_corrected_record_is_reported_and_is_not_a_retraction(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.NOT_YET_CORRECTED)
    seen: list[list[dict[str, Any]]] = []
    watch = watcher(db, provider, on_status_change=seen.append)

    watch._cycle(conn, "manual", None)
    provider.searches["covid"] = articles_for(fake_pubmed.CORRECTED)
    watch._cycle(conn, "manual", None)

    assert len(seen) == 1
    (change,) = seen[0]
    assert change["newly"] == "corrected"
    assert (change["retracted"], change["corrected"]) == (False, True)
    assert change["is_notice"] is False


def test_a_notice_is_reported_as_a_notice(db: Path, conn: Any) -> None:
    """A retraction notice is never evidence, and the caller is told so."""
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = fake_pubmed.RecordingProvider(
        {"covid": articles_for(fake_pubmed.RETRACTION_NOTICE)}
    )
    seen: list[list[dict[str, Any]]] = []
    watch = watcher(db, provider, on_status_change=seen.append)

    watch._cycle(conn, "manual", None)
    assert [change["newly"] for change in seen[0]] == ["notice"]
    assert seen[0][0]["is_notice"] is True
    assert seen[0][0]["retracted"] is False, "the notice is not itself retracted"

    watch._cycle(conn, "manual", None)
    assert len(seen) == 1, "reported once, not on every check"


def test_an_unchanged_rediscovery_tells_the_callback_nothing(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.RETRACTED)
    seen: list[list[dict[str, Any]]] = []
    watch = watcher(db, provider, on_status_change=seen.append)

    for _ in range(3):
        assert watch._cycle(conn, "manual", None)["status"] == "ok"
    assert seen == []


def test_the_callback_only_ever_sees_a_committed_row(db: Path, conn: Any) -> None:
    """It may act on what it is told, so what it is told must be on disk."""
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.NOT_YET_RETRACTED)
    read_back: list[int] = []

    def on_change(changes: list[dict[str, Any]]) -> None:
        # A connection of its own, so only committed rows are visible.
        other = connect(db)
        try:
            for change in changes:
                row = other.execute(
                    "SELECT retracted FROM literature_records WHERE id = ?",
                    (change["record_id"],),
                ).fetchone()
                read_back.append(int(row["retracted"]))
        finally:
            other.close()

    watch = watcher(db, provider, on_status_change=on_change)
    watch._cycle(conn, "manual", None)
    provider.searches["covid"] = articles_for(fake_pubmed.RETRACTED)
    watch._cycle(conn, "manual", None)

    assert read_back == [1], "the flag was already committed when we were told"


def test_a_throwing_callback_neither_stops_the_sweep_nor_undoes_the_write(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.NOT_YET_RETRACTED)

    def explode(changes: list[dict[str, Any]]) -> None:
        raise RuntimeError("the caller's problem, not the watcher's")

    watch = watcher(db, provider, on_status_change=explode)
    watch._cycle(conn, "manual", None)
    provider.searches["covid"] = articles_for(fake_pubmed.RETRACTED)

    outcome = watch._cycle(conn, "manual", None)
    assert outcome["status"] == "ok"
    update = store.list_updates(conn)[0]
    assert update.retracted is True, "the write survived the bad callback"
    assert update.state == "unread"
    assert store.watch_status(conn)["locked"] is False


def test_a_status_change_still_reaches_the_caller_when_the_check_row_fails(
    db: Path, conn: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The records were committed; losing the notification would lose them."""
    enable_weekly(conn)
    store.create_topic(conn, label="Covid", query="covid")
    provider = retracting_provider(fake_pubmed.NOT_YET_RETRACTED)
    seen: list[list[dict[str, Any]]] = []
    watch = watcher(db, provider, on_status_change=seen.append)

    watch._cycle(conn, "manual", None)
    provider.searches["covid"] = articles_for(fake_pubmed.RETRACTED)

    real = store.finish_check
    calls = {"n": 0}

    def sometimes_broken(connection: Any, check_id: str, **kwargs: Any) -> Any:
        calls["n"] += 1
        if kwargs.get("status") == "ok":
            raise sqlite3.OperationalError("disk gone")
        return real(connection, check_id, **kwargs)

    monkeypatch.setattr(store, "finish_check", sometimes_broken)
    outcome = watch._cycle(conn, "manual", None)

    assert outcome["results"][0]["status"] == "failed"
    assert [change["newly"] for change in seen[0]] == ["retracted"]
    assert store.list_updates(conn)[0].retracted is True


# -- stopping mid-sweep --------------------------------------------------------


def test_a_check_left_running_by_a_shutdown_is_readable_afterwards(
    db: Path, conn: Any
) -> None:
    """The existing startup sweep closes it out; this does not duplicate that."""
    topic = store.create_topic(conn, label="Sepsis", query="sepsis")
    started = store.begin_check(conn, topic.id, "scheduled")
    assert store.get_check(conn, started.id).status == "running"

    jobs.sweep_interrupted(conn)

    closed = store.get_check(conn, started.id)
    assert closed.status == "failed"
    assert closed.failure_category == "interrupted"
    assert closed.finished_at is not None
    assert store.get_topic(conn, topic.id).last_checked_at is None


def test_closing_the_watcher_frees_its_lock(db: Path, conn: Any) -> None:
    watch = watcher(db, fake_pubmed.RecordingProvider())
    run(watch.start())
    run(watch.aclose())
    assert watch.running is False
    assert store.watch_status(conn)["locked"] is False


# -- refresh, wired into the sweep ---------------------------------------------


def test_the_sweep_refreshes_linked_records_and_notices_a_retraction(
    db: Path, conn: Any
) -> None:
    """The end-to-end shape of defects 3 and 4, through the scheduler."""
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Covid", query="hydroxychloroquine")
    provider = fake_pubmed.RecordingProvider(
        {"hydroxychloroquine": articles_for(fake_pubmed.NOT_YET_RETRACTED)}
    )
    watch = watcher(db, provider, on_status_change=lambda changes: seen.extend(changes))
    seen: list[dict[str, Any]] = []

    assert watch._cycle(conn, "scheduled", None)["status"] == "ok"
    update = store.list_updates(conn)[0]
    store.set_update_state(conn, update.id, "acknowledged")

    # The paper is now cited as evidence, and a date-sorted search will never
    # return it again.
    moment = iso(now())
    conn.execute(
        "INSERT INTO piles (id, title, tier, created_at, updated_at)"
        " VALUES ('pil_t', 'T', 'mid', ?, ?)",
        (moment, moment),
    )
    conn.execute(
        "INSERT INTO learning_points"
        " (id, pile_id, claim, support, content_hash, created_at, updated_at)"
        " VALUES ('lpt_t', 'pil_t', 'A claim', 'evidence_supported', 'h', ?, ?)",
        (moment, moment),
    )
    conn.execute(
        "INSERT INTO evidence_links"
        " (id, learning_point_id, record_id, relation, verified_at)"
        " VALUES ('evl_t', 'lpt_t', ?, 'supports', ?)",
        (update.record_id, moment),
    )

    # Upstream, it is retracted; the search still returns nothing new.
    provider.searches["hydroxychloroquine"] = []
    provider.refresh = articles_for(fake_pubmed.RETRACTED)
    store.update_topic(conn, topic.id, enabled=True)
    conn.execute(
        "UPDATE literature_topics SET next_due_at = ? WHERE id = ?",
        (iso(now() - timedelta(days=1)), topic.id),
    )

    outcome = watch._cycle(conn, "scheduled", None)
    assert provider.refreshed[-1] == ["40000003"]
    assert outcome["refresh"]["status"] == "ok"
    assert [change["newly"] for change in outcome["refresh"]["changed_status"]] == [
        "retracted"
    ]

    after = store.get_update(conn, update.id)
    assert after.retracted is True
    assert after.state == "unread"
    assert after.why_relevant == store.REASON_RETRACTED
    assert outcome["unread"] == 1
    assert [change["newly"] for change in seen] == ["retracted"]


def cite_first_record(connection: sqlite3.Connection, record_id: str) -> None:
    """Make a record evidence-linked, which is what refresh selects on."""
    moment = iso(now())
    connection.execute(
        "INSERT OR IGNORE INTO piles (id, title, tier, created_at, updated_at)"
        " VALUES ('pil_f', 'F', 'mid', ?, ?)",
        (moment, moment),
    )
    connection.execute(
        "INSERT INTO learning_points"
        " (id, pile_id, claim, support, content_hash, created_at, updated_at)"
        " VALUES ('lpt_f', 'pil_f', 'A claim', 'evidence_supported', 'h', ?, ?)",
        (moment, moment),
    )
    connection.execute(
        "INSERT INTO evidence_links"
        " (id, learning_point_id, record_id, relation, verified_at)"
        " VALUES ('evl_f', 'lpt_f', ?, 'supports', ?)",
        (record_id, moment),
    )


def test_a_failing_refresh_does_not_fail_the_sweep(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Sepsis", query="sepsis")
    provider = fake_pubmed.RecordingProvider(
        {"sepsis": articles_for(fake_pubmed.WITH_ABSTRACT)},
        refresh=ProviderError("timeout"),
    )
    watch = watcher(db, provider)
    watch._cycle(conn, "scheduled", None)
    cite_first_record(conn, store.list_updates(conn)[0].record_id)
    conn.execute(
        "UPDATE literature_topics SET next_due_at = ? WHERE id = ?",
        (iso(now() - timedelta(days=1)), topic.id),
    )

    outcome = watch._cycle(conn, "scheduled", None)
    assert provider.refreshed, "the refresh really did ask the provider"
    assert outcome["status"] == "ok", "a broken refresh is not a broken sweep"
    assert outcome["results"][0]["status"] == "ok"
    assert outcome["refresh"] == {"status": "failed", "failure_category": "timeout"}
    assert store.get_topic(conn, topic.id).last_status == "ok"
    # And the lock came back, so the next sweep is not blocked by this one.
    assert store.watch_status(conn)["locked"] is False


def test_without_a_provider_nothing_is_recorded_as_a_successful_check(
    db: Path, conn: Any
) -> None:
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Sepsis", query="sepsis")
    outcome = watcher(db, None)._cycle(conn, "scheduled", None)

    assert outcome["results"][0]["reason"] == "provider_unavailable"
    assert outcome["refresh"]["reason"] == "provider_unavailable"
    assert store.get_topic(conn, topic.id).last_checked_at is None
    assert store.list_checks(conn, topic.id) == []


# -- the status a person reads -------------------------------------------------


def test_the_watcher_leaves_a_readable_status_behind(db: Path, conn: Any) -> None:
    enable_weekly(conn)
    topic = store.create_topic(conn, label="Bad", query="explodes")
    provider = fake_pubmed.RecordingProvider({"explodes": ProviderError("connection")})
    watch = watcher(db, provider)

    watch._cycle(conn, "scheduled", None)
    watch._cycle(conn, "manual", None)

    status = store.watch_status(conn)
    assert status["weekly_enabled"] is True
    assert status["locked"] is False
    shown = next(item for item in status["topics"] if item["id"] == topic.id)
    assert shown["last_status"] == "failed"
    assert shown["last_failure"] == "connection"
    assert shown["consecutive_failures"] == 2
    assert shown["backoff_multiplier"] == 4
    assert shown["next_due_at"] > iso(now())
