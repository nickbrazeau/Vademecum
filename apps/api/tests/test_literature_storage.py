"""Topics, checks, records, Today, settings and the scheduler lock.

The distinctions under test are the ones that are wrong when collapsed: new to
the library against recently published, retracted against corrected, and a
failed check against a stale success.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

import fake_pubmed
from vademecum.db import apply_migrations, connect
from vademecum.literature import pubmed
from vademecum.literature.http import ProviderError
from vademecum.literature.pubmed import Article, CorrectionNote, PubMedProvider
from vademecum.storage import literature as store
from vademecum.storage.common import NotFoundError


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_article(**kwargs: Any) -> Article:
    base: dict[str, Any] = {
        "pmid": "1",
        "doi": "10.1/a",
        "title": "A title",
        "journal": "Lancet",
        "abstract": "An abstract.",
        "published_on": iso(now())[:10],
        "provider_date": iso(now())[:10],
        "publication_types": ("Journal Article",),
        "url": "https://pubmed.ncbi.nlm.nih.gov/1/",
        "priority": "other",
    }
    base.update(kwargs)
    return Article(**base)


def fetched(xml: str, ids: tuple[str, ...] = fake_pubmed.DEFAULT_IDS) -> list[Article]:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(identifiers=ids, xml=xml), max_results=25
    )
    return provider.search("sepsis")


@pytest.fixture()
def topic(connection: sqlite3.Connection) -> store.Topic:
    return store.create_topic(connection, label="Sepsis", query="sepsis fluids")


# -- topics --------------------------------------------------------------------


def test_a_new_topic_is_due_immediately(connection: sqlite3.Connection) -> None:
    created = store.create_topic(connection, label="Sepsis", query="sepsis")
    assert created.enabled is True
    assert created.next_due_at is not None
    assert store.due_topics(connection, now=iso(now())) == [created]


def test_a_disabled_topic_is_never_due(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    store.update_topic(connection, topic.id, enabled=False)
    assert store.due_topics(connection, now=iso(now() + timedelta(days=400))) == []


def test_re_enabling_a_topic_makes_it_due_again(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    store.update_topic(connection, topic.id, enabled=False)
    revived = store.update_topic(connection, topic.id, enabled=True)
    assert revived.next_due_at is not None
    assert store.due_topics(connection, now=iso(now())) == [revived]


def test_a_topic_can_be_relabelled_without_losing_its_schedule(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    renamed = store.update_topic(connection, topic.id, label="Septic shock")
    assert renamed.label == "Septic shock"
    assert renamed.query == topic.query
    assert renamed.next_due_at == topic.next_due_at


def test_an_unknown_topic_is_a_not_found(connection: sqlite3.Connection) -> None:
    with pytest.raises(NotFoundError):
        store.get_topic(connection, "ltp_nope")
    with pytest.raises(NotFoundError):
        store.delete_topic(connection, "ltp_nope")


def test_deleting_a_topic_takes_its_checks_and_links(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    check = store.begin_check(connection, topic.id, "manual")
    store.record_articles(
        connection, topic_id=topic.id, check_id=check.id, articles=[make_article()]
    )
    store.delete_topic(connection, topic.id)
    assert store.list_updates(connection) == []
    # The record itself survives: it may be cited by a learning point.
    remaining = connection.execute("SELECT COUNT(*) AS n FROM literature_records")
    assert remaining.fetchone()["n"] == 1


# -- checks --------------------------------------------------------------------


def test_a_successful_check_advances_the_clock(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    check = store.begin_check(connection, topic.id, "scheduled")
    assert check.status == "running"
    store.finish_check(connection, check.id, status="ok", result_count=3, new_count=2)

    updated = store.get_topic(connection, topic.id)
    assert updated.last_status == "ok"
    assert updated.last_failure == ""
    assert updated.consecutive_failures == 0
    assert updated.last_checked_at is not None
    assert updated.next_due_at > iso(now() + timedelta(days=6))
    assert store.due_topics(connection, now=iso(now())) == []

    finished = store.get_check(connection, check.id)
    assert (finished.status, finished.result_count, finished.new_count) == ("ok", 3, 2)


def test_a_failed_check_does_not_advance_last_checked_at(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    first = store.begin_check(connection, topic.id, "scheduled")
    store.finish_check(connection, first.id, status="ok", result_count=1, new_count=1)
    after_success = store.get_topic(connection, topic.id)

    second = store.begin_check(connection, topic.id, "scheduled")
    store.finish_check(connection, second.id, status="failed", failure_category="timeout")
    after_failure = store.get_topic(connection, topic.id)

    assert after_failure.last_checked_at == after_success.last_checked_at, (
        "a failure must never read as a fresh success"
    )
    assert after_failure.last_status == "failed"
    assert after_failure.last_failure == "timeout"
    assert after_failure.consecutive_failures == 1
    assert store.get_check(connection, second.id).failure_category == "timeout"


def test_failures_back_off_exponentially_and_stop_doubling(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    interval = store.get_settings(connection)["interval_hours"]
    gaps: list[float] = []
    for _ in range(6):
        check = store.begin_check(connection, topic.id, "scheduled")
        store.finish_check(
            connection, check.id, status="failed", failure_category="connection"
        )
        updated = store.get_topic(connection, topic.id)
        finished = store.get_check(connection, check.id)
        gap = store._parse_timestamp(updated.next_due_at) - store._parse_timestamp(
            finished.finished_at
        )
        gaps.append(round(gap.total_seconds() / 3600.0, 3))

    assert gaps == [interval * factor for factor in (2, 4, 8, 16, 16, 16)]
    assert store.get_topic(connection, topic.id).consecutive_failures == 6


def test_a_success_clears_the_backoff(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    failed = store.begin_check(connection, topic.id, "scheduled")
    store.finish_check(connection, failed.id, status="failed", failure_category="timeout")
    good = store.begin_check(connection, topic.id, "scheduled")
    store.finish_check(connection, good.id, status="ok")

    topic_now = store.get_topic(connection, topic.id)
    assert topic_now.consecutive_failures == 0
    assert topic_now.last_failure == ""


def test_an_unknown_trigger_or_status_is_refused(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    with pytest.raises(ValueError):
        store.begin_check(connection, topic.id, "cron")
    check = store.begin_check(connection, topic.id, "manual")
    with pytest.raises(ValueError):
        store.finish_check(connection, check.id, status="running")


# -- records -------------------------------------------------------------------


def record_all(
    connection: sqlite3.Connection, topic: store.Topic, articles: list[Article]
) -> dict[str, Any]:
    check = store.begin_check(connection, topic.id, "manual")
    counts = store.record_articles(
        connection, topic_id=topic.id, check_id=check.id, articles=articles
    )
    store.finish_check(
        connection,
        check.id,
        status="ok",
        result_count=counts["returned"],
        new_count=counts["new_to_library"],
    )
    return counts


def test_the_same_pmid_twice_is_one_record(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    twice = fetched(
        fake_pubmed.article_set(fake_pubmed.WITH_ABSTRACT, fake_pubmed.DUPLICATE_PMID)
    )
    assert len(twice) == 2, "the provider returned it twice, as providers do"

    counts = record_all(connection, topic, twice)
    assert counts["returned"] == 2
    assert counts["new_to_library"] == 1
    assert counts["already_known"] == 1
    assert len(store.list_updates(connection)) == 1


def test_a_record_seen_in_a_later_check_is_not_new_again(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    articles = fetched(fake_pubmed.article_set(fake_pubmed.WITH_ABSTRACT))
    first = record_all(connection, topic, articles)
    second = record_all(connection, topic, articles)
    assert first["new_to_library"] == 1
    assert second["new_to_library"] == 0
    assert second["already_known"] == 1
    assert len(store.list_updates(connection)) == 1


def test_deduplication_falls_back_to_the_doi(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    one = make_article(pmid="", doi="10.1/shared", title="First sighting")
    two = make_article(pmid="", doi="10.1/shared", title="Second sighting")
    counts = record_all(connection, topic, [one, two])
    assert (counts["new_to_library"], counts["already_known"]) == (1, 1)
    rows = connection.execute("SELECT * FROM literature_records").fetchall()
    assert len(rows) == 1


def test_a_record_with_neither_identifier_is_not_stored(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    counts = record_all(connection, topic, [make_article(pmid="", doi="")])
    assert counts["returned"] == 1
    assert counts["new_to_library"] == 0
    assert connection.execute("SELECT COUNT(*) AS n FROM literature_records").fetchone()[
        "n"
    ] == 0


def test_a_later_pmid_fills_in_a_record_first_seen_by_doi(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, [make_article(pmid="", doi="10.1/late")])
    record_all(connection, topic, [make_article(pmid="9001", doi="10.1/late")])
    rows = connection.execute("SELECT pmid, doi FROM literature_records").fetchall()
    assert len(rows) == 1
    assert rows[0]["pmid"] == "9001"


def test_new_to_the_library_and_recently_published_are_different_facts(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    fresh_old = make_article(
        pmid="10", doi="10.1/old", published_on="1998-04-01", title="Old but unseen"
    )
    recent = make_article(
        pmid="11",
        doi="10.1/new",
        published_on=str(date.today() - timedelta(days=5)),
        title="Recent",
    )
    counts = record_all(connection, topic, [fresh_old, recent])
    assert counts["new_to_library"] == 2, "both are new to this database"
    assert counts["recently_published"] == 1, "only one was published recently"

    # And the other way round: known already, still recently published.
    again = record_all(connection, topic, [recent])
    assert again["new_to_library"] == 0
    assert again["already_known"] == 1
    assert again["recently_published"] == 1


def test_a_record_just_outside_the_window_is_not_recent(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    edge = date.today() - timedelta(days=store.RECENT_WINDOW_DAYS + 1)
    counts = record_all(connection, topic, [make_article(published_on=str(edge))])
    assert counts["recently_published"] == 0


def test_a_record_with_no_publication_date_is_not_counted_as_recent(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    counts = record_all(connection, topic, [make_article(published_on=None)])
    assert counts["recently_published"] == 0
    assert counts["new_to_library"] == 1


def test_retracted_and_corrected_are_stored_as_separate_flags(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(
        connection,
        topic,
        fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED, fake_pubmed.CORRECTED)),
    )
    by_pmid = {update.pmid: update for update in store.list_updates(connection)}
    assert (by_pmid["40000003"].retracted, by_pmid["40000003"].corrected) == (True, False)
    assert (by_pmid["40000004"].retracted, by_pmid["40000004"].corrected) == (False, True)


def test_a_status_flip_on_a_known_record_is_reported_to_the_caller(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    before = fetched(
        fake_pubmed.article_set(
            fake_pubmed.NOT_YET_RETRACTED, fake_pubmed.NOT_YET_CORRECTED
        )
    )
    first = record_all(connection, topic, before)
    assert first["changed_status"] == [], "nothing flipped on a first sighting"

    after = fetched(
        fake_pubmed.article_set(fake_pubmed.RETRACTED, fake_pubmed.CORRECTED)
    )
    second = record_all(connection, topic, after)
    assert len(second["changed_status"]) == 2

    flipped = {
        row["pmid"]: row
        for row in connection.execute(
            "SELECT pmid, retracted, corrected FROM literature_records"
        ).fetchall()
    }
    assert (flipped["40000003"]["retracted"], flipped["40000003"]["corrected"]) == (1, 0)
    assert (flipped["40000004"]["retracted"], flipped["40000004"]["corrected"]) == (0, 1)

    # Reported once: a third check of the same state is not a new flip.
    third = record_all(connection, topic, after)
    assert third["changed_status"] == []


def test_a_retraction_is_never_downgraded_to_nothing(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED)))
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    )
    row = connection.execute(
        "SELECT retracted FROM literature_records WHERE pmid = '40000003'"
    ).fetchone()
    assert row["retracted"] == 1


def check_id_of(connection: sqlite3.Connection, topic_record_id: str) -> str | None:
    """Which check a Today row currently shows a timestamp from."""
    row = connection.execute(
        "SELECT check_id FROM literature_topic_records WHERE id = ?", (topic_record_id,)
    ).fetchone()
    return None if row is None else row["check_id"]


# -- what changed_status tells the caller -------------------------------------


def test_changed_status_distinguishes_a_retraction_from_a_correction(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    """The caller must be able to tell the two apart without re-reading rows.

    ``flag_points_for_record`` cannot: it takes a free-text reason and holds
    everything the same way. So the distinction is carried here instead, and a
    correction never arrives looking like a retraction.
    """
    record_all(
        connection,
        topic,
        fetched(
            fake_pubmed.article_set(
                fake_pubmed.NOT_YET_RETRACTED, fake_pubmed.NOT_YET_CORRECTED
            )
        ),
    )
    counts = record_all(
        connection,
        topic,
        fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED, fake_pubmed.CORRECTED)),
    )

    changes = {change["newly"]: change for change in counts["changed_status"]}
    assert set(changes) == {"retracted", "corrected"}
    for change in changes.values():
        assert set(change) >= {
            "record_id",
            "retracted",
            "corrected",
            "newly",
        }
    assert (changes["retracted"]["retracted"], changes["retracted"]["corrected"]) == (
        True,
        False,
    )
    assert (changes["corrected"]["retracted"], changes["corrected"]["corrected"]) == (
        False,
        True,
    )
    assert changes["corrected"]["is_notice"] is False


def test_the_module_documents_that_a_correction_must_not_auto_release() -> None:
    """The rule lives where the caller of changed_status will read it."""
    text = (store.__doc__ or "").lower()
    assert "auto-release" in text or "auto release" in text
    assert "re-review" in text
    assert "correction is not a retraction" in text


# -- notices are not evidence --------------------------------------------------


def test_a_notice_is_flagged_as_one_on_the_stored_row(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(
        connection,
        topic,
        fetched(
            fake_pubmed.article_set(
                fake_pubmed.RETRACTION_NOTICE,
                fake_pubmed.ERRATUM_NOTICE,
                fake_pubmed.CONCERN_NOTICE,
                fake_pubmed.WITH_ABSTRACT,
            ),
            ids=("40000099", "40000098", "40000097", "40000001"),
        ),
    )
    by_pmid = {update.pmid: update for update in store.list_updates(connection)}

    for pmid in ("40000099", "40000098", "40000097"):
        assert by_pmid[pmid].is_notice is True, f"{pmid} is a notice, not evidence"
        assert by_pmid[pmid].retracted is False
        assert by_pmid[pmid].as_dict()["is_notice"] is True

    ordinary = by_pmid["40000001"]
    assert ordinary.is_notice is False


def test_a_notice_is_reported_to_the_caller_as_a_notice(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    """The caller has to know before anything can cite one as support.

    A notice never flips ``retracted`` or ``corrected`` -- it is born as the
    erratum about another paper -- so waiting for a flag flip would report it
    never.
    """
    counts = record_all(
        connection,
        topic,
        fetched(
            fake_pubmed.article_set(
                fake_pubmed.RETRACTION_NOTICE, fake_pubmed.WITH_ABSTRACT
            ),
            ids=("40000099", "40000001"),
        ),
    )
    (change,) = counts["changed_status"]
    assert change["newly"] == "notice"
    assert change["is_notice"] is True
    assert (change["retracted"], change["corrected"]) == (False, False)

    notice = next(
        update for update in store.list_updates(connection) if update.pmid == "40000099"
    )
    assert change["record_id"] == notice.record_id
    assert notice.is_notice is True

    ordinary = next(
        update for update in store.list_updates(connection) if update.pmid == "40000001"
    )
    assert ordinary.record_id != change["record_id"], "an article is not a notice"


def test_a_record_newly_recognised_as_a_notice_is_reported_once_and_resurfaces_nothing(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    plain = make_article(pmid="40000098", doi="10.1136/bmj.q1")
    record_all(connection, topic, [plain])
    first = store.list_updates(connection)[0]
    store.set_update_state(connection, first.id, "acknowledged")
    shown = check_id_of(connection, first.id)

    recognised = make_article(
        pmid="40000098",
        doi="10.1136/bmj.q1",
        publication_types=("Published Erratum",),
        correction_notes=(CorrectionNote(ref_type="ErratumFor", pmid="40000004"),),
    )
    counts = record_all(connection, topic, [recognised])
    assert [change["newly"] for change in counts["changed_status"]] == ["notice"]

    after = store.get_update(connection, first.id)
    assert after.is_notice is True
    # Nothing was withdrawn or corrected about a finding, so nothing resurfaces.
    assert after.state == "acknowledged"
    assert after.why_relevant == ""
    assert check_id_of(connection, first.id) == shown

    assert record_all(connection, topic, [recognised])["changed_status"] == []


def test_the_two_copies_of_the_notice_vocabulary_cannot_drift() -> None:
    """Storage decides notice-ness from a row; the provider from XML."""
    assert store.NOTICE_PUBLICATION_TYPES == pubmed.NOTICE_PUBLICATION_TYPES
    assert store.NOTICE_REF_TYPES == pubmed.NOTICE_REF_TYPES


def test_notice_status_round_trips_through_the_stored_columns() -> None:
    assert store.is_notice_row('["Published Erratum"]', "[]") is True
    assert (
        store.is_notice_row('["Journal Article"]', '[{"ref_type": "RetractionOf"}]')
        is True
    )
    assert (
        store.is_notice_row('["Journal Article"]', '[{"ref_type": "RetractionIn"}]')
        is False
    )
    assert store.is_notice_row("[]", "[]") is False
    assert store.is_notice_row(None, "not json") is False


# -- a status change resurfaces the record ------------------------------------


def test_a_retraction_resurfaces_an_acknowledged_record_with_the_reason(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    """The defect: it stayed acknowledged, unread stayed 0, checked_at was old."""
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    )
    first = store.list_updates(connection)[0]
    first_check = check_id_of(connection, first.id)
    store.set_update_state(connection, first.id, "acknowledged")
    assert store.unread_count(connection) == 0

    counts = record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED))
    )
    assert [change["newly"] for change in counts["changed_status"]] == ["retracted"]

    after = store.get_update(connection, first.id)
    assert after.state == "unread", "an acknowledgement does not cover a withdrawal"
    assert store.unread_count(connection) == 1
    assert after.retracted is True
    assert after.why_relevant == store.REASON_RETRACTED
    assert "retracted" in after.why_relevant.lower()
    assert after.checked_at >= first.checked_at
    assert check_id_of(connection, first.id) != first_check, (
        "shown as found by the check that noticed, not the one that first saw it"
    )
    assert after.first_seen_at == first.first_seen_at, "not a fresh find"


def test_a_correction_resurfaces_a_dismissed_record_and_says_so(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_CORRECTED))
    )
    first = store.list_updates(connection)[0]
    store.set_update_state(connection, first.id, "dismissed")

    record_all(connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.CORRECTED)))
    after = store.get_update(connection, first.id)
    assert after.state == "unread"
    assert after.corrected is True
    assert after.retracted is False
    assert after.why_relevant == store.REASON_CORRECTED


def test_routine_rediscovery_never_resurfaces_an_acknowledgement(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    """Only a change resurfaces. Seeing the same paper again is not one."""
    articles = fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED))
    record_all(connection, topic, articles)
    first = store.list_updates(connection)[0]
    store.set_update_state(connection, first.id, "acknowledged")
    shown = check_id_of(connection, first.id)

    for _ in range(3):
        counts = record_all(connection, topic, articles)
        assert counts["changed_status"] == []
    after = store.get_update(connection, first.id)
    assert after.state == "acknowledged"
    assert after.why_relevant == "", "nothing changed, so there is nothing to say"
    assert store.unread_count(connection) == 0
    assert check_id_of(connection, first.id) == shown, "and the date does not move"
    assert after.checked_at == first.checked_at


def test_a_change_resurfaces_the_record_for_every_topic_that_linked_it(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    other = store.create_topic(connection, label="Fluids", query="crystalloid")
    before = fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    record_all(connection, topic, before)
    record_all(connection, other, before)
    for update in store.list_updates(connection):
        store.set_update_state(connection, update.id, "acknowledged")

    untouched = store.get_topic(connection, other.id)
    record_all(connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED)))
    assert store.unread_count(connection) == 2

    # Each row shows a check belonging to its own topic, not a borrowed one.
    for update in store.list_updates(connection):
        shown = check_id_of(connection, update.id)
        assert store.get_check(connection, shown).topic_id == update.topic_id
        assert update.why_relevant == store.REASON_RETRACTED
    # And the other topic's own schedule did not move: it was not searched.
    after = store.get_topic(connection, other.id)
    assert (after.last_checked_at, after.next_due_at) == (
        untouched.last_checked_at,
        untouched.next_due_at,
    )


def test_an_empty_abstract_survives_storage(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NO_ABSTRACT)))
    update = store.list_updates(connection)[0]
    assert update.abstract == ""
    assert update.title == "Abstract unavailable for this record"


def test_a_record_seen_by_two_topics_is_linked_twice_but_stored_once(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    other = store.create_topic(connection, label="Fluids", query="crystalloid")
    articles = fetched(fake_pubmed.article_set(fake_pubmed.WITH_ABSTRACT))
    record_all(connection, topic, articles)
    counts = record_all(connection, other, articles)

    assert counts["new_to_library"] == 0
    assert connection.execute("SELECT COUNT(*) AS n FROM literature_records").fetchone()[
        "n"
    ] == 1
    assert len(store.list_updates(connection)) == 2


def test_articles_for_an_unknown_topic_are_refused(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(NotFoundError):
        store.record_articles(
            connection, topic_id="ltp_nope", check_id="lck_nope", articles=[]
        )


# -- refreshing what is already cited as evidence ------------------------------


def cite_as_evidence(connection: sqlite3.Connection, record_id: str) -> str:
    """Give a record an evidence_links row, which is what refresh selects on."""
    moment = iso(now())
    point_id = f"lpt_{record_id}"
    connection.execute(
        "INSERT OR IGNORE INTO piles (id, title, tier, created_at, updated_at)"
        " VALUES ('pil_test', 'Test', 'mid', ?, ?)",
        (moment, moment),
    )
    connection.execute(
        "INSERT INTO learning_points"
        " (id, pile_id, claim, support, content_hash, created_at, updated_at)"
        " VALUES (?, 'pil_test', 'A claim', 'evidence_supported', ?, ?, ?)",
        (point_id, point_id, moment, moment),
    )
    connection.execute(
        "INSERT INTO evidence_links"
        " (id, learning_point_id, record_id, relation, verified_at)"
        " VALUES (?, ?, ?, 'supports', ?)",
        (f"evl_{record_id}", point_id, record_id, moment),
    )
    return point_id


def record_id_for(connection: sqlite3.Connection, pmid: str) -> str:
    row = connection.execute(
        "SELECT id FROM literature_records WHERE pmid = ?", (pmid,)
    ).fetchone()
    assert row is not None
    return row["id"]


class StubProvider:
    """Returns canned articles for whatever PMIDs it is asked about."""

    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles
        self.asked: list[list[str]] = []

    def fetch_by_pmid(self, pmids: list[str]) -> list[Article]:
        self.asked.append(list(pmids))
        return list(self.articles)


def test_a_linked_record_that_becomes_retracted_is_picked_up_by_refresh(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    """The defect: a date-sorted search never returns an old paper again.

    Nothing ever re-read it, so its retraction was never noticed. Refresh
    selects on being cited, not on any date window.
    """
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    )
    update = store.list_updates(connection)[0]
    search_check = check_id_of(connection, update.id)
    store.set_update_state(connection, update.id, "acknowledged")
    cite_as_evidence(connection, update.record_id)

    provider = StubProvider(fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED)))
    outcome = store.refresh_records(connection, provider, limit=10)

    assert provider.asked == [["40000003"]]
    assert (outcome["considered"], outcome["refreshed"]) == (1, 1)
    assert [change["newly"] for change in outcome["changed_status"]] == ["retracted"]
    assert outcome["changed_status"][0]["record_id"] == update.record_id

    after = store.get_update(connection, update.id)
    assert after.retracted is True
    assert after.state == "unread", "it must come back in front of the owner"
    assert after.why_relevant == store.REASON_RETRACTED
    assert after.checked_at >= update.checked_at
    assert check_id_of(connection, update.id) != search_check, (
        "the timestamp shown is when the refresh found out"
    )
    assert store.unread_count(connection) == 1


def test_refresh_only_asks_about_records_that_are_cited(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.DEFAULT_XML))
    cited = record_id_for(connection, "40000003")
    cite_as_evidence(connection, cited)

    provider = StubProvider([])
    outcome = store.refresh_records(connection, provider, limit=10)
    assert provider.asked == [["40000003"]], "an unlinked record is not worth a call"
    assert outcome["considered"] == 1


def test_refresh_is_bounded_and_takes_the_oldest_refreshed_first(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    for index in range(5):
        record_all(
            connection,
            topic,
            [make_article(pmid=str(9000 + index), doi=f"10.1/r{index}")],
        )
        cite_as_evidence(connection, record_id_for(connection, str(9000 + index)))

    provider = StubProvider([])
    store.refresh_records(connection, provider, limit=2)
    assert provider.asked == [["9000", "9001"]], "oldest refreshed first, bounded"

    # 9000 and 9001 keep their old updated_at because nothing came back for
    # them, so a second pass with a wider limit still starts where it left off.
    store.refresh_records(connection, provider, limit=3)
    assert provider.asked[1] == ["9000", "9001", "9002"]

    huge = store.refresh_records(connection, provider, limit=10_000)
    assert huge["considered"] <= store.MAX_REFRESH_LIMIT


def test_refresh_never_invents_a_record_it_was_not_asked_about(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, [make_article(pmid="9100", doi="10.1/known")])
    cite_as_evidence(connection, record_id_for(connection, "9100"))

    stranger = make_article(pmid="9999", doi="10.1/stranger", title="Never asked for")
    outcome = store.refresh_records(connection, StubProvider([stranger]), limit=5)

    assert outcome["refreshed"] == 0
    assert connection.execute(
        "SELECT COUNT(*) AS n FROM literature_records WHERE pmid = '9999'"
    ).fetchone()["n"] == 0, "discovering records is what a search is for"


def test_refresh_does_not_move_the_topics_own_due_clock(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    )
    cite_as_evidence(connection, record_id_for(connection, "40000003"))
    before = store.get_topic(connection, topic.id)

    store.refresh_records(
        connection,
        StubProvider(fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED))),
        limit=5,
    )
    after = store.get_topic(connection, topic.id)
    assert after.next_due_at == before.next_due_at
    assert after.last_checked_at == before.last_checked_at


def test_refresh_reports_nothing_when_nothing_changed(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    articles = fetched(fake_pubmed.article_set(fake_pubmed.RETRACTED))
    record_all(connection, topic, articles)
    update = store.list_updates(connection)[0]
    store.set_update_state(connection, update.id, "acknowledged")
    cite_as_evidence(connection, update.record_id)

    outcome = store.refresh_records(connection, StubProvider(articles), limit=5)
    assert outcome["refreshed"] == 1
    assert outcome["changed_status"] == []
    assert store.get_update(connection, update.id).state == "acknowledged"


def test_refresh_with_nothing_cited_makes_no_provider_call(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.DEFAULT_XML))
    provider = StubProvider([])
    outcome = store.refresh_records(connection, provider, limit=5)
    assert provider.asked == []
    assert outcome == {"considered": 0, "refreshed": 0, "changed_status": []}


def test_a_refresh_that_raises_leaves_the_records_alone(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(
        connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.NOT_YET_RETRACTED))
    )
    cite_as_evidence(connection, record_id_for(connection, "40000003"))

    class Broken:
        def fetch_by_pmid(self, pmids: list[str]) -> list[Article]:
            raise ProviderError("timeout")

    with pytest.raises(ProviderError):
        store.refresh_records(connection, Broken(), limit=5)
    assert store.list_updates(connection)[0].retracted is False


# -- Today ---------------------------------------------------------------------


def test_updates_carry_the_topic_the_check_and_the_citation(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.article_set(fake_pubmed.WITH_ABSTRACT)))
    update = store.list_updates(connection)[0]

    assert update.topic_label == "Sepsis"
    assert update.checked_at is not None
    assert update.title == "Balanced crystalloids in sepsis"
    assert update.journal == "N Engl J Med"
    assert update.url == "https://pubmed.ncbi.nlm.nih.gov/40000001/"
    assert update.publication_types == (
        "Journal Article",
        "Randomized Controlled Trial",
    )
    assert update.state == "unread"
    assert update.as_dict()["publication_types"] == list(update.publication_types)


def test_updates_are_newest_first_and_bounded(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.DEFAULT_XML))
    updates = store.list_updates(connection, limit=2)
    assert len(updates) == 2
    assert updates[0].first_seen_at >= updates[1].first_seen_at


def test_states_move_and_are_countable(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    record_all(connection, topic, fetched(fake_pubmed.DEFAULT_XML))
    updates = store.list_updates(connection)
    assert store.unread_count(connection) == len(updates)

    store.set_update_state(connection, updates[0].id, "acknowledged")
    store.set_update_state(connection, updates[1].id, "dismissed")
    assert store.unread_count(connection) == len(updates) - 2
    assert [u.id for u in store.list_updates(connection, state="acknowledged")] == [
        updates[0].id
    ]

    with pytest.raises(ValueError):
        store.set_update_state(connection, updates[0].id, "archived")
    with pytest.raises(NotFoundError):
        store.set_update_state(connection, "ltr_nope", "dismissed")


def test_resurfacing_a_record_does_not_mark_it_unread_again(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    articles = fetched(fake_pubmed.article_set(fake_pubmed.WITH_ABSTRACT))
    record_all(connection, topic, articles)
    first = store.list_updates(connection)[0]
    store.set_update_state(connection, first.id, "dismissed")

    record_all(connection, topic, articles)
    assert store.list_updates(connection)[0].state == "dismissed"
    assert store.unread_count(connection) == 0


# -- settings ------------------------------------------------------------------


def test_weekly_checking_is_off_until_the_owner_turns_it_on(
    connection: sqlite3.Connection,
) -> None:
    """Opt-in. An unattended loop that reaches the network is not a default."""
    assert store.DEFAULT_WEEKLY_ENABLED is False
    assert store.get_settings(connection)["weekly_enabled"] is False

    # Seeding is configuration, and configuration cannot say "on".
    store.seed_settings(connection, interval_hours=24.0)
    assert store.get_settings(connection)["weekly_enabled"] is False
    assert store.get_settings(connection)["interval_hours"] == 24.0

    store.set_settings(connection, weekly_enabled=True, interval_hours=24.0)
    assert store.get_settings(connection)["weekly_enabled"] is True


def test_settings_have_defaults_and_persist(connection: sqlite3.Connection) -> None:
    assert store.get_settings(connection) == {
        "weekly_enabled": False,
        "interval_hours": store.DEFAULT_INTERVAL_HOURS,
    }
    store.set_settings(connection, weekly_enabled=False, interval_hours=24.0)
    assert store.get_settings(connection) == {
        "weekly_enabled": False,
        "interval_hours": 24.0,
    }
    stored = {
        row["key"]: row["value"]
        for row in connection.execute("SELECT key, value FROM app_state").fetchall()
    }
    assert stored[store.SETTING_WEEKLY_ENABLED] == "0"
    assert stored[store.SETTING_INTERVAL_HOURS] == "24.0"


def test_an_absurd_interval_is_clamped(connection: sqlite3.Connection) -> None:
    assert store.set_settings(connection, weekly_enabled=True, interval_hours=0.0)[
        "interval_hours"
    ] == store.MIN_INTERVAL_HOURS
    assert store.set_settings(connection, weekly_enabled=True, interval_hours=1e9)[
        "interval_hours"
    ] == store.MAX_INTERVAL_HOURS


def test_seeding_does_not_override_a_stored_choice(
    connection: sqlite3.Connection,
) -> None:
    store.set_settings(connection, weekly_enabled=True, interval_hours=24.0)
    store.seed_settings(connection, interval_hours=168.0)
    assert store.get_settings(connection)["weekly_enabled"] is True
    assert store.get_settings(connection)["interval_hours"] == 24.0


def test_the_stored_interval_is_what_a_check_schedules_by(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    store.set_settings(connection, weekly_enabled=True, interval_hours=12.0)
    check = store.begin_check(connection, topic.id, "manual")
    store.finish_check(connection, check.id, status="ok")
    updated = store.get_topic(connection, topic.id)
    gap = store._parse_timestamp(updated.next_due_at) - store._parse_timestamp(
        store.get_check(connection, check.id).finished_at
    )
    assert round(gap.total_seconds() / 3600.0, 3) == 12.0


def test_a_corrupt_stored_interval_falls_back_to_the_default(
    connection: sqlite3.Connection,
) -> None:
    connection.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, 'soon', 'x')",
        (store.SETTING_INTERVAL_HOURS,),
    )
    assert store.get_settings(connection)["interval_hours"] == store.DEFAULT_INTERVAL_HOURS


# -- the scheduler lock --------------------------------------------------------


def test_two_concurrent_acquires_produce_exactly_one_winner(
    connection: sqlite3.Connection, database_path: Path
) -> None:
    second = connect(database_path)
    try:
        moment = iso(now())
        first_won = store.acquire_lock(
            connection, holder="run-a", ttl_seconds=600, now=moment
        )
        second_won = store.acquire_lock(second, holder="run-b", ttl_seconds=600, now=moment)
        assert [first_won, second_won] == [True, False]
    finally:
        second.close()


def test_the_same_holder_may_re_acquire(connection: sqlite3.Connection) -> None:
    moment = iso(now())
    assert store.acquire_lock(connection, holder="run-a", ttl_seconds=600, now=moment)
    assert store.acquire_lock(connection, holder="run-a", ttl_seconds=600, now=moment)


def test_an_expired_lock_can_be_taken_over(connection: sqlite3.Connection) -> None:
    """A crashed run costs one TTL, not the watcher forever."""
    crashed_at = iso(now() - timedelta(hours=2))
    assert store.acquire_lock(
        connection, holder="crashed", ttl_seconds=60, now=crashed_at
    )
    assert not store.acquire_lock(
        connection, holder="next", ttl_seconds=60, now=iso(now() - timedelta(hours=2))
    )
    assert store.acquire_lock(connection, holder="next", ttl_seconds=60, now=iso(now()))


def test_releasing_frees_the_lock_but_only_for_its_holder(
    connection: sqlite3.Connection,
) -> None:
    moment = iso(now())
    store.acquire_lock(connection, holder="run-a", ttl_seconds=600, now=moment)

    store.release_lock(connection, "someone-else")
    assert not store.acquire_lock(connection, holder="run-b", ttl_seconds=600, now=moment)

    store.release_lock(connection, "run-a")
    assert store.acquire_lock(connection, holder="run-b", ttl_seconds=600, now=moment)


def test_releasing_a_lock_nobody_holds_is_harmless(
    connection: sqlite3.Connection,
) -> None:
    store.release_lock(connection, "run-a")
    assert store.acquire_lock(
        connection, holder="run-b", ttl_seconds=600, now=iso(now())
    )


def test_the_lock_lives_in_the_database_so_a_backup_carries_it(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    store.acquire_lock(connection, holder="run-a", ttl_seconds=600, now=iso(now()))
    row = connection.execute(
        "SELECT value FROM app_state WHERE key = ?", (store.LOCK_KEY,)
    ).fetchone()
    assert "run-a" in row["value"]


# -- correction notes round-trip ----------------------------------------------


def test_the_watch_status_is_readable_without_running_anything(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    check = store.begin_check(connection, topic.id, "scheduled")
    store.finish_check(connection, check.id, status="failed", failure_category="timeout")

    status = store.watch_status(connection)
    assert status["weekly_enabled"] is False
    assert status["locked"] is False
    assert status["lock_holder"] == ""
    shown = status["topics"][0]
    assert shown["last_status"] == "failed"
    assert shown["last_failure"] == "timeout"
    assert shown["consecutive_failures"] == 1
    assert shown["next_due_at"] is not None
    assert shown["backoff_multiplier"] == 2

    store.acquire_lock(connection, holder="run-a", ttl_seconds=600, now=iso(now()))
    held = store.watch_status(connection)
    assert held["locked"] is True
    assert held["lock_holder"] == "run-a"
    # An expired lock reads as free, which is what stops a crash wedging it.
    assert store.watch_status(connection, now=iso(now() + timedelta(hours=2)))[
        "locked"
    ] is False


def test_correction_notes_keep_the_raw_reftype_and_pmid(
    connection: sqlite3.Connection, topic: store.Topic
) -> None:
    article = make_article(
        corrected=True,
        correction_notes=(CorrectionNote(ref_type="ErratumIn", pmid="777"),),
    )
    record_all(connection, topic, [article])
    update = store.list_updates(connection)[0]
    assert update.correction_notes == ({"ref_type": "ErratumIn", "pmid": "777"},)


def test_a_fresh_database_has_no_literature_state(tmp_path: Path) -> None:
    """Nothing is created until the owner asks for it."""
    connection = connect(tmp_path / "fresh.sqlite3")
    try:
        apply_migrations(connection)
        assert store.list_topics(connection) == []
        assert store.list_updates(connection) == []
        assert store.unread_count(connection) == 0
    finally:
        connection.close()
