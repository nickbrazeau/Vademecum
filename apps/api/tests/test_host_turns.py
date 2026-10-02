"""Host mode (ADR 0009): the learner's ChatGPT does the model work, the server checks.

Every test drives the real application with ``model_provider="host"``. There is
no turn runner at all in this mode: the pipeline files pending turns, a test
plays ChatGPT by reading them and submitting results, and the same storage
rules, quote checks and evidence gates run on what comes back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider
from test_end_to_end import ASSESSMENT, EVIDENCE, GRADE, LECTURE, QUOTE, SYNTHESIS, ABSTRACT
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model import prompts, schemas
from vademecum.model.host import HostTurns, SubmissionRefused


@pytest.fixture()
def host_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host")


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider(
        articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)]
    )


@pytest.fixture()
def host(host_settings: Settings, provider: FakeProvider):
    app = create_app(
        host_settings, transport_factory=refusing_factory(), provider_factory=lambda: provider
    )
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        yield client


def seed(client: TestClient) -> tuple[str, str]:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    uploaded = client.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("lecture.txt", LECTURE.encode(), "text/plain"))],
        data={"confidence": "mid"},
    )
    assert uploaded.status_code == 201
    return pile["id"], uploaded.json()["results"][0]["source"]["id"]


def start(client: TestClient, pile_id: str) -> dict:
    preview = client.get(f"/api/piles/{pile_id}/build/preview").json()
    assert preview["blocked_reason"] == ""
    started = client.post(
        f"/api/piles/{pile_id}/build",
        json={"batch_id": preview["batch_id"], "selection_hash": preview["selection_hash"]},
    )
    assert started.status_code == 202, started.text
    return started.json()


def submit(client: TestClient, pile_id: str, turn_id: str, result: dict):
    return client.post(
        f"/api/piles/{pile_id}/build/submit", json={"turn_id": turn_id, "result": result}
    )


def play_chatgpt(client: TestClient, pile_id: str, first: dict) -> dict:
    """Answer every pending turn with the scripted result for its kind."""
    replies = {"synthesis": SYNTHESIS, "evidence": EVIDENCE, "assessment": ASSESSMENT}
    turn = first
    for _ in range(20):
        response = submit(client, pile_id, turn["turn_id"], replies[turn["kind"]])
        assert response.status_code == 200, response.text
        body = response.json()
        if body["next"] is None:
            return body
        turn = body["next"]
    raise AssertionError("the build never finished")


# --- the build ------------------------------------------------------------------


def test_health_says_this_process_runs_no_model(host: TestClient) -> None:
    health = host.get("/api/health").json()
    assert health["model_mode"] == "host"
    assert health["model_calls_configured"] is False


def test_the_disclosures_name_chatgpt_not_codex(host: TestClient) -> None:
    pile_id, _ = seed(host)
    preview = host.get(f"/api/piles/{pile_id}/build/preview").json()
    assert "your own ChatGPT" in preview["disclosure"]["destination"]
    assert "no API key" in preview["disclosure"]["destination"]
    assert "Codex" not in preview["disclosure"]["destination"]
    overview = host.get("/api/tutor").json()
    assert overview["mode"] == "host"
    assert "Codex" not in overview["disclosure"]["destination"]


def test_a_build_is_a_sequence_of_pending_turns(host: TestClient, provider: FakeProvider) -> None:
    pile_id, _ = seed(host)
    started = start(host, pile_id)
    assert started["run"]["status"] == "running"
    assert len(started["pending"]) == 1
    first = started["pending"][0]

    # The first turn is synthesis, and it carries exactly what Codex would get.
    assert first["kind"] == "synthesis"
    assert first["instructions"] == prompts.BASE_INSTRUCTIONS
    assert first["rules"] == prompts.SYNTHESIS_DEVELOPER
    assert first["output_schema"] == schemas.SYNTHESIS_SCHEMA
    assert "[E1]" in first["material"]
    assert QUOTE in first["material"]
    assert "lecture.txt" in first["material"], "the disclosure said filenames are shown"
    assert "src_" not in first["material"] and "seg_" not in first["material"]
    assert "/Users/" not in first["material"]
    assert first["how_to_submit"]

    status = host.get(f"/api/piles/{pile_id}/build/status").json()
    assert status["awaiting_host"] is True
    assert status["pending_kinds"] == ["synthesis"]
    listed = host.get(f"/api/piles/{pile_id}/build/pending").json()
    assert [turn["turn_id"] for turn in listed["turns"]] == [first["turn_id"]]

    # Play ChatGPT: synthesis, then evidence, then assessment.
    after_synthesis = submit(host, pile_id, first["turn_id"], SYNTHESIS).json()
    assert after_synthesis["accepted"] is True
    assert after_synthesis["next"]["kind"] == "evidence"
    assert ABSTRACT[:40] in after_synthesis["next"]["material"]
    assert provider.queries == ['"septic shock"'], "PubMed was asked by the server, not by ChatGPT"

    after_evidence = submit(host, pile_id, after_synthesis["next"]["turn_id"], EVIDENCE).json()
    assert after_evidence["next"]["kind"] == "assessment"
    assert "How is resuscitation guided" in after_evidence["next"]["material"]

    done = submit(host, pile_id, after_evidence["next"]["turn_id"], ASSESSMENT).json()
    assert done["next"] is None
    assert done["run"]["status"] == "succeeded", done["run"]
    assert done["run"]["point_count"] == 1

    points = host.get("/api/points").json()
    assert points[0]["support"] == "evidence_supported"
    assert points[0]["evidence"][0]["pmid"] == "30012345"
    assert host.get("/api/tutor").json()["eligible"] == 1
    assert host.get(f"/api/piles/{pile_id}/build/status").json()["awaiting_host"] is False


def test_a_submission_that_does_not_match_the_schema_is_refused_and_the_turn_stays(
    host: TestClient,
) -> None:
    pile_id, _ = seed(host)
    first = start(host, pile_id)["pending"][0]

    bad = submit(host, pile_id, first["turn_id"], {"points": [], "search_topics": [], "verified": True})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_submission"
    assert "verified" not in bad.json()["error"]["message"], "nothing submitted is echoed"

    wrong_enum = submit(host, pile_id, first["turn_id"], {"points": [], "search_topics": 5})
    assert wrong_enum.status_code == 422

    listed = host.get(f"/api/piles/{pile_id}/build/pending").json()
    assert [turn["turn_id"] for turn in listed["turns"]] == [first["turn_id"]], "still pending"
    assert host.get(f"/api/piles/{pile_id}/build/status").json()["run"]["status"] == "running"


def test_a_turn_accepts_exactly_one_result(host: TestClient) -> None:
    pile_id, _ = seed(host)
    first = start(host, pile_id)["pending"][0]
    assert submit(host, pile_id, first["turn_id"], SYNTHESIS).status_code == 200
    replay = submit(host, pile_id, first["turn_id"], SYNTHESIS)
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "already_submitted"


def test_an_unknown_turn_is_refused(host: TestClient) -> None:
    pile_id, _ = seed(host)
    start(host, pile_id)
    response = submit(host, pile_id, "turn_nope", SYNTHESIS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unknown_turn"


def test_a_turn_from_another_pile_cannot_be_submitted_here(host: TestClient) -> None:
    pile_id, _ = seed(host)
    first = start(host, pile_id)["pending"][0]
    other = host.post("/api/piles", json={"title": "Other", "tier": "low"}).json()
    response = submit(host, other["id"], first["turn_id"], SYNTHESIS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "unknown_turn"


def test_nothing_survives_the_quote_check_even_from_a_valid_submission(host: TestClient) -> None:
    """A schema-valid answer whose quotes are not in the material yields no points."""
    pile_id, _ = seed(host)
    first = start(host, pile_id)["pending"][0]
    invented = {
        "points": [
            {
                **SYNTHESIS["points"][0],
                "citations": [{"excerpt_id": "E1", "quote": "a sentence that is not in the lecture"}],
                "questions": [],
            }
        ],
        "search_topics": ["septic shock"],
    }
    done = submit(host, pile_id, first["turn_id"], invented).json()
    assert done["next"] is None
    assert done["run"]["status"] == "failed"
    assert done["run"]["failure_category"] == "invalid_output"
    assert host.get("/api/points").json() == []


def test_cancelling_abandons_the_pending_turn(host: TestClient) -> None:
    pile_id, _ = seed(host)
    first = start(host, pile_id)["pending"][0]
    cancelled = host.post(f"/api/piles/{pile_id}/build/cancel").json()
    assert cancelled["stopped"] is True
    assert cancelled["run"]["status"] == "cancelled"
    assert host.get(f"/api/piles/{pile_id}/build/pending").json()["turns"] == []
    late = submit(host, pile_id, first["turn_id"], SYNTHESIS)
    assert late.status_code == 409


def test_codex_mode_has_nothing_to_submit(client: TestClient) -> None:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    response = client.post(
        f"/api/piles/{pile['id']}/build/submit", json={"turn_id": "turn_x", "result": {}}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "host_mode_only"
    assert client.get(f"/api/piles/{pile['id']}/build/pending").json()["turns"] == []
    assert client.get("/api/health").json()["model_mode"] == "codex"


def test_the_process_has_no_turn_runner_in_host_mode(host: TestClient) -> None:
    with pytest.raises(RuntimeError):
        host.app.state.turn_factory()


# --- grading ----------------------------------------------------------------------


def build_bank(host: TestClient) -> tuple[str, str, dict]:
    pile_id, source_id = seed(host)
    first = start(host, pile_id)["pending"][0]
    play_chatgpt(host, pile_id, first)
    question = host.get("/api/tutor/next").json()["question"]
    assert question is not None
    return pile_id, source_id, question


def test_grading_is_two_halves_around_chatgpt(host: TestClient) -> None:
    _, _, question = build_bank(host)

    asked = host.post(
        "/api/tutor/grade", json={"question_id": question["id"], "answer": "Serial lactate."}
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["attempt"] is None
    assert body["refused"] == "host_mode"
    assert "ChatGPT" in body["message"]
    turn = body["pending"]
    assert turn["kind"] == "grading"
    assert turn["rules"] == prompts.GRADING_DEVELOPER
    assert turn["output_schema"] == schemas.GRADING_SCHEMA
    assert "Serial lactate." in turn["material"]
    assert "By serial lactate measurement." in turn["material"]
    assert "lecture.txt" not in turn["material"], "four things, and nothing else"
    assert "reference_answer" not in body["question"]

    recorded = host.post(
        "/api/tutor/grade/submit", json={"turn_id": turn["turn_id"], "result": GRADE}
    )
    assert recorded.status_code == 200, recorded.text
    result = recorded.json()
    assert result["attempt"]["outcome"] == "partially_correct"
    assert result["attempt"]["graded_by"] == "model"
    assert "answer" not in result["attempt"]
    assert result["question"]["reference_answer"]

    history = host.get("/api/tutor/history").json()
    assert len(history) == 1
    assert history[0]["asked_prompt"] == question["prompt"]

    again = host.post("/api/tutor/grade/submit", json={"turn_id": turn["turn_id"], "result": GRADE})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_submitted"


def test_a_grade_that_does_not_match_the_schema_is_refused(host: TestClient) -> None:
    _, _, question = build_bank(host)
    turn = host.post(
        "/api/tutor/grade", json={"question_id": question["id"], "answer": "Lactate."}
    ).json()["pending"]
    refused = host.post(
        "/api/tutor/grade/submit",
        json={"turn_id": turn["turn_id"], "result": {**GRADE, "outcome": "brilliant"}},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_submission"
    assert host.get("/api/tutor/history").json() == []


def test_a_grade_for_a_question_that_moved_is_not_recorded(host: TestClient) -> None:
    _, source_id, question = build_bank(host)
    turn = host.post(
        "/api/tutor/grade", json={"question_id": question["id"], "answer": "Lactate."}
    ).json()["pending"]
    # The source is excluded while ChatGPT is grading: the question is held.
    assert host.patch(f"/api/sources/{source_id}", json={"excluded": True}).status_code == 200
    response = host.post(
        "/api/tutor/grade/submit", json={"turn_id": turn["turn_id"], "result": GRADE}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["attempt"] is None
    assert body["refused"] in {"stale", "not_eligible"}
    assert host.get("/api/tutor/history").json() == []


def test_a_patient_specific_answer_is_a_boundary_not_a_turn(host: TestClient) -> None:
    _, _, question = build_bank(host)
    response = host.post(
        "/api/tutor/grade",
        json={"question_id": question["id"], "answer": "My patient is in front of me, should I give it?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["refused"] == "patient_specific"
    assert "pending" not in body


def test_grade_submit_in_codex_mode_is_refused(client: TestClient) -> None:
    response = client.post("/api/tutor/grade/submit", json={"turn_id": "turn_x", "result": {}})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "host_mode_only"


# --- the registry on its own --------------------------------------------------------


def test_a_restart_abandons_whatever_was_pending(host_settings: Settings) -> None:
    from vademecum.app import prepare_database

    database_path, _ = prepare_database(host_settings)
    turns = HostTurns(database_path)
    turn = turns.create(
        kind="grading",
        scope_kind="question",
        scope_id="q1",
        instructions="i",
        rules="r",
        material="m",
        output_schema=schemas.GRADING_SCHEMA,
    )
    assert turns.pending("question", "q1")
    assert HostTurns(database_path).sweep() == 1
    assert turns.get(turn.id).status == "abandoned"
    with pytest.raises(SubmissionRefused) as refused:
        turns.submit(turn.id, GRADE)
    assert refused.value.code == "turn_expired"


def test_a_turn_expires(host_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta, timezone

    from vademecum.app import prepare_database
    from vademecum.model import host as host_module

    database_path, _ = prepare_database(host_settings)
    turns = HostTurns(database_path, ttl_seconds=60)
    turn = turns.create(
        kind="grading",
        scope_kind="question",
        scope_id="q1",
        instructions="i",
        rules="r",
        material="m",
        output_schema=schemas.GRADING_SCHEMA,
    )
    later = datetime.now(timezone.utc) + timedelta(minutes=5)
    monkeypatch.setattr(host_module, "_now", lambda: later)
    assert turns.pending("question", "q1") == []
    with pytest.raises(SubmissionRefused) as refused:
        turns.submit(turn.id, GRADE)
    assert refused.value.code == "turn_expired"


def test_pending_turns_are_exported_with_everything_else(host: TestClient) -> None:
    from vademecum.storage.maintenance import EXPORTED_TABLES

    assert "pending_turns" in EXPORTED_TABLES
    assert host.post("/api/export").status_code == 201
