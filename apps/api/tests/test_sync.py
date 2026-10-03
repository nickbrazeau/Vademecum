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


def run_sync(home_app, away: TestClient, scope: str = "full") -> dict:
    connection = db(home_app)
    try:
        return sync_once(
            connection,
            peer=Peer(ClientTransport(away), TOKEN),
            source_dir=home_app.state.source_dir,
            role="domi",
            scope=scope,
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
