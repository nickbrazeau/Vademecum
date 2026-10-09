"""The Today cover sheet: honest about what it does not have.

Today now shows *generated learning points*, not raw uploaded text. Presenting
a plain-text note as though it had been through synthesis and verification is
the exact confusion the support labels exist to prevent, so the cover sheet
stays empty until a build has produced something.
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from vademecum.storage import jobs, learning, overview, piles


def _built_point(connection: sqlite3.Connection, *, claim: str) -> str:
    """A point written directly, standing in for a completed build."""
    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    run = jobs.start_run(
        connection, pile_id=pile.id, source_count=0, excerpt_count=0, excerpt_chars=0
    )
    point_id, _ = learning.upsert_point(
        connection,
        pile_id=pile.id,
        generation_id=run.id,
        draft=learning.DraftPoint(claim=claim, detail="", topics=(), citations=()),
    )
    return point_id


def test_an_empty_workspace_says_so_rather_than_inventing_anything(
    client: TestClient,
) -> None:
    body = client.get("/api/today").json()
    assert body["recall"] == {"card": None, "unit": None}
    assert body["recent_flags"] == []
    assert body["open_flag_count"] == 0
    assert body["sources"]["total"] == 0


def test_the_literature_section_explains_an_empty_watch(client: TestClient) -> None:
    body = client.get("/api/today").json()
    assert body["literature"]["updates"] == []
    assert body["literature"]["unread"] == 0
    assert "no literature topics" in body["literature"]["message"].lower()


def test_tutor_reports_an_empty_bank_honestly(client: TestClient) -> None:
    body = client.get("/api/today").json()
    assert body["tutor"]["eligible"] == 0
    assert body["tutor"]["message"]
    # Nothing that reads as a queue to clear.
    assert "due" not in body["tutor"]["message"].lower()


def test_raw_plain_text_notes_are_not_presented_as_learning_points(
    client: TestClient,
) -> None:
    """The old cover sheet showed learning_items directly. It must not."""
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    client.post(f"/api/piles/{pile['id']}/items", json={"title": "Lactate clearance"})

    body = client.get("/api/today").json()
    assert body["recall"]["card"] is None


def test_a_built_point_appears_with_its_support_stated(
    connection: sqlite3.Connection,
) -> None:
    _built_point(connection, claim="Lactate clearance guides resuscitation")
    sheet = overview.cover_sheet(connection)

    # A point with no citations settles as uncertain and is held. That is the honest
    # outcome, not a bug.
    assert sheet["recall"]["card"] is None
    assert sheet["held"]["points"] == 1


def test_confidences_are_reported_as_source_confidence_with_labels(
    client: TestClient,
) -> None:
    client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"})
    body = client.get("/api/today").json()
    labels = {entry["confidence"]: entry["label"] for entry in body["confidences"]}
    assert labels == {"low": "Low", "mid": "Medium", "high": "High"}
    meanings = " ".join(entry["meaning"] for entry in body["confidences"]).lower()
    # Confidence is about the material, never about mastery or verification.
    for forbidden in ("mastery", "difficulty", "priority", "how well you know"):
        assert forbidden not in meanings


def test_the_cover_sheet_names_no_quota_or_streak(client: TestClient) -> None:
    body = client.get("/api/today").text.lower()
    for forbidden in ("streak", "due count", "review queue", "items due"):
        assert forbidden not in body
