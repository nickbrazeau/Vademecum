"""Two Vademecums that sync (ADR 0015): domi keeps the files and builds; foris
is where a phone works; a round trip loses nothing and bounces nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from test_end_to_end import ABSTRACT, ASSESSMENT, EVIDENCE, GRADE, LECTURE, SYNTHESIS, build, upload
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.db import connect
from vademecum.storage import sync as sync_store
from vademecum.sync import Peer, SyncError, sync_once

TOKEN = "a-long-shared-secret-for-the-pair"
HEADERS = {"X-Vademecum-Sync": TOKEN}


class ClientTransport:
    """The peer, in-process: the test client stands in for HTTPS."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def request(self, method: str, path: str, *, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        response = self.client.request(method, path, headers=headers, content=body)
        return response.status_code, response.content


def node(tmp_path: Path, name: str, **overrides):
    turns = ScriptedTurns(synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT])
    turns.grading = [GRADE]
    provider = FakeProvider(articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)])
    settings = Settings(data_dir=tmp_path / name, host="127.0.0.1", port=8765, sources_folder_enabled=False, **overrides)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    client = TestClient(app, base_url=LOCAL_ORIGIN)
    client.__enter__()
    app.state.turn_factory = turns
    app.state.build_service._turn_factory = turns
    return client, app


@pytest.fixture()
def pair(tmp_path: Path):
    home, home_app = node(tmp_path, "home", sync_role="domi")
    away, away_app = node(tmp_path, "away", sync_role="foris", sync_accept_token=TOKEN)
    try:
        yield home, home_app, away, away_app
    finally:
        home.__exit__(None, None, None)
        away.__exit__(None, None, None)


def db(app):
    return connect(app.state.database_path)


def run_sync(home_app, away: TestClient, scope: str = "full", after_pull=None) -> dict:
    connection = db(home_app)
    try:
        return sync_once(
            connection,
            peer=Peer(ClientTransport(away), TOKEN),
            source_dir=home_app.state.source_dir,
            role="domi",
            scope=scope,
            after_pull=after_pull,
        )
    finally:
        connection.close()


def test_the_routes_are_invisible_without_the_token(pair) -> None:
    home, _, away, _ = pair
    assert away.get("/api/sync/status").status_code == 404
    assert away.get("/api/sync/status", headers={"X-Vademecum-Sync": "wrong"}).status_code == 404
    assert away.get("/api/sync/status", headers=HEADERS).status_code == 200
    # Domi accepts no token at all: nothing is configured there.
    assert home.get("/api/sync/status", headers=HEADERS).status_code == 404


def test_every_write_is_logged_and_nothing_applied_is_logged_again(pair) -> None:
    home, home_app, _, _ = pair
    pile = home.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    home.patch(f"/api/piles/{pile['id']}", json={"title": "Sepsis and shock"})
    connection = db(home_app)
    changes, through, done = sync_store.changes_since(connection, 0)
    assert done and through >= 2
    piles = [(c.op, c.row["title"]) for c in changes if c.table == "piles"]
    assert piles == [("upsert", "Sepsis and shock")], "ten edits to one row travel as one change"
    # Applying a peer's change writes the row without logging it.
    before = sync_store.state(connection)["log_length"]
    incoming = sync_store.Change(99, "knowledge_gap_flags", ["flg_x"], "upsert", {
        "id": "flg_x", "text": "Unsure about vasopressors", "topic": None, "pile_id": None,
        "status": "open", "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z", "addressed_at": None,
    })
    result = sync_store.apply_changes(connection, [incoming], role="domi")
    assert result.applied == 1 and result.through == 99
    assert sync_store.state(connection)["log_length"] == before
    assert home.get("/api/flags").json()[0]["text"] == "Unsure about vasopressors"
    connection.close()


def test_a_round_trip_carries_the_bank_out_and_the_work_back(pair, tmp_path: Path) -> None:
    home, home_app, away, away_app = pair

    # Domi: a pile, a source, a build.
    pile = home.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(home, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    build(home, pile["id"])
    assert home.get(f"/api/piles/{pile['id']}/build/status").json()["run"]["status"] == "succeeded"

    first = run_sync(home_app, away)
    # Foris's own start-up settings rows come across too; nothing else is there yet.
    assert first["pushed"] > 0 and first["deferred"] == 0

    # Foris now has the pile, the source and its text, the point and the question.
    assert [p["title"] for p in away.get("/api/piles").json()] == ["Sepsis"]
    source = away.get(f"/api/piles/{pile['id']}/sources").json()[0]
    assert source["display_name"] == "lecture.txt" and source["status"] == "extracted"
    assert "lactate" in away.get(f"/api/sources/{source['id']}").json()["segments_preview"][0]["text"].lower()
    assert len(away.get("/api/points").json()) == 1
    assert (tmp_path / "away" / "attachments" / "sources").glob("*.txt")
    # And the original file came across by name, verified by digest.
    assert next((tmp_path / "away" / "attachments" / "sources").glob("*.txt")).read_bytes() == LECTURE.encode()

    # Foris: the phone works. A Tutor answer, self-assessed; a flag; a pile note.
    question = away.get("/api/tutor/next").json()["question"]
    away.post("/api/tutor/reveal", json={"question_id": question["id"]})
    away.post("/api/tutor/self-assess", json={"question_id": question["id"], "answer": "Serial lactate.", "outcome": "correct"})
    away.post("/api/flags", json={"text": "Unsure about vasopressor choice"})
    away.patch(f"/api/piles/{pile['id']}", json={"description": "Read on the train"})

    second = run_sync(home_app, away)
    assert second["pulled"] > 0 and second["applied"] > 0

    # Domi has the attempt, the flag and the note; nothing was duplicated.
    history = home.get("/api/tutor/history").json()
    assert len(history) == 1 and history[0]["outcome"] == "self_assessed"
    assert [f["text"] for f in home.get("/api/flags").json()] == ["Unsure about vasopressor choice"]
    assert home.get(f"/api/piles/{pile['id']}").json()["description"] == "Read on the train"

    # A third round moves nothing: no echo in either direction.
    third = run_sync(home_app, away)
    assert third == {"pulled": 0, "applied": 0, "skipped": 0, "deferred": 0, "pushed": 0}

    # A removal on one side is carried to the other.
    flag = home.get("/api/flags").json()[0]
    assert home.delete(f"/api/flags/{flag['id']}").status_code == 204
    run_sync(home_app, away)
    assert away.get("/api/flags").json() == []


def test_domi_owned_rows_are_not_overwritten_from_foris(pair) -> None:
    home, home_app, away, away_app = pair
    pile = home.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(home, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    run_sync(home_app, away)
    source = away.get(f"/api/piles/{pile['id']}/sources").json()[0]
    # Foris marks the source excluded; that is domi's table, so it keeps its own row.
    assert away.patch(f"/api/sources/{source['id']}", json={"excluded": True}).status_code == 200
    result = run_sync(home_app, away)
    assert result["skipped"] >= 1
    assert home.get(f"/api/sources/{source['id']}").json()["excluded"] is False
    # But a source foris created (a note from the phone) is new to domi and is taken.
    away.post(f"/api/piles/{pile['id']}/sources", files=[("files", ("note.txt", b"A note typed on the phone about lactate.", "text/plain"))], data={"confidence": "low"})
    run_sync(home_app, away)
    names = sorted(s["display_name"] for s in home.get(f"/api/piles/{pile['id']}/sources").json())
    assert names == ["lecture.txt", "note.txt"]


def test_a_peer_that_is_this_node_or_unreachable_is_refused(pair) -> None:
    home, home_app, away, _ = pair

    class Dead:
        def request(self, method, path, *, headers, body):
            return 503, b""

    connection = db(home_app)
    with pytest.raises(SyncError):
        sync_once(connection, peer=Peer(Dead(), TOKEN), source_dir=home_app.state.source_dir, role="domi")
    connection.close()


def test_the_change_payload_names_no_path(pair) -> None:
    home, home_app, away, _ = pair
    pile = home.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(home, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    connection = db(home_app)
    changes, _, _ = sync_store.changes_since(connection, 0)
    text = json.dumps([c.as_dict() for c in changes])
    assert "/Users/" not in text and str(home_app.state.data_dir) not in text
    connection.close()


def test_the_lean_scope_carries_records_and_cited_passages_but_no_files(pair, tmp_path: Path) -> None:
    """ADR 0017: foris stays small however large the Mac's library grows."""
    home, home_app, away, away_app = pair
    pile = home.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(home, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    # A second source nobody cites: its text must not travel.
    assert upload(home, pile["id"], "appendix.txt", b"An appendix about ward logistics that no learning point cites.\n").status_code == 201
    build(home, pile["id"])
    assert home.get(f"/api/piles/{pile['id']}/build/status").json()["run"]["status"] == "succeeded"

    result = run_sync(home_app, away, scope="lean")
    assert result["pushed"] > 0 and result["deferred"] == 0

    # Rows, yes. Files, no.
    sources = {s["display_name"]: s for s in away.get(f"/api/piles/{pile['id']}/sources").json()}
    assert set(sources) == {"lecture.txt", "appendix.txt"}
    assert list((tmp_path / "away" / "attachments" / "sources").glob("*")) == []
    assert len(away.get("/api/points").json()) == 1

    # Only the cited passage came across; the appendix's text did not.
    lecture = away.get(f"/api/sources/{sources['lecture.txt']['id']}").json()
    assert lecture["segments_preview"] and "lactate" in lecture["segments_preview"][0]["text"].lower()
    appendix = away.get(f"/api/sources/{sources['appendix.txt']['id']}").json()
    assert appendix["segments_preview"] == []

    # The Tutor works on foris from what came across.
    question = away.get("/api/tutor/next").json()["question"]
    assert question is not None
    # And a second round moves nothing.
    assert run_sync(home_app, away, scope="lean")["pushed"] == 0


def test_a_foris_restored_from_an_older_copy_of_itself_is_sent_what_it_lost(pair) -> None:
    """ADR 0026: the cloud copy snapshots every few minutes; a restart between snapshots
    loses rows it had acknowledged. It records what it holds in its own database, so the
    restored copy says it holds less, and domi sends the rest again."""
    import shutil

    home, home_app, away, away_app = pair
    first = home.post("/api/flags", json={"text": "A flag before the snapshot", "topic": "Sepsis"}).json()
    assert run_sync(home_app, away, scope="lean")["pushed"] > 0
    snapshot = away_app.state.database_path.with_suffix(".snapshot")
    away_db = db(away_app)
    try:
        away_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        away_db.close()
    shutil.copyfile(away_app.state.database_path, snapshot)

    page = home.post("/api/piles", json={"title": "After the snapshot", "tier": "high"}).json()
    assert run_sync(home_app, away, scope="lean")["pushed"] > 0
    assert any(p["id"] == page["id"] for p in away.get("/api/piles").json())

    # The cloud copy dies before its next snapshot and comes back from the older one.
    away_db = db(away_app)
    try:
        restored = connect(snapshot)
        restored.backup(away_db)
        restored.close()
    finally:
        away_db.close()
    assert not any(p["id"] == page["id"] for p in away.get("/api/piles").json()), "lost with the restart"

    run_sync(home_app, away, scope="lean")
    assert any(p["id"] == page["id"] for p in away.get("/api/piles").json()), "sent again"
    assert any(f["id"] == first["id"] for f in away.get("/api/flags").json())



def test_the_same_paper_under_two_ids_is_skipped_not_fatal(pair) -> None:
    """ADR 0026: both nodes may fetch one paper from PubMed; the copy that arrives second
    under another id duplicates a unique DOI and is skipped, and the rest of the batch applies."""
    home, home_app, away, away_app = pair
    now = "2026-10-04T00:00:00Z"
    for app, record_id in ((home_app, "rec_home"), (away_app, "rec_away")):
        connection = db(app)
        try:
            connection.execute(
                "INSERT INTO literature_records (id, pmid, doi, title, journal, abstract, publication_types, correction_notes, url, priority, first_seen_at, updated_at)"
                " VALUES (?, '123', '10.1/x', 'Same paper', 'J', '', '[]', '[]', '', 'other', ?, ?)",
                (record_id, now, now),
            )
            connection.commit()
        finally:
            connection.close()
    away.post("/api/flags", json={"text": "A flag made on the phone", "topic": "Sepsis"})
    connection = db(away_app)
    try:
        # Foris's own bookkeeping on its copy of the paper: a topic it watches, the paper filed under it.
        connection.execute("INSERT INTO literature_topics (id, label, query, enabled, created_at, updated_at) VALUES ('top_a', 'Sepsis', 'sepsis', 1, ?, ?)", (now, now))
        connection.execute(
            "INSERT INTO literature_topic_records (id, topic_id, record_id, state, first_seen_at, updated_at) VALUES ('ltr_a', 'top_a', 'rec_away', 'unread', ?, ?)",
            (now, now),
        )
        connection.commit()
    finally:
        connection.close()
    result = run_sync(home_app, away, scope="lean")
    assert result["pulled"] > 0
    assert any(f["text"] == "A flag made on the phone" for f in home.get("/api/flags").json()), "the rest of the batch arrived"
    # And the other way: foris's copy gives way to domi's, which is the owner's.
    connection = db(away_app)
    try:
        ids = [row[0] for row in connection.execute("SELECT id FROM literature_records WHERE doi = '10.1/x'").fetchall()]
    finally:
        connection.close()
    assert ids == ["rec_home"]


def test_podcast_audio_goes_to_the_cloud_copy_and_is_retired_once_heard(pair) -> None:
    """ADR 0027: the Mac sends the newest ten unheard episodes' audio; the cloud copy
    plays it; listening on the phone deletes it there and, at the next sync, on the Mac,
    where the episode goes back to its script."""
    from vademecum.storage import podcasts
    from vademecum.sync import push_podcast_audio

    home, home_app, away, away_app = pair
    home_dir = podcasts.podcasts_dir(home_app.state.source_dir)
    home_dir.mkdir(parents=True, exist_ok=True)
    connection = db(home_app)
    try:
        ids = []
        for index in range(12):
            episode = podcasts.create_episode(connection, title=f"Episode {index}", entry_ids=[])
            connection.execute("UPDATE podcast_episodes SET created_at = ? WHERE id = ?", (f"2026-10-{index + 1:02d}T00:00:00Z", episode.id))
            connection.commit()
            (home_dir / f"{episode.id}.m4a").write_bytes(f"audio {index}".encode() * 100)
            podcasts.set_rendered(connection, episode.id, audio_name=f"{episode.id}.m4a", audio_bytes=900, duration_seconds=60, voices={})
            ids.append(episode.id)
    finally:
        connection.close()
    newest_first = list(reversed(ids))

    run_sync(home_app, away, scope="lean")
    connection = db(home_app)
    try:
        assert push_podcast_audio(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 10
        assert push_podcast_audio(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 0, "only what is missing"
    finally:
        connection.close()
    listed = {e["id"]: e for e in away.get("/api/podcasts").json()["episodes"]}
    assert [listed[i]["has_audio"] for i in newest_first] == [True] * 10 + [False] * 2
    played = away.get(f"/api/podcasts/{newest_first[0]}/audio")
    assert played.status_code == 200 and played.content == b"audio 11" * 100

    # Heard on the phone: deleted there at once, and on the Mac at the next sync.
    heard = away.post(f"/api/podcasts/{newest_first[0]}/listened", json={"listened": True}).json()
    assert heard["has_audio"] is False and heard["audio_elsewhere"] is False and heard["script"] == []
    assert away.get(f"/api/podcasts/{newest_first[0]}/audio").status_code == 409
    run_sync(home_app, away, scope="lean")
    connection = db(home_app)
    try:
        podcasts.retire_audio(connection, home_dir, role="domi")
        mac = podcasts.get_episode(connection, newest_first[0])
        assert mac.audio_name == "" and mac.status == "scripted" and not (home_dir / f"{newest_first[0]}.m4a").exists()
        # The next newest unheard one moves up into the five.
        assert push_podcast_audio(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 1
    finally:
        connection.close()
    held = {item["episode_id"] for item in away.get("/api/sync/podcast-audio", headers={"X-Vademecum-Sync": TOKEN}).json()["held"]}
    assert held == set(newest_first[1:11])
    # Rendered again in other voices: the same name, a new file, sent again.
    (home_dir / f"{newest_first[1]}.m4a").write_bytes(b"new voices" * 500)
    connection = db(home_app)
    try:
        assert push_podcast_audio(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 1
    finally:
        connection.close()
    assert away.get(f"/api/podcasts/{newest_first[1]}/audio").content == b"new voices" * 500


def test_page_figures_reach_the_phones_copy_and_leave_when_unused(pair) -> None:
    """Feedback of 6 October: figures were broken images on the phone. The Mac sends the
    pictures its pages place, the cloud copy serves them, and drops them when no page does."""
    from test_images_and_schematics import pdf_with_picture
    from vademecum.storage import encyclopedia as pages
    from vademecum.sync import push_figures

    home, home_app, away, away_app = pair
    pile = home.post("/api/piles", json={"title": "Figures", "tier": "high"}).json()
    uploaded = home.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("figure.pdf", pdf_with_picture("A labelled figure, in words."), "application/pdf"))],
        data={"confidence": "high"},
    )
    source_id = uploaded.json()["results"][0]["source"]["id"]
    image = home.get(f"/api/sources/{source_id}/images").json()[0]
    connection = db(home_app)
    try:
        entry = pages.upsert_entry(
            connection, topic="figures", title="Figures", specialty_id=None, summary="S.",
            sections=[{"heading": "H", "paragraphs": [{"text": "See the figure.", "point_ids": [], "figures": [{"image_id": image["id"], "source": "figure.pdf", "locator": "page 1", "width": 120, "height": 120}]}]}],
            point_ids=[], points_hash_value="1",
        )
    finally:
        connection.close()
    run_sync(home_app, away, scope="lean")
    assert away.get(f"/api/images/{image['id']}").status_code == 404, "lean: no pictures until sent"
    connection = db(home_app)
    try:
        assert push_figures(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 1
        assert push_figures(connection, Peer(ClientTransport(away), TOKEN), home_app.state.source_dir) == 0, "only what is missing"
    finally:
        connection.close()
    served = away.get(f"/api/images/{image['id']}")
    assert served.status_code == 200 and served.headers["content-type"].startswith("image/")
    refused = away.put("/api/sync/figures/img_notplaced1?ext=png", content=b"x", headers={"X-Vademecum-Sync": TOKEN})
    assert refused.json()["stored"] is False, "only a picture a page places"
    # The page is rewritten without its figure: the next sync drops it on the phone's copy.
    connection = db(home_app)
    try:
        pages.upsert_entry(connection, topic="figures", title="Figures", specialty_id=None, summary="S.", sections=[{"heading": "H", "paragraphs": [{"text": "No figure now.", "point_ids": []}]}], point_ids=[], points_hash_value="2")
    finally:
        connection.close()
    run_sync(home_app, away, scope="lean")
    assert away.get(f"/api/images/{image['id']}").status_code == 404


def test_a_batch_carries_the_parents_its_rows_point_at(connection) -> None:
    """Feedback of 9 October: a pile that came from the peer is not logged again here, so a
    peer that lost it could never get it back, and every batch citing it failed for good.
    Each batch now carries its rows' parents, and a fresh peer takes the batch whole."""
    from vademecum.db import apply_migrations
    from vademecum.storage import piles

    connection.execute("UPDATE sync_state SET applying = 1 WHERE id = 1")  # as if applied from the peer
    pile = piles.create_pile(connection, title="From the phone", tier="mid")
    connection.execute("UPDATE sync_state SET applying = 0 WHERE id = 1")
    connection.commit()
    assert connection.execute("SELECT COUNT(*) FROM sync_log WHERE table_name = 'piles'").fetchone()[0] == 0
    connection.execute(
        "INSERT INTO knowledge_gap_flags (id, text, topic, pile_id, status, created_at, updated_at, addressed_at)"
        " VALUES ('kgf_1', 'Unsure', 't', ?, 'open', '2026-10-09T00:00:00Z', '2026-10-09T00:00:00Z', NULL)",
        (pile.id,),
    )
    connection.commit()
    changes, _through, _done = sync_store.changes_since(connection, 0)
    assert [(c.table, c.key) for c in changes if c.table == "piles"] == [("piles", [pile.id])]

    import sqlite3
    fresh = sqlite3.connect(":memory:")
    fresh.row_factory = sqlite3.Row
    fresh.execute("PRAGMA foreign_keys = ON")
    apply_migrations(fresh)
    applied = sync_store.apply_changes(fresh, changes, role="foris", directories={}, require_files=False)
    assert applied.applied >= 2 and fresh.execute("SELECT COUNT(*) FROM knowledge_gap_flags").fetchone()[0] == 1


def test_a_batch_with_its_parents_still_fits_the_peers_limit(connection, monkeypatch) -> None:
    """The parents a batch carries count against the peer's limit (it refuses a longer one)."""
    from vademecum.storage import piles

    monkeypatch.setattr(sync_store, "MAX_BATCH", 2)
    connection.execute("UPDATE sync_state SET applying = 1 WHERE id = 1")
    pile = piles.create_pile(connection, title="From the phone", tier="mid")
    connection.execute("UPDATE sync_state SET applying = 0 WHERE id = 1")
    for index in range(3):
        connection.execute(
            "INSERT INTO knowledge_gap_flags (id, text, topic, pile_id, status, created_at, updated_at, addressed_at)"
            " VALUES (?, 'Unsure', 't', ?, 'open', '2026-10-09T00:00:00Z', '2026-10-09T00:00:00Z', NULL)",
            (f"kgf_{index}", pile.id),
        )
    connection.commit()
    since, seen = 0, 0
    while True:
        changes, through, done = sync_store.changes_since(connection, since)
        assert len(changes) <= 2
        seen += sum(1 for change in changes if change.table == "knowledge_gap_flags")
        since = through
        if done:
            break
    assert seen == 3


def test_a_peer_restored_with_a_shorter_log_is_read_again_from_the_start(pair) -> None:
    """Feedback of 9 October: the cloud copy came back on an older database, its log shorter
    than the Mac had read, and the Mac kept asking for changes after a point that no longer
    existed: work done on the phone never arrived."""
    _home, home_app, away, _ = pair
    connection = db(home_app)
    try:
        sync_store.record_sync(connection, peer_node_id="node_elsewhere", pulled_through=5000, note="before the restore")
    finally:
        connection.close()
    away.post("/api/flags", json={"text": "Made on the phone after the restore"})
    result = run_sync(home_app, away)
    assert result["pulled"] >= 1
    connection = db(home_app)
    try:
        texts = [row["text"] for row in connection.execute("SELECT text FROM knowledge_gap_flags")]
        assert "Made on the phone after the restore" in texts
        assert sync_store.state(connection)["pulled_through"] < 5000
    finally:
        connection.close()


def test_rows_from_a_restored_peer_are_queued_to_go_back_to_it(pair) -> None:
    """What the peer made before its restore reached here unlogged; it is queued again."""
    _home, home_app, _away, _ = pair
    connection = db(home_app)
    try:
        incoming = sync_store.Change(7, "knowledge_gap_flags", ["flg_phone"], "upsert", {
            "id": "flg_phone", "text": "Flagged on the phone", "topic": None, "pile_id": None,
            "status": "open", "created_at": "2026-10-02T00:00:00Z", "updated_at": "2026-10-02T00:00:00Z", "addressed_at": None,
        })
        sync_store.apply_changes(connection, [incoming], role="domi")
        logged = "SELECT COUNT(*) FROM sync_log WHERE table_name = 'knowledge_gap_flags' AND row_key = json_array('flg_phone')"
        assert connection.execute(logged).fetchone()[0] == 0
        assert sync_store.relog_unlogged(connection) >= 1
        assert connection.execute(logged).fetchone()[0] == 1
        assert sync_store.relog_unlogged(connection) == 0, "once is enough"
    finally:
        connection.close()


def test_an_upload_without_its_checksum_is_refused(pair) -> None:
    """Feedback of 10 October: a body with no checksum replaced an episode's audio."""
    _home, _home_app, away, away_app = pair
    from vademecum.storage import podcasts

    connection = db(away_app)
    try:
        episode = podcasts.create_episode(connection, title="E", entry_ids=[])
        connection.execute("UPDATE podcast_episodes SET audio_name = ? WHERE id = ?", (f"{episode.id}.m4a", episode.id))
        connection.commit()
    finally:
        connection.close()
    refused = away.put(f"/api/sync/podcast-audio/{episode.id}", content=b"x", headers={"X-Vademecum-Sync": TOKEN}).json()
    assert refused == {"stored": False, "reason": "digest"}


def test_a_page_edited_or_deleted_on_the_cloud_copy_is_applied_on_the_mac(pair) -> None:
    """Feedback of 10 October: edit and delete work away from the Mac; the Mac, whose pages
    they are, applies them at its next sync, and the result goes back."""
    from vademecum.storage import encyclopedia as pages
    from vademecum.storage import page_changes

    _home, home_app, away, away_app = pair
    connection = db(home_app)
    try:
        kept = pages.upsert_entry(connection, topic="sepsis", title="Sepsis", specialty_id=None, summary="s", sections=[], point_ids=[], points_hash_value="1")
        gone = pages.upsert_entry(connection, topic="gout", title="Gout", specialty_id=None, summary="s", sections=[], point_ids=[], points_hash_value="1")
    finally:
        connection.close()
    run_sync(home_app, away)
    assert away.put(f"/api/encyclopedia/{kept.id}", json={"body_md": "# Sepsis\n\nMy own words."}).status_code == 200
    assert away.delete(f"/api/encyclopedia/{gone.id}").json()["deleted"]["topic"] == "gout"
    run_sync(home_app, away, after_pull=lambda conn: page_changes.apply_waiting(conn, None))
    connection = db(home_app)
    try:
        assert "My own words." in pages.get_entry(connection, kept.id).body_md
        assert connection.execute("SELECT COUNT(*) FROM encyclopedia_entries WHERE id = ?", (gone.id,)).fetchone()[0] == 0
        assert "gout" in pages.deleted_pages(connection)
        assert page_changes.waiting(connection) == []
    finally:
        connection.close()
