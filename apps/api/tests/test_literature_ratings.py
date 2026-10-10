"""Thumbs on papers, and what Today shows next (feedback of 10 October)."""

from __future__ import annotations

from datetime import datetime, timezone

from vademecum.storage import literature as store


def _paper(connection, topic_id: str, pmid: str, journal: str, seen: str) -> str:
    record_id = f"lrc_{pmid}"
    connection.execute(
        "INSERT INTO literature_records (id, pmid, title, journal, first_seen_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (record_id, pmid, f"Paper {pmid}", journal, seen, seen),
    )
    connection.execute(
        "INSERT INTO literature_topic_records (id, topic_id, record_id, check_id, state, first_seen_at, updated_at)"
        " VALUES (?, ?, ?, NULL, 'unread', ?, ?)",
        (f"ltr_{pmid}", topic_id, record_id, seen, seen),
    )
    connection.commit()
    return f"ltr_{pmid}"


def test_thumbs_steer_the_order_and_a_paper_turned_down_is_not_shown(connection) -> None:
    topic = store.create_topic(connection, label="Sepsis", query="sepsis")
    now = datetime(2026, 10, 10, tzinfo=timezone.utc)
    liked = _paper(connection, topic.id, "1", "Lancet", "2026-10-01T00:00:00Z")
    _paper(connection, topic.id, "2", "Obscure Journal", "2026-10-09T00:00:00Z")
    other_lancet = _paper(connection, topic.id, "3", "Lancet", "2026-10-02T00:00:00Z")
    refused = _paper(connection, topic.id, "4", "Obscure Journal", "2026-10-09T12:00:00Z")
    store.set_rating(connection, store.get_update(connection, liked).record_id, 1)
    store.set_rating(connection, store.get_update(connection, refused).record_id, -1)
    order = [update.id for update in store.ranked_updates(connection, limit=10, now=now)]
    assert refused not in order
    assert order.index(other_lancet) < order.index("ltr_2"), "a liked journal outranks a newer paper elsewhere"
    assert store.ranked_updates(connection, limit=1, exclude=[order[0]], now=now)[0].id == order[1], "Next article"
    store.set_rating(connection, store.get_update(connection, refused).record_id, 0)
    assert refused in [update.id for update in store.ranked_updates(connection, limit=10, now=now)]


def test_a_check_that_finds_nothing_new_reads_further_back_next_time(connection) -> None:
    topic = store.create_topic(connection, label="Sepsis", query="sepsis")
    assert store.search_depth(connection, topic.id) == 0
    store.note_search_depth(connection, topic.id, found_new=0, step=25)
    store.note_search_depth(connection, topic.id, found_new=0, step=25)
    assert store.search_depth(connection, topic.id) == 50
    store.note_search_depth(connection, topic.id, found_new=3, step=25)
    assert store.search_depth(connection, topic.id) == 0


def test_journals_liked_twice_are_offered_but_not_added(connection) -> None:
    topic = store.create_topic(connection, label="Sepsis", query="sepsis")
    for pmid in ("11", "12"):
        update = _paper(connection, topic.id, pmid, "BMJ", "2026-10-09T00:00:00Z")
        store.set_rating(connection, store.get_update(connection, update).record_id, 1)
    assert store.suggested_journals(connection) == ["BMJ"]
    assert "BMJ" not in store.get_settings(connection)["preferred_journals"]
