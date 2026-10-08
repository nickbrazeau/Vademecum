"""Watched topics, checks, and the records a check returned.

Three distinctions this module exists to keep, because each is easy to collapse
and each is wrong when collapsed:

* **new to your library** is not **recently published**. The first is about this
  database, the second about the calendar. They are counted separately and
  reported separately.
* **retracted** is not **corrected**. Two columns, two flags, never merged.
* a **failed check** is not a **stale success**. A failure records its category
  and backs the topic off; it does not touch ``last_checked_at``, so nothing can
  read a failure as "checked, nothing new".

Two paths write records. :func:`record_articles` handles what a topic search
returned; :func:`refresh_records` re-reads records that are already cited as
evidence, which a date-sorted search will never return again. Both share the
same status rules.

**What ``changed_status`` obliges the caller to do.** When a flag newly turns
true, the affected topic-record rows go back to ``unread`` with a reason, and
one entry per record comes back as::

    {"record_id": ..., "retracted": bool, "corrected": bool,
     "is_notice": bool, "newly": "retracted" | "corrected" | "notice"}

``newly`` exists so the caller can tell them apart, because the correct
response differs:

* ``retracted`` -- the paper is withdrawn; dependent points and questions are
  held.
* ``corrected`` -- an erratum or expression of concern applies. Dependent
  points and questions must be **held pending an explicit human re-review** and
  must **never be auto-released**. Nothing may read "the correction is filed"
  or "the record was seen again" as a reason to release a hold; only a person
  re-reviewing the claim may do that. A correction is not a retraction, and it
  is also not an all-clear.
* ``notice`` -- the record *is* the retraction notice, erratum or expression of
  concern about some other paper. It is not evidence for anything and must
  never be cited as support. Reported the first time the record is stored or
  the first time it is recognised as one; it resurfaces nothing, because there
  is no acknowledgement of a finding to withdraw.

Reported once, and only on a change: an unchanged record seen again on the next
check produces no entry, so nothing resurfaces an acknowledgement for a paper
that has not moved. Every entry describes a row that is already written, so a
caller acting on one is never ahead of the database.

Nothing here talks to a provider (both provider arguments are Protocols) and
nothing here imports the learning bank; the caller wires the two together.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

TRIGGERS: tuple[str, ...] = ("manual", "scheduled", "catch_up")
UPDATE_STATES: tuple[str, ...] = ("unread", "acknowledged", "dismissed")

# "Recently published" is a window on the calendar, nothing more.
RECENT_WINDOW_DAYS = 90

# Failures widen the gap rather than hammering a provider that is having a bad
# day. Capped at 2**4, so the worst case is sixteen intervals, not silence.
MAX_BACKOFF_DOUBLINGS = 4

SETTING_WEEKLY_ENABLED = "literature.weekly_enabled"
SETTING_INTERVAL_HOURS = "literature.interval_hours"
LOCK_KEY = "literature.lock"

# Opt-in. Weekly checking stays off until the owner turns it on: an unattended
# loop that reaches the network is not something to switch on for someone.
DEFAULT_WEEKLY_ENABLED = False
DEFAULT_INTERVAL_HOURS = 168.0
MIN_INTERVAL_HOURS = 1.0
MAX_INTERVAL_HOURS = 8760.0

# How many evidence-linked records one refresh pass may re-read. Bounded so a
# sweep costs a predictable number of provider calls no matter how big the
# library gets.
DEFAULT_REFRESH_LIMIT = 25
MAX_REFRESH_LIMIT = 100

# A copy of literature.pubmed's notice vocabulary. Storage must decide, from a
# stored row alone, whether a record is a notice rather than evidence -- and it
# writes rows without importing the provider package. A test pins the two
# copies together so they cannot drift.
NOTICE_PUBLICATION_TYPES = frozenset(
    {"retraction of publication", "published erratum", "expression of concern"}
)
NOTICE_REF_TYPES = frozenset(
    {
        "retractionof",
        "partialretractionof",
        "erratumfor",
        "expressionofconcernfor",
        "correctedandrepublishedfrom",
    }
)

# Why a row came back to unread. Shown on Today so a resurfaced record reads as
# "this changed" and never as a fresh find.
REASON_RETRACTED = "Resurfaced: this article has been retracted."
REASON_CORRECTED = (
    "Resurfaced: a correction or expression of concern was published for this."
)


class ArticleRow(Protocol):
    """What :func:`record_articles` needs: a row of literature_records columns.

    A Protocol rather than an import of ``literature.pubmed``: storage should
    not depend on the provider package to write a row.
    """

    def as_row(self) -> dict[str, Any]: ...


class RecordProvider(Protocol):
    """What :func:`refresh_records` needs: fetch named records by PMID.

    A Protocol again, for the same reason: storage names the capability, the
    caller supplies something that has it. In tests that is a fake with no
    socket behind it.
    """

    def fetch_by_pmid(self, pmids: list[str]) -> list[ArticleRow]: ...


@dataclass(frozen=True)
class Topic:
    id: str
    label: str
    query: str
    enabled: bool
    last_checked_at: str | None
    last_status: str
    last_failure: str
    consecutive_failures: int
    next_due_at: str | None
    created_at: str
    updated_at: str

    @property
    def backoff_multiplier(self) -> int:
        """What the failure streak is currently doing to the interval."""
        if self.consecutive_failures <= 0:
            return 1
        return 2 ** min(self.consecutive_failures, MAX_BACKOFF_DOUBLINGS)

    def as_dict(self) -> dict[str, Any]:
        # The whole readable status in one place: what happened last, why, how
        # many times in a row, when the next attempt is, and how far the
        # interval has been stretched.
        data = asdict(self)
        data["backoff_multiplier"] = self.backoff_multiplier
        return data


@dataclass(frozen=True)
class Check:
    id: str
    topic_id: str
    trigger: str
    status: str
    failure_category: str
    result_count: int
    new_count: int
    started_at: str
    finished_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Update:
    """One record as it appears on Today: the join row plus its citation."""

    id: str
    topic_id: str
    topic_label: str
    record_id: str
    state: str
    why_relevant: str
    first_seen_at: str
    checked_at: str | None
    pmid: str | None
    doi: str | None
    title: str
    journal: str
    abstract: str
    published_on: str | None
    publication_types: tuple[str, ...]
    retracted: bool
    corrected: bool
    # This record is the retraction/erratum/concern notice about some other
    # paper. Never supporting evidence for anything.
    is_notice: bool
    correction_notes: tuple[dict[str, str], ...]
    url: str
    priority: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["publication_types"] = list(self.publication_types)
        data["correction_notes"] = [dict(note) for note in self.correction_notes]
        return data


# -- topics -------------------------------------------------------------------


def _topic(row: sqlite3.Row) -> Topic:
    return Topic(
        id=row["id"],
        label=row["label"],
        query=row["query"],
        enabled=bool(row["enabled"]),
        last_checked_at=row["last_checked_at"],
        last_status=row["last_status"],
        last_failure=row["last_failure"],
        consecutive_failures=row["consecutive_failures"],
        next_due_at=row["next_due_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_topics(connection: sqlite3.Connection) -> list[Topic]:
    rows = connection.execute(
        "SELECT * FROM literature_topics ORDER BY created_at DESC"
    ).fetchall()
    return [_topic(row) for row in rows]


def get_topic(connection: sqlite3.Connection, topic_id: str) -> Topic:
    row = connection.execute(
        "SELECT * FROM literature_topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("literature topic", topic_id)
    return _topic(row)


def create_topic(connection: sqlite3.Connection, *, label: str, query: str) -> Topic:
    """A new topic is due immediately, so adding one is followed by a check."""
    now = utc_now()
    topic_id = new_id("ltp")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO literature_topics"
            " (id, label, query, enabled, next_due_at, created_at, updated_at)"
            " VALUES (?, ?, ?, 1, ?, ?, ?)",
            (topic_id, label, query, now, now, now),
        )
    return get_topic(connection, topic_id)


def update_topic(
    connection: sqlite3.Connection,
    topic_id: str,
    *,
    label: str | None = None,
    query: str | None = None,
    enabled: bool | None = None,
) -> Topic:
    current = get_topic(connection, topic_id)
    now = utc_now()
    next_enabled = current.enabled if enabled is None else bool(enabled)
    next_due = current.next_due_at
    if next_enabled and not current.enabled:
        # Re-enabling means "look now", not "wait out the old interval".
        next_due = now
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE literature_topics"
            " SET label = ?, query = ?, enabled = ?, next_due_at = ?, updated_at = ?"
            " WHERE id = ?",
            (
                current.label if label is None else label,
                current.query if query is None else query,
                1 if next_enabled else 0,
                next_due,
                now,
                topic_id,
            ),
        )
    return get_topic(connection, topic_id)


def delete_topic(connection: sqlite3.Connection, topic_id: str) -> None:
    with transaction(connection) as tx:
        cursor = tx.execute("DELETE FROM literature_topics WHERE id = ?", (topic_id,))
    if cursor.rowcount == 0:
        raise NotFoundError("literature topic", topic_id)


def due_topics(connection: sqlite3.Connection, *, now: str) -> list[Topic]:
    rows = connection.execute(
        "SELECT * FROM literature_topics"
        " WHERE enabled = 1 AND next_due_at IS NOT NULL AND next_due_at <= ?"
        " ORDER BY next_due_at ASC",
        (now,),
    ).fetchall()
    return [_topic(row) for row in rows]


# -- checks -------------------------------------------------------------------


def _check(row: sqlite3.Row) -> Check:
    return Check(
        id=row["id"],
        topic_id=row["topic_id"],
        trigger=row["trigger"],
        status=row["status"],
        failure_category=row["failure_category"],
        result_count=row["result_count"],
        new_count=row["new_count"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def get_check(connection: sqlite3.Connection, check_id: str) -> Check:
    row = connection.execute(
        "SELECT * FROM literature_checks WHERE id = ?", (check_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("literature check", check_id)
    return _check(row)


def list_checks(
    connection: sqlite3.Connection, topic_id: str, *, limit: int = 20
) -> list[Check]:
    rows = connection.execute(
        "SELECT * FROM literature_checks WHERE topic_id = ?"
        " ORDER BY started_at DESC, rowid DESC LIMIT ?",
        (topic_id, max(1, int(limit))),
    ).fetchall()
    return [_check(row) for row in rows]


def begin_check(connection: sqlite3.Connection, topic_id: str, trigger: str) -> Check:
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown check trigger {trigger!r}")
    get_topic(connection, topic_id)
    check_id = new_id("lck")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO literature_checks (id, topic_id, trigger, status, started_at)"
            " VALUES (?, ?, ?, 'running', ?)",
            (check_id, topic_id, trigger, now),
        )
    return get_check(connection, check_id)


def finish_check(
    connection: sqlite3.Connection,
    check_id: str,
    *,
    status: str,
    failure_category: str = "",
    result_count: int = 0,
    new_count: int = 0,
) -> Check:
    """Close a check and move the topic's clock -- differently for each outcome."""
    if status not in ("ok", "failed"):
        raise ValueError(f"a check finishes ok or failed, not {status!r}")
    check = get_check(connection, check_id)
    topic = get_topic(connection, check.topic_id)
    now = utc_now()
    interval = get_settings(connection)["interval_hours"]

    if status == "ok":
        failures = 0
        last_checked = now
        last_status = "ok"
        last_failure = ""
        next_due = _plus_hours(now, interval)
    else:
        failures = topic.consecutive_failures + 1
        # Not advanced. A failed check must never read as a fresh success.
        last_checked = topic.last_checked_at
        last_status = "failed"
        last_failure = failure_category
        next_due = _plus_hours(now, interval * (2 ** min(failures, MAX_BACKOFF_DOUBLINGS)))

    with transaction(connection) as tx:
        tx.execute(
            "UPDATE literature_checks SET status = ?, failure_category = ?,"
            " result_count = ?, new_count = ?, finished_at = ? WHERE id = ?",
            (
                status,
                failure_category if status == "failed" else "",
                int(result_count),
                int(new_count),
                now,
                check_id,
            ),
        )
        tx.execute(
            "UPDATE literature_topics SET last_checked_at = ?, last_status = ?,"
            " last_failure = ?, consecutive_failures = ?, next_due_at = ?,"
            " updated_at = ? WHERE id = ?",
            (
                last_checked,
                last_status,
                last_failure,
                failures,
                next_due,
                now,
                topic.id,
            ),
        )
    return get_check(connection, check_id)


# -- records ------------------------------------------------------------------


_RECORD_COLUMNS = (
    "pmid",
    "doi",
    "title",
    "journal",
    "abstract",
    "published_on",
    "provider_date",
    "publication_types",
    "retracted",
    "corrected",
    "correction_notes",
    "url",
    "priority",
)


def record_articles(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    check_id: str,
    articles: list[ArticleRow],
) -> dict[str, Any]:
    """Upsert records, link them to the topic, and count what happened.

    Deduplication is by PMID first and DOI second, matching the two partial
    unique indexes. The counts are four separate facts; ``new_to_library`` and
    ``recently_published`` in particular answer different questions and are
    never derived from each other.
    """
    get_topic(connection, topic_id)
    now = utc_now()
    today = _as_date(now[:10])
    counts = {
        "returned": len(articles),
        "new_to_library": 0,
        "already_known": 0,
        "recently_published": 0,
    }
    changed_status: list[dict[str, Any]] = []

    with transaction(connection) as tx:
        for article in articles:
            source = article.as_row()
            row = {key: source.get(key) for key in _RECORD_COLUMNS}
            pmid = row["pmid"] or None
            doi = row["doi"] or None
            if pmid is None and doi is None:
                # Nothing to deduplicate on: it would be a fresh row every check.
                continue
            existing = _find_record(tx, pmid=pmid, doi=doi)
            if existing is None:
                record_id = new_id("lrc")
                tx.execute(
                    "INSERT INTO literature_records"
                    " (id, pmid, doi, title, journal, abstract, published_on,"
                    "  provider_date, publication_types, retracted, corrected,"
                    "  correction_notes, url, priority, first_seen_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record_id,
                        pmid,
                        doi,
                        row["title"] or "",
                        row["journal"] or "",
                        row["abstract"] or "",
                        row["published_on"],
                        row["provider_date"],
                        row["publication_types"] or "[]",
                        int(row["retracted"] or 0),
                        int(row["corrected"] or 0),
                        row["correction_notes"] or "[]",
                        row["url"] or "",
                        row["priority"] or "other",
                        now,
                        now,
                    ),
                )
                counts["new_to_library"] += 1
                arrival = _notice_arrival(record_id, row)
                if arrival is not None:
                    changed_status.append(arrival)
            else:
                record_id = existing["id"]
                counts["already_known"] += 1
                # Sticky flags: a retraction that stops being reported is still
                # a retraction. Flipping 0 -> 1 is what the caller must hear about.
                retracted = int(existing["retracted"]) or int(row["retracted"] or 0)
                corrected = int(existing["corrected"]) or int(row["corrected"] or 0)
                change = _status_change(existing, row, retracted, corrected)
                if change is not None:
                    changed_status.append(change)
                tx.execute(
                    "UPDATE literature_records SET pmid = ?, doi = ?, title = ?,"
                    " journal = ?, abstract = ?, published_on = ?, provider_date = ?,"
                    " publication_types = ?, retracted = ?, corrected = ?,"
                    " correction_notes = ?, url = ?, priority = ?, updated_at = ?"
                    " WHERE id = ?",
                    (
                        _keep_identifier(tx, existing, "pmid", pmid),
                        _keep_identifier(tx, existing, "doi", doi),
                        row["title"] or existing["title"],
                        row["journal"] or existing["journal"],
                        row["abstract"] or existing["abstract"],
                        row["published_on"] or existing["published_on"],
                        row["provider_date"] or existing["provider_date"],
                        row["publication_types"] or existing["publication_types"],
                        retracted,
                        corrected,
                        row["correction_notes"] or existing["correction_notes"],
                        row["url"] or existing["url"],
                        row["priority"] or existing["priority"],
                        now,
                        record_id,
                    ),
                )

            if _is_recent(row["published_on"], today):
                counts["recently_published"] += 1

            # Unique per topic+record. An existing link keeps its state and its
            # first_seen_at: rediscovering an *unchanged* record is not a reason
            # to mark it unread again. Only a status change is, and that is done
            # below, deliberately and with a reason attached.
            tx.execute(
                "INSERT OR IGNORE INTO literature_topic_records"
                " (id, topic_id, record_id, check_id, state, first_seen_at, updated_at)"
                " VALUES (?, ?, ?, ?, 'unread', ?, ?)",
                (new_id("ltr"), topic_id, record_id, check_id, now, now),
            )

        # This topic's rows point at the check that just ran. A record another
        # topic also linked is resurfaced too -- the paper was withdrawn for
        # everyone -- and gets a check row of its own rather than borrowing
        # this topic's.
        others = _StatusChecks(tx, now)
        for change in changed_status:
            _resurface(
                tx,
                change,
                now=now,
                check_for_topic=lambda found: (
                    check_id if found == topic_id else others.for_topic(found)
                ),
            )

    counts["changed_status"] = changed_status
    return counts


# -- status changes -----------------------------------------------------------


def _status_change(
    existing: sqlite3.Row, row: dict[str, Any], retracted: int, corrected: int
) -> dict[str, Any] | None:
    """One entry for the caller, or ``None`` if nothing newly turned true.

    Newly, not currently: a record that was already retracted last week is not
    news this week, and reporting it again would resurface an acknowledgement
    on every single check.
    """
    newly_retracted = bool(retracted) and not existing["retracted"]
    newly_corrected = bool(corrected) and not existing["corrected"]
    notice = is_notice_row(
        row["publication_types"] or existing["publication_types"],
        row["correction_notes"] or existing["correction_notes"],
    )
    # A record only just recognised as a notice is news too: whatever cites it
    # is citing a retraction notice as if it were a finding.
    newly_notice = notice and not is_notice_row(
        existing["publication_types"], existing["correction_notes"]
    )
    if not newly_retracted and not newly_corrected and not newly_notice:
        return None
    if newly_retracted:
        # A retraction outranks a correction when both land at once: the caller
        # must not treat a withdrawn paper as merely corrected.
        newly = "retracted"
    elif newly_corrected:
        newly = "corrected"
    else:
        newly = "notice"
    return {
        "record_id": existing["id"],
        "retracted": bool(retracted),
        "corrected": bool(corrected),
        "is_notice": notice,
        "newly": newly,
    }


def _notice_arrival(record_id: str, row: dict[str, Any]) -> dict[str, Any] | None:
    """A record stored for the first time that is a notice, not a finding.

    Reported on arrival rather than on a flag flip, because a notice never
    flips one: it is born as the erratum or the retraction notice about some
    other paper, and the caller has to know that before anything can cite it.
    """
    if not is_notice_row(row["publication_types"], row["correction_notes"]):
        return None
    return {
        "record_id": record_id,
        "retracted": bool(int(row["retracted"] or 0)),
        "corrected": bool(int(row["corrected"] or 0)),
        "is_notice": True,
        "newly": "notice",
    }


def _resurface(
    connection: sqlite3.Connection,
    change: dict[str, Any],
    *,
    now: str,
    check_for_topic: Callable[[str], str | None],
) -> None:
    """Put a changed record back in front of the owner, with the reason.

    An acknowledged or dismissed row goes back to ``unread``: "I have read
    this" was said about a paper that has since been withdrawn or corrected,
    and that acknowledgement no longer covers it. ``why_relevant`` says which,
    so Today reads "this was retracted" and not "here is something new", and
    the row is repointed at the check that noticed, so the displayed timestamp
    is when we found out rather than when we first saw the paper.

    A ``notice`` entry resurfaces nothing: recognising the erratum itself is
    not a finding about the paper the owner acknowledged.
    """
    if change["newly"] == "notice":
        return
    reason = REASON_RETRACTED if change["newly"] == "retracted" else REASON_CORRECTED
    rows = connection.execute(
        "SELECT id, topic_id FROM literature_topic_records WHERE record_id = ?",
        (change["record_id"],),
    ).fetchall()
    for row in rows:
        connection.execute(
            "UPDATE literature_topic_records"
            " SET state = 'unread', why_relevant = ?,"
            "     check_id = COALESCE(?, check_id), updated_at = ?"
            " WHERE id = ?",
            (reason, check_for_topic(row["topic_id"]), now, row["id"]),
        )


# -- refreshing what is already cited -----------------------------------------


def refresh_records(
    connection: sqlite3.Connection,
    provider: RecordProvider,
    *,
    limit: int = DEFAULT_REFRESH_LIMIT,
) -> dict[str, Any]:
    """Re-read evidence-linked records and notice what changed about them.

    The gap this closes: a topic search is date-sorted top-N, so a paper linked
    as evidence two years ago never comes back in one, and its retraction is
    never seen. Selection here is therefore by *what is cited*, not by any date
    window -- every record with an ``evidence_links`` row and a PMID, oldest
    refreshed first so the queue rotates and nothing starves.

    Bounded on purpose: at most *limit* records per pass, which is at most a
    couple of provider calls. The provider is the same one the search uses, so
    the allowlist, throttle, retry and body-size limits are the same ones; none
    of them are restated here.
    """
    bounded = max(1, min(int(limit), MAX_REFRESH_LIMIT))
    candidates = connection.execute(
        "SELECT r.* FROM literature_records r"
        " WHERE r.pmid IS NOT NULL"
        "   AND EXISTS (SELECT 1 FROM evidence_links e WHERE e.record_id = r.id)"
        " ORDER BY r.updated_at ASC, r.rowid ASC LIMIT ?",
        (bounded,),
    ).fetchall()
    result: dict[str, Any] = {
        "considered": len(candidates),
        "refreshed": 0,
        "changed_status": [],
    }
    if not candidates:
        return result

    by_pmid = {row["pmid"]: row for row in candidates}
    # The provider call happens outside the transaction: a slow or failing
    # fetch must not hold a write lock on the database.
    articles = provider.fetch_by_pmid(list(by_pmid))

    now = utc_now()
    changed_status: list[dict[str, Any]] = []
    with transaction(connection) as tx:
        for article in articles:
            source = article.as_row()
            row = {key: source.get(key) for key in _RECORD_COLUMNS}
            existing = by_pmid.get(str(row["pmid"] or ""))
            if existing is None:
                # Not one of the records we asked about. Refresh writes nothing
                # new: discovering records is what a search is for.
                continue
            result["refreshed"] += 1
            retracted = int(existing["retracted"]) or int(row["retracted"] or 0)
            corrected = int(existing["corrected"]) or int(row["corrected"] or 0)
            change = _status_change(existing, row, retracted, corrected)
            if change is not None:
                changed_status.append(change)
            tx.execute(
                "UPDATE literature_records SET title = ?, journal = ?, abstract = ?,"
                " published_on = ?, provider_date = ?, publication_types = ?,"
                " retracted = ?, corrected = ?, correction_notes = ?, url = ?,"
                " priority = ?, updated_at = ? WHERE id = ?",
                (
                    row["title"] or existing["title"],
                    row["journal"] or existing["journal"],
                    row["abstract"] or existing["abstract"],
                    row["published_on"] or existing["published_on"],
                    row["provider_date"] or existing["provider_date"],
                    row["publication_types"] or existing["publication_types"],
                    retracted,
                    corrected,
                    row["correction_notes"] or existing["correction_notes"],
                    row["url"] or existing["url"],
                    row["priority"] or existing["priority"],
                    now,
                    existing["id"],
                ),
            )
        checks = _StatusChecks(tx, now)
        for change in changed_status:
            _resurface(tx, change, now=now, check_for_topic=checks.for_topic)

    result["changed_status"] = changed_status
    return result


class _StatusChecks:
    """One check row per topic whose rows a status change resurfaced.

    We really did ask the provider, so a resurfaced row points at a real check
    rather than at a two-year-old one and shows when we found out. The row is
    written directly instead of through :func:`finish_check` because this is
    not the topic's weekly search and must not move its due clock or its
    ``last_checked_at``. Created lazily, so a pass that changes nothing writes
    no rows at all.
    """

    def __init__(self, connection: sqlite3.Connection, now: str) -> None:
        self._connection = connection
        self._now = now
        self._by_topic: dict[str, str] = {}

    def for_topic(self, topic_id: str) -> str:
        existing = self._by_topic.get(topic_id)
        if existing is not None:
            return existing
        check_id = new_id("lck")
        self._connection.execute(
            "INSERT INTO literature_checks"
            " (id, topic_id, trigger, status, result_count, new_count,"
            "  started_at, finished_at)"
            " VALUES (?, ?, 'scheduled', 'ok', 1, 0, ?, ?)",
            (check_id, topic_id, self._now, self._now),
        )
        self._by_topic[topic_id] = check_id
        return check_id


def is_notice_row(publication_types: str | None, correction_notes: str | None) -> bool:
    """Is this stored row a notice about another paper, rather than evidence?

    Read back out of the two JSON columns the provider wrote. Direction is the
    whole question: ``RetractionIn`` happened to this article, ``RetractionOf``
    is this article being the retraction of another one.
    """
    types = {
        str(value).strip().lower()
        for value in _json_list(publication_types)
        if isinstance(value, str)
    }
    if types & NOTICE_PUBLICATION_TYPES:
        return True
    for note in _json_list(correction_notes):
        if not isinstance(note, dict):
            continue
        ref = str(note.get("ref_type", "")).strip().lower().replace(" ", "")
        if ref in NOTICE_REF_TYPES:
            return True
    return False


def _find_record(
    connection: sqlite3.Connection, *, pmid: str | None, doi: str | None
) -> sqlite3.Row | None:
    if pmid:
        row = connection.execute(
            "SELECT * FROM literature_records WHERE pmid = ?", (pmid,)
        ).fetchone()
        if row is not None:
            return row
    if doi:
        return connection.execute(
            "SELECT * FROM literature_records WHERE doi = ?", (doi,)
        ).fetchone()
    return None


def _keep_identifier(
    connection: sqlite3.Connection, existing: sqlite3.Row, column: str, value: str | None
) -> str | None:
    """Fill in a missing identifier; never overwrite or duplicate one."""
    current = existing[column]
    if current:
        return current
    if not value:
        return current
    # `column` is one of two literals below, never caller input.
    statement = {
        "pmid": "SELECT id FROM literature_records WHERE pmid = ? AND id != ?",
        "doi": "SELECT id FROM literature_records WHERE doi = ? AND id != ?",
    }[column]
    clash = connection.execute(statement, (value, existing["id"])).fetchone()
    return current if clash is not None else value


def _is_recent(published_on: str | None, today: date | None) -> bool:
    published = _as_date(published_on)
    if published is None or today is None:
        return False
    return published >= today - timedelta(days=RECENT_WINDOW_DAYS)


def _as_date(value: str | None) -> date | None:
    """YYYY, YYYY-MM and YYYY-MM-DD all resolve; anything else is unknown."""
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2][:2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except (ValueError, IndexError):
        return None


# -- Today --------------------------------------------------------------------


_UPDATE_SELECT = """
SELECT tr.id AS id, tr.topic_id AS topic_id, t.label AS topic_label,
       tr.record_id AS record_id, tr.state AS state, tr.why_relevant AS why_relevant,
       tr.first_seen_at AS first_seen_at, c.started_at AS checked_at,
       r.pmid AS pmid, r.doi AS doi, r.title AS title, r.journal AS journal,
       r.abstract AS abstract, r.published_on AS published_on,
       r.publication_types AS publication_types, r.retracted AS retracted,
       r.corrected AS corrected, r.correction_notes AS correction_notes,
       r.url AS url, r.priority AS priority
FROM literature_topic_records tr
JOIN literature_topics t ON t.id = tr.topic_id
JOIN literature_records r ON r.id = tr.record_id
LEFT JOIN literature_checks c ON c.id = tr.check_id
"""


def _update(row: sqlite3.Row) -> Update:
    return Update(
        id=row["id"],
        topic_id=row["topic_id"],
        topic_label=row["topic_label"],
        record_id=row["record_id"],
        state=row["state"],
        why_relevant=row["why_relevant"],
        first_seen_at=row["first_seen_at"],
        checked_at=row["checked_at"],
        pmid=row["pmid"],
        doi=row["doi"],
        title=row["title"],
        journal=row["journal"],
        abstract=row["abstract"],
        published_on=row["published_on"],
        publication_types=tuple(_json_list(row["publication_types"])),
        retracted=bool(row["retracted"]),
        corrected=bool(row["corrected"]),
        is_notice=is_notice_row(row["publication_types"], row["correction_notes"]),
        correction_notes=tuple(_json_list(row["correction_notes"])),
        url=row["url"],
        priority=row["priority"],
    )


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def list_updates(
    connection: sqlite3.Connection, *, state: str | None = None, limit: int = 50
) -> list[Update]:
    bounded = max(1, min(int(limit), 500))
    if state is None:
        rows = connection.execute(
            _UPDATE_SELECT + " ORDER BY tr.first_seen_at DESC, tr.rowid DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    else:
        if state not in UPDATE_STATES:
            raise ValueError(f"unknown update state {state!r}")
        rows = connection.execute(
            _UPDATE_SELECT
            + " WHERE tr.state = ? ORDER BY tr.first_seen_at DESC, tr.rowid DESC LIMIT ?",
            (state, bounded),
        ).fetchall()
    return [_update(row) for row in rows]


def get_update(connection: sqlite3.Connection, topic_record_id: str) -> Update:
    row = connection.execute(
        _UPDATE_SELECT + " WHERE tr.id = ?", (topic_record_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("literature update", topic_record_id)
    return _update(row)


def set_update_state(
    connection: sqlite3.Connection, topic_record_id: str, state: str
) -> Update:
    if state not in UPDATE_STATES:
        raise ValueError(f"unknown update state {state!r}")
    with transaction(connection) as tx:
        cursor = tx.execute(
            "UPDATE literature_topic_records SET state = ?, updated_at = ? WHERE id = ?",
            (state, utc_now(), topic_record_id),
        )
    if cursor.rowcount == 0:
        raise NotFoundError("literature update", topic_record_id)
    return get_update(connection, topic_record_id)


def unread_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS n FROM literature_topic_records WHERE state = 'unread'"
    ).fetchone()
    return int(row["n"])


# -- settings and the scheduler lock ------------------------------------------


SETTING_PREFERRED_JOURNALS = "literature.preferred_journals"
SETTING_GUIDELINES_FIRST = "literature.guidelines_first"
# PubMed journal-title abbreviations ([ta]). The owner can change the list;
# this is the starting point they asked for.
DEFAULT_PREFERRED_JOURNALS: tuple[str, ...] = ("N Engl J Med", "JAMA", "Nature", "Lancet")
MAX_PREFERRED_JOURNALS = 12


def clean_journals(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        text = " ".join(str(value).replace('"', "").split())[:60]
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned[:MAX_PREFERRED_JOURNALS]


def get_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = _state(connection, SETTING_WEEKLY_ENABLED)
    weekly = DEFAULT_WEEKLY_ENABLED if stored is None else stored == "1"
    raw = _state(connection, SETTING_INTERVAL_HOURS)
    try:
        interval = DEFAULT_INTERVAL_HOURS if raw is None else float(raw)
    except ValueError:
        interval = DEFAULT_INTERVAL_HOURS
    journals_raw = _state(connection, SETTING_PREFERRED_JOURNALS)
    try:
        journals = clean_journals(list(json.loads(journals_raw))) if journals_raw is not None else list(DEFAULT_PREFERRED_JOURNALS)
    except (ValueError, TypeError):
        journals = list(DEFAULT_PREFERRED_JOURNALS)
    guidelines = _state(connection, SETTING_GUIDELINES_FIRST)
    return {
        "weekly_enabled": weekly,
        "interval_hours": _clamp_interval(interval),
        "preferred_journals": journals,
        "guidelines_first": True if guidelines is None else guidelines == "1",
    }


def set_settings(
    connection: sqlite3.Connection,
    *,
    weekly_enabled: bool,
    interval_hours: float,
    preferred_journals: list[str] | None = None,
    guidelines_first: bool | None = None,
) -> dict[str, Any]:
    with transaction(connection) as tx:
        _write_state(tx, SETTING_WEEKLY_ENABLED, "1" if weekly_enabled else "0")
        _write_state(tx, SETTING_INTERVAL_HOURS, str(_clamp_interval(interval_hours)))
        if preferred_journals is not None:
            _write_state(tx, SETTING_PREFERRED_JOURNALS, json.dumps(clean_journals(preferred_journals)))
        if guidelines_first is not None:
            _write_state(tx, SETTING_GUIDELINES_FIRST, "1" if guidelines_first else "0")
    return get_settings(connection)


def seed_settings(
    connection: sqlite3.Connection, *, interval_hours: float
) -> dict[str, Any]:
    """Write the configured interval once, without overriding a stored choice.

    There is deliberately no ``weekly_enabled`` argument. Configuration may say
    how often to check; only the owner may say *whether* to, so seeding writes
    the off default and nothing in this module can turn the sweep on by itself.
    :func:`set_settings` is the one way, and it is reached from an explicit
    request.
    """
    with transaction(connection) as tx:
        for key, value in (
            (SETTING_WEEKLY_ENABLED, "1" if DEFAULT_WEEKLY_ENABLED else "0"),
            (SETTING_INTERVAL_HOURS, str(_clamp_interval(interval_hours))),
        ):
            tx.execute(
                "INSERT OR IGNORE INTO app_state (key, value, updated_at)"
                " VALUES (?, ?, ?)",
                (key, value, utc_now()),
            )
    return get_settings(connection)


def acquire_lock(
    connection: sqlite3.Connection, *, holder: str, ttl_seconds: float, now: str
) -> bool:
    """Take the watcher lock, or report that someone else holds a live one.

    One statement, so two connections racing produce exactly one winner. The
    lock expires: a process that dies mid-check cannot wedge the watcher, it
    only delays it by the TTL. Re-acquiring as the same holder refreshes rather
    than fails, which is what makes a retried startup harmless.
    """
    expires_at = _plus_seconds(now, ttl_seconds)
    value = json.dumps({"holder": holder, "expires_at": expires_at})
    with transaction(connection) as tx:
        cursor = tx.execute(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at"
            " WHERE json_extract(app_state.value, '$.expires_at') <= ?"
            "    OR json_extract(app_state.value, '$.holder') = ?",
            (LOCK_KEY, value, now, now, holder),
        )
    return cursor.rowcount > 0


def watch_status(connection: sqlite3.Connection, *, now: str | None = None) -> dict[str, Any]:
    """Everything an interface needs to say what the watcher is doing.

    Whether the sweep is on, how often, whether a run holds the lock right now
    and until when, and per topic: last status, last failure category, the
    failure streak, the next attempt and how far the backoff has stretched the
    interval. Read-only, so it is safe to call from anywhere.
    """
    moment = now or utc_now()
    settings = get_settings(connection)
    raw = _state(connection, LOCK_KEY)
    holder = ""
    expires_at = ""
    if raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            holder = str(parsed.get("holder") or "")
            expires_at = str(parsed.get("expires_at") or "")
    return {
        **settings,
        # An expired lock is not held: a crashed run costs one TTL, not forever.
        "locked": bool(expires_at) and expires_at > moment,
        "lock_holder": holder,
        "lock_expires_at": expires_at,
        "topics": [topic.as_dict() for topic in list_topics(connection)],
    }


def release_lock(connection: sqlite3.Connection, holder: str) -> None:
    """Only the holder may release it, so a late finisher cannot free a new run."""
    with transaction(connection) as tx:
        tx.execute(
            "DELETE FROM app_state WHERE key = ?"
            " AND json_extract(value, '$.holder') = ?",
            (LOCK_KEY, holder),
        )


def _state(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute(
        "SELECT value FROM app_state WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else row["value"]


def _write_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (key, value, utc_now()),
    )


def _clamp_interval(hours: float) -> float:
    return float(min(max(float(hours), MIN_INTERVAL_HOURS), MAX_INTERVAL_HOURS))


def _parse_timestamp(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format(moment: datetime) -> str:
    return (
        moment.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _plus_hours(value: str, hours: float) -> str:
    return _format(_parse_timestamp(value) + timedelta(hours=hours))


def _plus_seconds(value: str, seconds: float) -> str:
    return _format(_parse_timestamp(value) + timedelta(seconds=seconds))
