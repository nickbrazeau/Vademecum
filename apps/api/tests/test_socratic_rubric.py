"""Socratic grading made transparent, and sessions about a flagged gap (feedback of 12 October).

Each area is scored 0-4 against the same anchors, on the learner's own words: a quote that is
not in their answers does not count, an area never asked or answered is not graded, and the
total is added up here, never taken from the model."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from test_socratic_podcasts import _page
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.prompts import socratic_prompt
from vademecum.storage import socratic as sessions

TRANSCRIPT = [
    {"role": "tutor", "text": "What is on your differential?"},
    {"role": "learner", "text": "She doesn't have a coagulation deficiency. She had early pregnancy loss, so you think about APLS."},
    {"role": "tutor", "text": "Initial anticoagulant?"},
    {"role": "learner", "text": "Warfarin. Cause that's what the guidelines say"},
]


def test_scores_stand_only_on_the_learners_own_words() -> None:
    checked = sessions.check_rubric(
        [
            {"domain": "differential", "score": 2, "evidence": "she had early pregnancy loss ... think about APLS", "full_credit": "Name LA testing and persistence.", "reason": "Recognised APS; overcalled."},
            {"domain": "treatment", "score": 4, "evidence": "Start rivaroxaban at once", "full_credit": "x", "reason": "y"},
            {"domain": "knowledge", "score": -1, "evidence": "", "full_credit": "Mechanism of the mixing study.", "reason": ""},
            {"domain": "knowledge", "score": 4, "evidence": "Warfarin", "full_credit": "", "reason": "a second knowledge row is ignored"},
        ],
        TRANSCRIPT,
    )
    by = {row["domain"]: row for row in checked["rubric"]}
    assert by["differential"]["score"] == 2 and "APLS" in by["differential"]["evidence"], "an ellipsis joins two of their own phrases"
    assert by["treatment"]["score"] is None and "not the learner's" in by["treatment"]["reason"], "words they never said grade nothing"
    assert by["knowledge"]["score"] is None and "not asked" in by["knowledge"]["reason"]
    assert by["knowledge"]["full_credit"] == "Mechanism of the mixing study.", "what full credit needed is still shown"
    assert (checked["points"], checked["out_of"]) == (2, 4), "only graded areas count, added up here"
    assert [row["domain"] for row in checked["rubric"]] == ["differential", "knowledge", "treatment"]
    assert checked["scale"][4] == "complete and accurate"


def test_no_rubric_means_nothing_graded_not_zero() -> None:
    checked = sessions.check_assessment({"summary": "Old style."}, TRANSCRIPT)
    assert checked["out_of"] == 0 and all(row["score"] is None for row in checked["rubric"])


def _app(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, model_provider="host")
    return create_app(settings, transport_factory=refusing_factory())


def test_a_chat_host_finishes_with_a_rubric_that_is_checked_and_totalled(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect

        connection = connect(app.state.database_path)
        try:
            _page(connection)
        finally:
            connection.close()
        session = c.post("/api/socratic", json={}).json()["session"]
        c.post(f"/api/socratic/{session['id']}/turn", json={"question": "Differential?", "answer": "Sepsis, then pancreatitis.", "probe": "differential"})
        done = c.post(
            f"/api/socratic/{session['id']}/finish",
            json={
                "assessment": {
                    "differential": "Narrow.", "treatment": "", "knowledge_strengths": "", "knowledge_gaps": [], "summary": "Short.",
                    "rubric": [{"domain": "differential", "score": 3, "evidence": "Sepsis, then pancreatitis", "full_credit": "Add adrenal crisis.", "reason": "Two of three."}],
                    "overall": "10/10",
                }
            },
        ).json()["session"]["assessment"]
        assert done["points"] == 3 and done["out_of"] == 4 and "overall" not in done, "the model's own total is dropped"
        assert done["rubric"][0]["evidence"] == "Sepsis, then pancreatitis"


def test_a_session_can_be_about_a_gap_the_learner_flagged(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect

        connection = connect(app.state.database_path)
        try:
            page = _page(connection, topic="Lactate in sepsis")
        finally:
            connection.close()
        flag = c.post("/api/flags", json={"text": "Not sure when lactate clearance changes management", "topic": "Lactate in sepsis"}).json()
        started = c.post("/api/socratic", json={"flag_id": flag["id"]}).json()["session"]
        assert started["focus_flag_id"] == flag["id"] and started["focus"].startswith("Not sure when lactate")
        assert started["title"].startswith("Gap: ") and started["entry_id"] == page.id, "grounded on the page on its topic"
        material = c.get(f"/api/socratic/{started['id']}/material").json()
        assert material["focus"] == flag["text"] and "flagged" in material["rules"]

        loose = c.post("/api/flags", json={"text": "What does a positive Coombs mean in a newborn?"}).json()
        unfiled = c.post("/api/socratic", json={"flag_id": loose["id"]}).json()["session"]
        assert unfiled["entry_id"] is None and unfiled["focus"].startswith("What does a positive Coombs"), "a gap with no page still makes a session"


def test_the_tutor_is_told_the_gap_in_the_learners_words() -> None:
    prompt = socratic_prompt("", [], 0, focus="Not sure when lactate clearance changes management")
    assert "THE GAP THE LEARNER FLAGGED" in prompt and "open on the gap" in prompt and "(no page on this yet)" in prompt
