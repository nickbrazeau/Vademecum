"""The HTTP boundary: validation, status codes, cache headers, connections."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vademecum.api import deps


def make_pile(client: TestClient, **overrides: object) -> dict:
    payload = {"title": "Sepsis", "tier": "mid"} | overrides
    response = client.post("/api/piles", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_health_reports_the_schema_version_and_the_bind(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["schema_version"] >= 1
    assert body["loopback_only"] is True
    # Model turns exist now, and are reachable from Build and Grade. The flag
    # says the capability is wired, not that anything has been sent.
    assert body["model_calls_configured"] is True
    assert body["model_bridge_configured"] is True


def test_api_responses_are_never_cacheable(client: TestClient) -> None:
    for path in ("/api/health", "/api/today", "/api/piles", "/api/flags"):
        response = client.get(path)
        assert response.headers["cache-control"] == "no-store", path


def test_every_response_carries_a_correlation_id(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.headers["X-Correlation-Id"]


def test_pile_crud(client: TestClient) -> None:
    pile = make_pile(client, title="Antimicrobial stewardship", tier="high")
    assert client.get(f"/api/piles/{pile['id']}").json()["title"] == "Antimicrobial stewardship"

    patched = client.patch(f"/api/piles/{pile['id']}", json={"tier": "low"})
    assert patched.status_code == 200
    assert patched.json()["tier"] == "low"

    assert client.delete(f"/api/piles/{pile['id']}").status_code == 204
    assert client.get(f"/api/piles/{pile['id']}").status_code == 404


def test_unknown_records_are_404_with_a_readable_body(client: TestClient) -> None:
    response = client.get("/api/piles/pil_nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["correlation_id"]


def test_pile_http_responses_preserve_source_counts_and_character_coverage(
    client: TestClient,
) -> None:
    pile = make_pile(client, title="Study methods", tier="mid")
    assert pile["confidence_label"] == "Medium"
    assert pile["source_count"] == pile["point_count"] == pile["question_count"] == 0
    assert pile["coverage"]["complete"] is False
    text = b"Attempt recall before reading the answer."
    upload = client.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("study.txt", text, "text/plain"))],
        data={"confidence": "high"},
    )
    assert upload.status_code == 201, upload.text
    assert client.post(
        f"/api/piles/{pile['id']}/items", json={"title": "Recall note"}
    ).status_code == 201
    expected = client.get(f"/api/piles/{pile['id']}/build/status").json()["coverage"]
    responses = (
        client.get("/api/piles").json()[0],
        client.get(f"/api/piles/{pile['id']}").json(),
        client.patch(f"/api/piles/{pile['id']}", json={"title": "Updated study"}).json(),
    )
    for response in responses:
        assert response["source_count"] == response["item_count"] == 1
        assert response["point_count"] == response["question_count"] == 0
        assert response["coverage"]["chars_total"] == len(text)
        assert response["coverage"]["chars_covered"] == 0
        assert {key: response["coverage"][key] for key in expected} == expected


def test_an_unsupported_tier_is_rejected(client: TestClient) -> None:
    response = client.post("/api/piles", json={"title": "X", "tier": "urgent"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_blank_and_whitespace_only_titles_are_rejected(client: TestClient) -> None:
    for title in ("", "   ", "\n\t"):
        response = client.post("/api/piles", json={"title": title, "tier": "low"})
        assert response.status_code == 422, title


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/piles", json={"title": "X", "tier": "low", "sendToModel": True}
    )
    assert response.status_code == 422


def test_over_long_text_is_rejected(client: TestClient) -> None:
    response = client.post("/api/flags", json={"text": "x" * 4001})
    assert response.status_code == 422


def test_items_live_under_their_pile(client: TestClient) -> None:
    pile = make_pile(client)
    created = client.post(
        f"/api/piles/{pile['id']}/items",
        json={"title": "Lactate clearance", "body": "notes", "source": "Grand rounds"},
    )
    assert created.status_code == 201
    item = created.json()
    listed = client.get(f"/api/piles/{pile['id']}/items").json()
    assert [entry["id"] for entry in listed] == [item["id"]]
    assert client.delete(f"/api/items/{item['id']}").status_code == 204


def test_items_for_an_unknown_pile_are_404_not_an_empty_list(client: TestClient) -> None:
    assert client.get("/api/piles/pil_nope/items").status_code == 404


def test_a_flag_takes_one_field(client: TestClient) -> None:
    response = client.post("/api/flags", json={"text": "Had to check the 4T score"})
    assert response.status_code == 201
    flag = response.json()
    assert flag["status"] == "open"
    assert flag["topic"] is None


def test_flags_can_be_filtered_by_status(client: TestClient) -> None:
    open_flag = client.post("/api/flags", json={"text": "One"}).json()
    other = client.post("/api/flags", json={"text": "Two"}).json()
    client.patch(f"/api/flags/{other['id']}", json={"status": "addressed"})

    still_open = client.get("/api/flags", params={"status": "open"}).json()
    assert [flag["id"] for flag in still_open] == [open_flag["id"]]
    assert len(client.get("/api/flags").json()) == 2


def test_the_improvement_map_groups_flags_by_topic(client: TestClient) -> None:
    client.post("/api/flags", json={"text": "One", "topic": "Nephrology"})
    client.post("/api/flags", json={"text": "Two", "topic": "Nephrology"})
    client.post("/api/flags", json={"text": "Three"})

    body = client.get("/api/improvement-map").json()
    by_topic = {entry["topic"]: entry for entry in body["topics"]}
    assert by_topic["Nephrology"]["open_flags"] == 2
    assert body["unfiled_flag_count"] == 1
    # Tiers are now reported as source *confidence*, with their labels.
    assert [entry["confidence"] for entry in body["confidences"]] == ["low", "mid", "high"]
    assert [entry["label"] for entry in body["confidences"]] == ["Low", "Medium", "High"]
    # Unfiled sorts last: a named topic is never buried under what the system
    # has not filed yet.
    assert body["topics"][-1]["topic"] is None


def test_the_improvement_map_links_topics_the_same_point_carries(
    client: TestClient, connection: sqlite3.Connection
) -> None:
    """A link is a fact about the material: one generated point, two topics.

    Flags alone never link anything, and the cluster is the pile the points came
    from -- the only grouping this version can honestly claim.
    """
    pile = client.post("/api/piles", json={"title": "Flu pile", "tier": "high"}).json()
    other = client.post("/api/piles", json={"title": "Other pile", "tier": "mid"}).json()
    client.post("/api/flags", json={"text": "One", "topic": "Influenza"})
    client.post("/api/flags", json={"text": "Two", "topic": "Pneumonia"})
    client.post("/api/flags", json={"text": "Three", "topic": "Nephrology"})

    def point(point_id: str, pile_id: str, *topics: str) -> None:
        connection.execute(
            "INSERT INTO learning_points (id, pile_id, claim, detail, support,"
            " evidence_grade, review_state, held, hold_reason, content_hash,"
            " created_at, updated_at) VALUES (?, ?, ?, '', 'source_supported',"
            " 'none', 'machine_reviewed', 1, 'test', ?, 'now', 'now')",
            (point_id, pile_id, f"claim {point_id}", f"hash-{point_id}"),
        )
        for topic in topics:
            connection.execute(
                "INSERT INTO learning_point_topics (learning_point_id, topic) VALUES (?, ?)",
                (point_id, topic),
            )

    point("lpt_1", pile["id"], "Influenza", "Pneumonia")
    point("lpt_2", pile["id"], "Influenza", "Pneumonia", "Antivirals")
    point("lpt_3", other["id"], "Influenza")
    connection.commit()

    body = client.get("/api/improvement-map").json()

    links = {(link["a"], link["b"]): link["weight"] for link in body["links"]}
    assert links[("Influenza", "Pneumonia")] == 2
    assert links[("Antivirals", "Influenza")] == 1
    assert links[("Antivirals", "Pneumonia")] == 1
    # Nephrology was only ever flagged; nothing links to it.
    assert not any("Nephrology" in pair for pair in links)
    # Every pair is stored once, ordered a < b.
    assert all(a < b for a, b in links)

    by_topic = {entry["topic"]: entry for entry in body["topics"]}
    assert by_topic["Influenza"]["cluster"] == {
        "id": pile["id"],
        "title": "Flu pile",
        "tier": "high",
    }
    assert by_topic["Nephrology"]["cluster"] is None
    covered = {entry["topic"]: entry for entry in body["covered_topics"]}
    assert covered["Antivirals"]["cluster"]["title"] == "Flu pile"
    assert covered["Influenza"]["point_count"] == 3


def test_the_improvement_map_carries_specialties_and_remembers_positions(
    client: TestClient,
) -> None:
    """A specialty is the owner's call; a name match is offered, marked, never stored.

    Positions are the whole layout: sending a list replaces it, and a topic left
    out is forgotten rather than kept forever.
    """
    client.post("/api/flags", json={"text": "One", "topic": "Nephrology"})
    client.post("/api/flags", json={"text": "Two", "topic": "renal"})
    client.post("/api/flags", json={"text": "Three", "topic": "Influenza"})
    client.post("/api/flags", json={"text": "Four"})

    body = client.get("/api/improvement-map").json()
    assert [entry["id"] for entry in body["specialties"]][:3] == [
        "general-internal-medicine",
        "cardiology",
        "pulmonology",
    ]
    assert len(body["specialties"]) == 14
    by_topic = {entry["topic"]: entry for entry in body["topics"]}
    # A topic literally named for a specialty, or its shorthand, is matched by name.
    assert by_topic["Nephrology"]["specialty"] == {
        "id": "nephrology",
        "name": "Nephrology",
        "assigned_by": "name",
    }
    assert by_topic["renal"]["specialty"]["id"] == "nephrology"
    assert by_topic["Influenza"]["specialty"] is None
    assert by_topic[None]["specialty"] is None
    assert body["positions"] == []

    # The owner files Influenza under infectious disease.
    response = client.put(
        "/api/improvement-map/topics/specialty",
        json={"topic": "Influenza", "specialty_id": "infectious-disease"},
    )
    assert response.status_code == 200
    assert response.json()["specialty"]["assigned_by"] == "owner"
    body = client.get("/api/improvement-map").json()
    by_topic = {entry["topic"]: entry for entry in body["topics"]}
    assert by_topic["Influenza"]["specialty"]["name"] == "Infectious Disease"

    # An owner's call overrides a name match, and can be cleared.
    client.put(
        "/api/improvement-map/topics/specialty",
        json={"topic": "Nephrology", "specialty_id": "cardiology"},
    )
    body = client.get("/api/improvement-map").json()
    assert {e["topic"]: e for e in body["topics"]}["Nephrology"]["specialty"]["id"] == "cardiology"
    client.put(
        "/api/improvement-map/topics/specialty",
        json={"topic": "Nephrology", "specialty_id": None},
    )
    body = client.get("/api/improvement-map").json()
    assert {e["topic"]: e for e in body["topics"]}["Nephrology"]["specialty"]["assigned_by"] == "name"

    # Unknown specialties and unknown fields are refused.
    assert client.put(
        "/api/improvement-map/topics/specialty",
        json={"topic": "Influenza", "specialty_id": "astrology"},
    ).status_code == 404
    assert client.put(
        "/api/improvement-map/topics/specialty",
        json={"topic": "Influenza", "specialty_id": "cardiology", "colour": "red"},
    ).status_code == 422

    # Positions: the list sent is the layout kept.
    saved = client.put(
        "/api/improvement-map/positions",
        json={"positions": [
            {"topic": "Influenza", "x": 120.5, "y": 80},
            {"topic": "Nephrology", "x": 300, "y": 200},
        ]},
    )
    assert saved.status_code == 200 and saved.json() == {"saved": 2}
    assert client.get("/api/improvement-map").json()["positions"] == [
        {"topic": "Influenza", "x": 120.5, "y": 80.0},
        {"topic": "Nephrology", "x": 300.0, "y": 200.0},
    ]
    client.put(
        "/api/improvement-map/positions",
        json={"positions": [{"topic": "Influenza", "x": 130, "y": 90}]},
    )
    assert client.get("/api/improvement-map").json()["positions"] == [
        {"topic": "Influenza", "x": 130.0, "y": 90.0}
    ]


def test_the_app_keeps_a_database_path_and_no_shared_connection(
    client: TestClient,
) -> None:
    """A sqlite3.Connection carries transaction state; it is not app state.

    FastAPI runs these synchronous endpoints on a thread pool. One connection
    on ``app.state`` would put two requests' transactions in the same place.
    """
    state = client.app.state  # type: ignore[attr-defined]
    assert isinstance(state.database_path, Path)
    assert state.database_path.name == "vademecum.sqlite3"
    assert not hasattr(state, "connection")


def test_every_request_opens_and_closes_its_own_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = deps.connect

    def spy(path: object) -> sqlite3.Connection:
        connection = real_connect(path)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    monkeypatch.setattr(deps, "connect", spy)

    assert client.get("/api/piles").status_code == 200
    assert client.post("/api/piles", json={"title": "A", "tier": "low"}).status_code == 201

    assert len(opened) == 2
    assert opened[0] is not opened[1]
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


def test_a_request_that_fails_still_closes_its_connection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = deps.connect

    def spy(path: object) -> sqlite3.Connection:
        connection = real_connect(path)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    monkeypatch.setattr(deps, "connect", spy)
    assert client.get("/api/piles/pil_nope").status_code == 404

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_concurrent_writes_each_land_exactly_once(client: TestClient) -> None:
    """Eight overlapping writes, one row each, no interleaved transaction."""
    titles = [f"Pile {index}" for index in range(8)]

    def create(title: str) -> int:
        return client.post("/api/piles", json={"title": title, "tier": "mid"}).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(create, titles))

    assert statuses == [201] * 8
    listed = client.get("/api/piles").json()
    assert sorted(pile["title"] for pile in listed) == sorted(titles)
