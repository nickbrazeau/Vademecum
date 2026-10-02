"""The whole vertical slice over HTTP: upload -> preview -> build -> Tutor -> grade.

Drives the real application with a fake turn runner and a fake provider. No
model call, no socket, no subprocess.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from vademecum.app import create_app
from vademecum.config import Settings

LECTURE = (
    "Septic shock: resuscitation targets.\n\n"
    "Serial lactate measurement guides resuscitation in septic shock. "
    "A fall of at least ten per cent within two hours is associated with "
    "improved survival in the cohorts reviewed here. Noradrenaline remains "
    "the first-line vasopressor for adults with septic shock.\n"
)
QUOTE = "Serial lactate measurement guides resuscitation in septic shock"
ABSTRACT = (
    "We studied resuscitation targets in septic shock across two centres. "
    "Serial lactate measurement guides resuscitation in septic shock and "
    "predicted mortality in this cohort of 412 adults admitted to intensive "
    "care with a vasopressor requirement, followed to ninety days."
)

SYNTHESIS = {
    "points": [
        {
            "claim": "Lactate clearance guides resuscitation in septic shock",
            "detail": "From the uploaded lecture.",
            "topics": ["septic shock"],
            "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
            "unclear": False,
            "questions": [
                {
                    "prompt": "How is resuscitation guided in septic shock?",
                    "reference_answer": "By serial lactate measurement.",
                    "rubric": "Mentions serial lactate measurement.",
                    "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
                }
            ],
        }
    ],
    "search_topics": ["septic shock"],
}
EVIDENCE = {
    "relation": "supports",
    "quote": "Serial lactate measurement guides resuscitation in septic shock",
    "reasoning": "The abstract states it.",
}
ASSESSMENT = {"verdict": "sound", "problems": [], "notes": ""}
GRADE = {
    "outcome": "partially_correct",
    "feedback": "You named the measurement but not the interval.",
    "strengths": "Correctly identified serial lactate.",
    "missing_or_unsafe": "No mention of the two-hour window.",
    "improved_answer": "Serial lactate measurement, reassessed at two hours.",
    "uncertainty": "",
}


@pytest.fixture()
def turns() -> ScriptedTurns:
    script = ScriptedTurns(
        synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT]
    )
    script.grading = [GRADE]
    return script


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider(
        articles=[
            FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)
        ]
    )


@pytest.fixture()
def client(settings: Settings, turns: ScriptedTurns, provider: FakeProvider):
    app = create_app(
        settings,
        transport_factory=refusing_factory(),
        provider_factory=lambda: provider,
    )
    with TestClient(app, base_url=LOCAL_ORIGIN) as test_client:
        # The turn factory is the seam: a scripted script, never a real runner.
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        yield test_client


def upload(client: TestClient, pile_id: str, name: str, body: bytes, confidence="mid"):
    return client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", (name, body, "text/plain"))],
        data={"confidence": confidence},
    )


def build(client: TestClient, pile_id: str) -> dict:
    preview = client.get(f"/api/piles/{pile_id}/build/preview").json()
    assert preview["blocked_reason"] == "", preview["blocked_reason"]
    started = client.post(
        f"/api/piles/{pile_id}/build",
        json={
            "batch_id": preview["batch_id"],
            "selection_hash": preview["selection_hash"],
        },
    )
    assert started.status_code == 202, started.text
    return preview


class TestTheWholeFlow:
    def test_upload_preview_build_tutor_grade(
        self, client: TestClient, turns: ScriptedTurns, provider: FakeProvider
    ) -> None:
        pile = client.post(
            "/api/piles", json={"title": "Sepsis", "tier": "mid"}
        ).json()

        # --- upload ---------------------------------------------------------
        uploaded = upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        assert uploaded.status_code == 201
        body = uploaded.json()
        assert body["accepted"] == 1 and body["rejected"] == 0
        source = body["results"][0]["source"]
        assert source["status"] == "extracted"
        assert source["coverage"]["chars_covered"] == 0

        # --- preview: the disclosure names what would be sent ---------------
        preview = client.get(f"/api/piles/{pile['id']}/build/preview").json()
        assert preview["excerpt_count"] >= 1
        assert preview["excerpts"][0]["display_name"] == "lecture.txt"
        assert preview["excerpts"][0]["confidence_label"] == "Medium"
        assert QUOTE in preview["excerpts"][0]["text"]
        assert "OpenAI" in preview["disclosure"]["destination"]
        assert len(preview["selection_hash"]) == 64

        # --- build ----------------------------------------------------------
        started = client.post(
            f"/api/piles/{pile['id']}/build",
            json={
                "batch_id": preview["batch_id"],
                "selection_hash": preview["selection_hash"],
            },
        )
        assert started.status_code == 202
        status = client.get(f"/api/piles/{pile['id']}/build/status").json()
        assert status["run"]["status"] == "succeeded", status["run"]
        assert status["run"]["point_count"] == 1
        assert status["coverage"]["chars_covered"] > 0

        # Only short public topic words reached the provider.
        assert provider.queries == ['"septic shock"']
        assert LECTURE[:40] not in " ".join(provider.queries)

        # --- the point, with its provenance ---------------------------------
        points = client.get("/api/points").json()
        assert len(points) == 1
        point = points[0]
        assert point["support"] == "evidence_supported"
        assert point["support_label"] == "Evidence-supported, machine reviewed"
        assert "not clinically validated" in point["support_meaning"]
        assert point["citations"][0]["display_name"] == "lecture.txt"
        assert point["evidence"][0]["pmid"] == "30012345"
        assert point["evidence_grade"] == "abstract_only"

        # --- Tutor ----------------------------------------------------------
        overview = client.get("/api/tutor").json()
        assert overview["eligible"] == 1
        assert "Grade" in overview["disclosure"]["headline"] or overview["disclosure"][
            "bullets"
        ]

        first = client.get("/api/tutor/next").json()
        question = first["question"]
        assert question is not None
        assert "reference_answer" not in question, "the answer is not shown up front"
        assert first["cycle"]["remaining"] == 1

        # Refreshing keeps the same question rather than burning one.
        again = client.get("/api/tutor/next").json()
        assert again["question"]["id"] == question["id"]

        # --- grade ----------------------------------------------------------
        graded = client.post(
            "/api/tutor/grade",
            json={"question_id": question["id"], "answer": "Serial lactate."},
        )
        assert graded.status_code == 200, graded.text
        result = graded.json()
        assert result["attempt"]["outcome"] == "partially_correct"
        assert result["attempt"]["graded_by"] == "model"
        assert result["question"]["reference_answer"]
        # The learner's own answer is not echoed back in the attempt.
        assert "answer" not in result["attempt"]

        # Exactly what was transmitted, and nothing else.
        grading_prompt = turns.prompts("grading")[0]
        assert "Serial lactate." in grading_prompt
        assert question["prompt"] in grading_prompt
        assert "lecture.txt" not in grading_prompt

        # --- history and Today ---------------------------------------------
        history = client.get("/api/tutor/history").json()
        assert len(history) == 1
        assert history[0]["asked_prompt"] == question["prompt"]

        today = client.get("/api/today").json()
        assert len(today["worth_a_look"]) == 1
        assert today["tutor"]["eligible"] == 1
        assert today["sources"]["usable"] == 1

    def test_advance_does_not_repeat_until_the_cycle_is_exhausted(
        self, client: TestClient, turns: ScriptedTurns
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        build(client, pile["id"])

        first = client.get("/api/tutor/next").json()["question"]
        advanced = client.post(
            "/api/tutor/advance", json={"question_id": first["id"]}
        ).json()
        # Only one eligible question exists, so the cycle redraws rather than
        # claiming there is nothing to ask.
        assert advanced["question"] is not None
        assert advanced["cycle"]["cycle_number"] >= 1

    def test_a_stale_preview_is_refused_with_a_reason(self, client: TestClient) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        uploaded = upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        source_id = uploaded.json()["results"][0]["source"]["id"]
        preview = client.get(f"/api/piles/{pile['id']}/build/preview").json()

        client.patch(f"/api/sources/{source_id}", json={"excluded": True})
        refused = client.post(
            f"/api/piles/{pile['id']}/build",
            json={
                "batch_id": preview["batch_id"],
                "selection_hash": preview["selection_hash"],
            },
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "selection_changed"

    def test_excluding_a_source_removes_its_questions_from_tutor(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        uploaded = upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        source_id = uploaded.json()["results"][0]["source"]["id"]
        build(client, pile["id"])
        question = client.get("/api/tutor/next").json()["question"]
        assert question is not None

        client.patch(f"/api/sources/{source_id}", json={"excluded": True})

        empty = client.get("/api/tutor/next").json()
        assert empty["question"] is None
        assert empty["empty_reason"]
        # And grading it now is refused rather than recorded.
        graded = client.post(
            "/api/tutor/grade",
            json={"question_id": question["id"], "answer": "Serial lactate."},
        )
        assert graded.status_code == 200
        assert graded.json()["refused"] == "not_eligible"
        assert graded.json()["attempt"] is None
        assert client.get("/api/tutor/history").json() == []

    def test_a_patient_specific_answer_gets_the_educational_boundary(
        self, client: TestClient, turns: ScriptedTurns
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        build(client, pile["id"])
        question = client.get("/api/tutor/next").json()["question"]

        graded = client.post(
            "/api/tutor/grade",
            json={
                "question_id": question["id"],
                "answer": "My patient is in shock now, should I give noradrenaline?",
            },
        )
        assert graded.status_code == 200
        body = graded.json()
        assert body["refused"] == "patient_specific"
        assert "educational" in body["message"].lower()
        assert body["attempt"] is None
        # Nothing was sent for it.
        assert turns.prompts("grading") == []

    def test_self_assessment_is_recorded_as_self_assessed(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        build(client, pile["id"])
        question = client.get("/api/tutor/next").json()["question"]

        recorded = client.post(
            "/api/tutor/self-assess",
            json={
                "question_id": question["id"],
                "answer": "Serial lactate.",
                "outcome": "correct",
            },
        ).json()
        assert recorded["attempt"]["outcome"] == "self_assessed"
        assert recorded["attempt"]["graded_by"] == "self"
        assert "did not grade" in recorded["attempt"]["uncertainty"].lower()

    def test_reveal_shows_the_reference_without_transmitting(
        self, client: TestClient, turns: ScriptedTurns
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        build(client, pile["id"])
        question = client.get("/api/tutor/next").json()["question"]

        revealed = client.post(
            "/api/tutor/reveal", json={"question_id": question["id"]}
        ).json()
        assert revealed["question"]["reference_answer"]
        assert revealed["question"]["anchors"][0]["display_name"] == "lecture.txt"
        assert turns.prompts("grading") == []


class TestUploadHandling:
    def test_several_files_each_succeed_or_fail_on_their_own(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Mixed", "tier": "low"}).json()
        response = client.post(
            f"/api/piles/{pile['id']}/sources",
            files=[
                ("files", ("good.txt", LECTURE.encode(), "text/plain")),
                ("files", ("deck.ppt", b"old binary powerpoint", "application/mspowerpoint")),
                ("files", ("renamed.pdf", b"PK\x03\x04not really a pdf", "application/pdf")),
            ],
            data={"confidence": "low"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["accepted"] == 1
        assert body["rejected"] == 2
        by_name = {entry["filename"]: entry for entry in body["results"]}
        assert by_name["good.txt"]["outcome"] == "stored"
        assert ".pptx" in by_name["deck.ppt"]["message"]
        assert "renamed" in by_name["renamed.pdf"]["message"].lower()

    def test_reuploading_the_same_bytes_is_a_duplicate(self, client: TestClient) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        again = upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        entry = again.json()["results"][0]
        assert entry["outcome"] == "duplicate"
        assert "nothing changed" in entry["message"].lower()

    def test_a_pile_holding_sources_refuses_to_be_deleted(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        refused = client.delete(f"/api/piles/{pile['id']}")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "pile_in_use"
        assert client.get(f"/api/piles/{pile['id']}/sources").json()


class TestRecheckRecovery:
    def test_a_fully_covered_pile_can_be_reopened_over_http(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())
        build(client, pile["id"])

        exhausted = client.get(f"/api/piles/{pile['id']}/build/preview").json()
        assert exhausted["excerpt_count"] == 0
        assert "already been through a build" in exhausted["blocked_reason"]

        reopened = client.post(f"/api/piles/{pile['id']}/recheck").json()
        assert reopened["segments_reopened"] >= 1
        assert "Nothing was released" in reopened["note"]

        fresh = client.get(f"/api/piles/{pile['id']}/build/preview").json()
        assert fresh["excerpt_count"] >= 1
        assert fresh["blocked_reason"] == ""


class TestBackupBundle:
    def test_a_backup_carries_the_database_and_the_originals(
        self, client: TestClient, data_dir: Path
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())

        backup = client.post("/api/backup")
        assert backup.status_code == 201
        detail = backup.json()
        assert detail["attachments_included"] == 1
        assert detail["attachments_missing"] == 0
        assert detail["restorable"] is True

        bundle = data_dir / "backups" / detail["filename"]
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        assert "vademecum.sqlite3" in names
        assert any(name.startswith("attachments/sources/") for name in names)

        verified = client.post(f"/api/backup/{detail['filename']}/verify").json()
        assert verified["ok"] is True
        assert verified["attachments_verified"] == 1
        assert verified["problems"] == []

    def test_an_export_lists_the_attachments_with_their_digests(
        self, client: TestClient
    ) -> None:
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        upload(client, pile["id"], "lecture.txt", LECTURE.encode())

        exported = client.post("/api/export").json()
        assert exported["attachments"] == 1
        assert exported["counts"]["sources"] == 1
        assert exported["counts"]["source_segments"] >= 1
