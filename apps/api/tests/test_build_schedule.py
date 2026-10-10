"""Builds on a timer (ADR 0018): a standing consent, runs the Mac does by
itself, and a refusal in host mode where nobody is there to do the turns."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from test_end_to_end import ABSTRACT, ASSESSMENT, EVIDENCE, LECTURE, SYNTHESIS, upload
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.schedule import NEEDS_CODEX, next_due
from vademecum.storage import schedule as store


def test_the_next_due_time_is_the_next_of_the_times_after_now() -> None:
    tz = timezone(timedelta(hours=-4))
    now = datetime(2026, 10, 3, 9, 30, tzinfo=tz)
    assert next_due(["07:00", "12:00", "18:00"], now) == datetime(2026, 10, 3, 12, 0, tzinfo=tz)
    late = datetime(2026, 10, 3, 19, 0, tzinfo=tz)
    assert next_due(["07:00", "12:00", "18:00"], late) == datetime(2026, 10, 4, 7, 0, tzinfo=tz)
    assert next_due(["12:00"], datetime(2026, 10, 3, 12, 0, tzinfo=tz)) == datetime(2026, 10, 4, 12, 0, tzinfo=tz)


def test_times_are_checked_and_consent_is_recorded_only_while_on(connection) -> None:
    assert store.get_schedule(connection) == {
        "enabled": False, "times": ["07:00", "12:00", "18:00"], "batches_per_run": 3, "consent_at": None,
        "continuous": True, "paused_until": None,
    }
    with pytest.raises(store.InvalidSchedule):
        store.normalise_times(["7am"])
    with pytest.raises(store.InvalidSchedule):
        store.normalise_times([])
    on = store.set_schedule(connection, enabled=True, times=["18:00", "07:00", "07:00"], batches_per_run=2)
    assert on["times"] == ["07:00", "18:00"] and on["batches_per_run"] == 2
    assert on["consent_at"] is not None
    again = store.set_schedule(connection, enabled=True, times=["09:00"], batches_per_run=2)
    assert again["consent_at"] == on["consent_at"], "a change while on keeps the original consent"
    off = store.set_schedule(connection, enabled=False, times=["09:00"], batches_per_run=2)
    assert off["consent_at"] is None, "turning it off withdraws it"


@pytest.fixture()
def codex_client(tmp_path):
    turns = ScriptedTurns(synthesis=[SYNTHESIS, SYNTHESIS], evidence=[EVIDENCE, EVIDENCE], assessment=[ASSESSMENT, ASSESSMENT])
    provider = FakeProvider(articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        yield client, app


def test_the_schedule_is_read_set_and_disclosed(codex_client) -> None:
    client, _ = codex_client
    before = client.get("/api/build/schedule").json()
    assert before["enabled"] is False and before["can_run"] is True and before["next_run_at"] is None
    assert "without a further prompt" in before["disclosure"] and "OpenAI" in before["disclosure"]

    bad = client.put("/api/build/schedule", json={"enabled": True, "times": ["noon"]})
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "invalid_schedule"

    on = client.put("/api/build/schedule", json={"enabled": True, "times": ["07:00", "18:00"], "batches_per_run": 2}).json()
    assert on["enabled"] is True and on["continuous"] is True and on["next_run_at"] is None, "builds whenever awake, by default"
    assert on["consent_at"] is not None and on["builder"]["state"]
    timer = client.put("/api/build/schedule", json={"enabled": True, "times": ["07:00", "18:00"], "continuous": False}).json()
    assert timer["continuous"] is False and timer["times"] == ["07:00", "18:00"] and timer["next_run_at"]
    paused = client.put("/api/build/schedule", json={"enabled": True, "times": ["07:00"], "pause_hours": 2}).json()
    assert paused["paused_until"]
    resumed = client.put("/api/build/schedule", json={"enabled": True, "times": ["07:00"], "pause_hours": 0}).json()
    assert resumed["paused_until"] is None


def test_run_now_builds_every_pile_and_records_what_it_did(codex_client) -> None:
    import time

    client, app = codex_client
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(client, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    empty = client.post("/api/piles", json={"title": "Empty", "tier": "low"}).json()

    started = client.post("/api/build/schedule/run")
    assert started.status_code == 202, started.text
    assert started.json()["started"] is True

    for _ in range(200):
        last = client.get("/api/build/schedule").json()["last_run"]
        if last is not None and not client.get("/api/build/schedule").json()["running"]:
            break
        time.sleep(0.05)
    assert last is not None and last["ran"] is True and last["reason"] == "requested"
    by_title = {entry["title"]: entry for entry in last["piles"]}
    assert by_title["Sepsis"]["status"] == "succeeded" and by_title["Sepsis"]["batches"] >= 1
    assert by_title["Sepsis"]["points"] == 1
    assert by_title["Empty"]["status"] == "nothing_to_build" and by_title["Empty"]["batches"] == 0
    assert len(client.get("/api/points").json()) == 1
    # A second run finds nothing left to build and sends nothing.
    client.post("/api/build/schedule/run")
    for _ in range(100):
        if not client.get("/api/build/schedule").json()["running"]:
            break
        time.sleep(0.05)
    assert client.get("/api/build/schedule").json()["last_run"]["piles"][0]["batches"] == 0


def test_host_mode_refuses_to_run_and_says_why(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        described = client.get("/api/build/schedule").json()
        assert described["can_run"] is False and "Codex" in described["blocked_reason"]
        refused = client.post("/api/build/schedule/run")
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "needs_codex"
        assert NEEDS_CODEX.split(".")[0] in refused.json()["error"]["message"]


def test_in_the_background_a_new_file_is_built_without_pressing_anything(codex_client) -> None:
    """ADR 0033 (feedback of 10 October): with consent given, the Mac builds whenever it is
    awake, one batch at a time, and says what it is doing."""
    import time

    client, _app = codex_client
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(client, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    client.put("/api/build/schedule", json={"enabled": True, "times": ["07:00"]})
    for _ in range(300):
        if len(client.get("/api/points").json()) == 1:
            break
        time.sleep(0.05)
    assert len(client.get("/api/points").json()) == 1, "built in the background"
    for _ in range(100):
        builder = client.get("/api/build/schedule").json()["builder"]
        if builder["state"] == "idle":
            break
        time.sleep(0.05)
    assert builder["state"] == "idle" and "Everything read in has been built" in builder["reason"]
