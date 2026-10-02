"""The phone pack and the inbox (ADR 0016): the bank goes out as one file, a
session comes back as one block, and the Mac records what it recognises."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from test_end_to_end import LECTURE, build, client, provider, turns, upload  # noqa: F401 - fixtures
from test_folder_intake import touch
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.db import connect
from vademecum.ingest import inbox as inbox_module
from vademecum.ingest.pack import PACK_VERSION, SESSION_FENCE, build_pack


def bank(client: TestClient) -> dict:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(client, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    build(client, pile["id"])
    assert client.get(f"/api/piles/{pile['id']}/build/status").json()["run"]["status"] == "succeeded"
    client.post("/api/flags", json={"text": "Unsure about vasopressor choice", "topic": "septic shock"})
    return pile


def test_the_pack_carries_the_bank_the_rules_and_the_log_format(client: TestClient, tmp_path: Path) -> None:
    bank(client)
    written = client.post("/api/pack")
    assert written.status_code == 201, written.text
    body = written.json()
    assert body["points"] == 1 and body["questions"] == 1 and body["flags"] == 1
    assert body["filename"].startswith("vademecum-pack-") and body["filename"].endswith(".md")
    assert "/" not in body["filename"] and "/" not in body["directory"]

    assert body["directory"] == "the phone folder in your source folder"
    text = (tmp_path / "source-folder" / "phone" / body["filename"]).read_text()
    assert "Educational only" in text or "Educational" in text
    assert "Never put patient identifiers" in text
    assert "Evidence-supported, machine reviewed" in text
    assert "Lactate clearance guides resuscitation" in text
    assert "How is resuscitation guided in septic shock?" in text
    assert "By serial lactate measurement." in text, "the reference answer is in the pack: the assistant grades"
    assert "Unsure about vasopressor choice" in text
    assert f"```{SESSION_FENCE}" in text and '"vademecum_session"' in text
    assert "/Users/" not in text and str(tmp_path) not in text
    for forbidden in ("streak", "due count", "quota"):
        assert forbidden not in text.lower()


def test_the_pack_goes_into_the_phone_folder_when_there_is_one(tmp_path: Path, turns, provider) -> None:  # noqa: F811
    folder = tmp_path / "Vademecum"
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        assert (folder / "phone").is_dir() and (folder / "inbox").is_dir()
        body = c.post("/api/pack").json()
        assert body["directory"] == "the phone folder in your source folder"
        assert (folder / "phone" / body["filename"]).is_file()


def session(client: TestClient, **extra) -> str:
    question = client.get("/api/tutor/next").json()["question"]
    log = {
        "vademecum_session": PACK_VERSION,
        "attempts": [
            {
                "question_id": question["id"],
                "answer": "Serial lactate, rechecked at two hours.",
                "outcome": "partially_correct",
                "feedback": "You named the measurement; the interval was vague.",
                "missing_or_unsafe": "The two-hour window.",
                "improved_answer": "Serial lactate measurement, reassessed at two hours.",
            },
            {"question_id": "tq_nope", "answer": "x", "outcome": "correct"},
            {"question_id": question["id"], "answer": "y", "outcome": "not-an-outcome"},
        ],
        "flags": [{"text": "Unsure about noradrenaline dosing", "topic": "vasopressors"}, {"text": ""}],
        **extra,
    }
    return "Here is your session log:\n\n```" + SESSION_FENCE + "\n" + json.dumps(log, indent=2) + "\n```\nSee you tomorrow.\n"


def test_a_session_log_in_the_inbox_is_recorded_once(tmp_path: Path, turns, provider) -> None:  # noqa: F811
    folder = tmp_path / "Vademecum"
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        bank(c)
        touch(folder / "inbox" / "Session 1.md", session(c).encode())
        touch(folder / "inbox" / "notes.md", b"# Just my notes, no block here\n")
        touch(folder / "inbox" / "photo.jpg", b"\xff\xd8\xff")

        report = c.post("/api/sources/scan").json()["inbox"]
        assert report["files"] == 1 and report["attempts"] == 1 and report["flags"] == 1
        assert report["unknown_questions"] == 1
        assert report["nothing_recognised"] == ["notes.md"]

        history = c.get("/api/tutor/history").json()
        assert len(history) == 1
        assert history[0]["outcome"] == "partially_correct" and history[0]["graded_by"] == "model"
        assert "outside a checked turn" in history[0]["uncertainty"]
        flags = [f["text"] for f in c.get("/api/flags").json()]
        assert "Unsure about noradrenaline dosing" in flags

        # The same file again changes nothing; a second session is taken.
        again = c.post("/api/sources/scan").json()["inbox"]
        assert again["files"] == 0 and again["already_taken"] == 1
        assert len(c.get("/api/tutor/history").json()) == 1
        touch(folder / "inbox" / "Session 2.md", session(c, session_note="the next day").encode())
        c.post("/api/sources/scan")
        assert len(c.get("/api/tutor/history").json()) == 2
        # Nothing in the inbox was removed.
        assert sorted(p.name for p in (folder / "inbox").iterdir()) == ["Session 1.md", "Session 2.md", "notes.md", "photo.jpg"]


def test_a_raw_json_session_and_a_newer_format_are_handled() -> None:
    assert inbox_module.parse_session('{"vademecum_session": 1, "attempts": []}') == [{"vademecum_session": 1, "attempts": []}]
    assert inbox_module.parse_session("no block") == []
    assert inbox_module.parse_session("```" + SESSION_FENCE + "\n{not json}\n```") == []
