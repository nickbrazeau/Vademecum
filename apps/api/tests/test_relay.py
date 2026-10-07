"""The Socratic relay (feedback of 6 October): the phone's own tutor, answered by the Mac.

The phone's copy has no model; it queues each turn, the Mac collects it over the sync
routes, computes the next question with its own connection, and posts it back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from test_socratic_podcasts import DialogueTurns
from test_sync import TOKEN, ClientTransport
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.db import connect
from vademecum.model import socratic as tutor
from vademecum.storage import encyclopedia as pages
from vademecum.sync import Peer


def _phone(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "phone", host="127.0.0.1", port=8765, sources_folder_enabled=False,
        sync_role="foris", sync_accept_token=TOKEN, model_provider="host",
    )
    app = create_app(settings, transport_factory=refusing_factory())
    return app


def _mac_answers(app, peer: Peer, turns: DialogueTurns) -> int:
    """What the Mac's loop does once: collect, compute, reply."""
    answered = 0
    for asked in peer.relay_wait(1):
        payload = asyncio.run(
            tutor.compute_turn(
                app.state.database_path,
                scope_id=asked["session_id"],
                entry_id=asked["entry_id"],
                transcript=[(t["role"], t["text"]) for t in asked["transcript"]],
                exchanges=asked["exchanges"],
                turn_factory=turns,
            )
        )
        assert peer.relay_reply(asked["id"], payload)["applied"] is True
        answered += 1
    return answered


def test_the_phone_tutor_is_answered_by_the_mac(tmp_path: Path) -> None:
    app = _phone(tmp_path)
    turns = DialogueTurns()
    with TestClient(app, base_url=LOCAL_ORIGIN) as phone:
        connection = connect(app.state.database_path)
        try:
            pages.upsert_entry(connection, topic="sepsis", title="Sepsis", specialty_id=None, summary="Lactate matters.", sections=[], point_ids=[], points_hash_value="1")
        finally:
            connection.close()
        peer = Peer(ClientTransport(phone), TOKEN)

        quiet = phone.get("/api/socratic").json()
        assert quiet["relay"] == {"available": True, "live": False} and quiet["can_answer_here"] is False
        assert "being woken" in quiet["note"]

        assert peer.relay_wait(0) == []  # the Mac checks in
        live = phone.get("/api/socratic").json()
        assert live["relay"]["live"] is True and live["can_answer_here"] is True and live["note"] == ""

        started = phone.post("/api/socratic", json={"relay": True}).json()
        session_id = started["session"]["id"]
        assert started["session"]["waiting"] is True and "first question" in started["note"]
        assert _mac_answers(app, peer, turns) == 1
        opened = phone.get(f"/api/socratic/{session_id}").json()["session"]
        assert opened["waiting"] is False and opened["transcript"][0]["role"] == "tutor"

        for answer in ("Septic shock first.", "Fluids, then pressors."):
            replied = phone.post(f"/api/socratic/{session_id}/answer", json={"answer": answer}).json()
            assert replied["session"]["waiting"] is True and replied["session"]["transcript"][-1]["text"] == answer
            assert _mac_answers(app, peer, turns) == 1
        done = phone.get(f"/api/socratic/{session_id}").json()["session"]
        assert done["status"] == "done" and done["assessed"], "the Mac's last turn closes it with the assessment"
        assert "Antiviral" not in str(done)  # nothing from elsewhere leaked in

        # A Mac that cannot answer says so on the session; the answer is kept.
        again = phone.post("/api/socratic", json={"relay": True}).json()["session"]["id"]
        asked = peer.relay_wait(1)[0]
        peer.relay_reply(asked["id"], {"error": "usage_limit"})
        failed = phone.get(f"/api/socratic/{again}").json()["session"]
        assert failed["relay_error"] == "usage_limit" and failed["waiting"] is False


def test_a_chat_host_still_starts_sessions_without_the_relay(tmp_path: Path) -> None:
    """ChatGPT or Claude call socratic_start (no relay flag): unchanged."""
    app = _phone(tmp_path)
    with TestClient(app, base_url=LOCAL_ORIGIN) as phone:
        connection = connect(app.state.database_path)
        try:
            pages.upsert_entry(connection, topic="sepsis", title="Sepsis", specialty_id=None, summary="S.", sections=[], point_ids=[], points_hash_value="1")
        finally:
            connection.close()
        started = phone.post("/api/socratic", json={}).json()
        assert started["session"]["status"] == "open" and "waiting" not in started["session"]
        assert phone.post(f"/api/socratic/{started['session']['id']}/turn", json={"question": "Q?", "answer": "A."}).status_code == 200


def test_the_relay_routes_need_the_sync_token(tmp_path: Path) -> None:
    app = _phone(tmp_path)
    with TestClient(app, base_url=LOCAL_ORIGIN) as phone:
        assert phone.get("/api/sync/relay/wait?timeout=0").status_code == 404
        assert phone.post("/api/sync/relay/x", json={"payload": {}}).status_code == 404


def test_the_macs_loop_wakes_on_the_flag_answers_and_replies(tmp_path: Path, monkeypatch) -> None:
    """The Mac's side: idle until the flag is fresh, then collect, answer, reply."""
    import time

    import vademecum.sync as sync_module
    from vademecum import app as app_module

    database = tmp_path / "mac.sqlite3"
    from vademecum.db.migrate import apply_migrations

    connection = connect(database)
    try:
        apply_migrations(connection)
        entry = pages.upsert_entry(connection, topic="sepsis", title="Sepsis", specialty_id=None, summary="S.", sections=[], point_ids=[], points_hash_value="1")
    finally:
        connection.close()

    replies: list[tuple[str, dict]] = []
    flags = [None, time.time()]  # first look: nobody there; second: the owner opened the tutor

    class FakePeer:
        def __init__(self, *args, **kwargs) -> None:
            self.waits = 0

        def relay_wanted(self):
            return flags.pop(0) if flags else time.time()

        def relay_wait(self, timeout=20):
            self.waits += 1
            if self.waits == 1:
                return [{"id": "r1", "session_id": "soc_x", "entry_id": entry.id, "transcript": [], "exchanges": 0}]
            time.sleep(0.05)
            return []

        def relay_reply(self, request_id, payload):
            replies.append((request_id, payload))
            return {"applied": True}

    monkeypatch.setattr(sync_module, "Peer", FakePeer)
    monkeypatch.setattr(sync_module, "HttpTransport", lambda *a, **k: None)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda seconds: real_sleep(0.01))
    settings = Settings(data_dir=tmp_path / "d", sync_peer_url="https://seat.example", sync_token=TOKEN, sources_folder_enabled=False)

    async def run() -> None:
        task = asyncio.create_task(app_module._relay_loop(database, settings, DialogueTurns()))
        for _ in range(200):
            if replies:
                break
            await real_sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert replies and replies[0][0] == "r1" and replies[0][1].get("question"), "the opening question, written on the Mac"
