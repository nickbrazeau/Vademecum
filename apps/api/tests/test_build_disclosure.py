"""The preview names every kind of content the build can transmit."""

from fastapi.testclient import TestClient


def test_preview_discloses_metadata_and_follow_up_checks(client: TestClient) -> None:
    pile = client.post("/api/piles", json={"title": "Study", "tier": "high"}).json()
    response = client.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("study-note.txt", b"A local source excerpt for study.", "text/plain"))],
        data={"confidence": "low"},
    )
    assert response.status_code == 201
    preview = client.get(f"/api/piles/{pile['id']}/build/preview")
    assert preview.status_code == 200
    data = preview.json()
    disclosure = " ".join(data["disclosure"]["bullets"]).lower()
    for term in (
        "filename", "source-confidence", "location", "claim", "context",
        "abstract", "question", "reference answer", "rubric", "source passages",
        "pubmed", "no learner answers",
    ):
        assert term in disclosure, term
    assert "OpenAI" in data["disclosure"]["destination"]
    assert "ChatGPT" in data["disclosure"]["destination"]
    # A preview is local preparation, not consent to start a model turn.
    assert client.get("/api/runs").json() == []
