"""Multi tenancy (ADR 0010): a workspace per learner, isolated by construction.

The application runs with ``tenancy="multi"`` and host mode, a static resolver
standing in for the MCP server's access store. Learner A does real work --
piles, sources, flags, a build with pending turns -- and learner B can see
none of it, through any route, by any id.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider
from test_end_to_end import ABSTRACT, LECTURE, SYNTHESIS
from vademecum.app import create_app
from vademecum.config import ConfigError, Settings
from vademecum.tenancy import LEARNERS_DIRNAME, TOKEN_HEADER, StaticResolver

TOKENS = {"token-for-a": "lrn_a", "token-for-b": "lrn_b"}


@pytest.fixture()
def multi_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=8765,
        model_provider="host",
        tenancy="multi",
    )


@pytest.fixture()
def resolver() -> StaticResolver:
    return StaticResolver(TOKENS)


@pytest.fixture()
def provider() -> FakeProvider:
    return FakeProvider(
        articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)]
    )


@pytest.fixture()
def multi(multi_settings: Settings, resolver: StaticResolver, provider: FakeProvider):
    app = create_app(
        multi_settings,
        transport_factory=refusing_factory(),
        provider_factory=lambda: provider,
        token_resolver=resolver,
    )
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        yield client


def as_learner(token: str) -> dict[str, str]:
    return {TOKEN_HEADER: token}


A = as_learner("token-for-a")
B = as_learner("token-for-b")


def seed_a(client: TestClient) -> dict:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}, headers=A).json()
    uploaded = client.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("lecture.txt", LECTURE.encode(), "text/plain"))],
        data={"confidence": "mid"},
        headers=A,
    ).json()
    flag = client.post("/api/flags", json={"text": "Unsure about vasopressors"}, headers=A).json()
    preview = client.get(f"/api/piles/{pile['id']}/build/preview", headers=A).json()
    started = client.post(
        f"/api/piles/{pile['id']}/build",
        json={"batch_id": preview["batch_id"], "selection_hash": preview["selection_hash"]},
        headers=A,
    ).json()
    return {
        "pile": pile,
        "source": uploaded["results"][0]["source"],
        "flag": flag,
        "turn": started["pending"][0],
    }


# --- identity ---------------------------------------------------------------


def test_no_token_means_nothing_is_read(multi: TestClient) -> None:
    for path in ("/api/piles", "/api/today", "/api/flags", "/api/tutor", "/api/workspace"):
        response = multi.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthenticated"
        assert response.headers["cache-control"] == "no-store"
    assert multi.post("/api/piles", json={"title": "X", "tier": "low"}).status_code == 401


def test_an_unknown_token_is_the_same_as_none(multi: TestClient) -> None:
    response = multi.get("/api/piles", headers=as_learner("token-for-nobody"))
    assert response.status_code == 401


def test_health_needs_no_token_and_opens_no_learner(multi: TestClient, multi_settings: Settings) -> None:
    health = multi.get("/api/health")
    assert health.status_code == 200
    assert health.json()["tenancy"] == "multi"
    assert health.json()["model_mode"] == "host"
    assert health.json()["schema_version"] >= 5
    learners = multi_settings.resolve_data_dir() / LEARNERS_DIRNAME
    assert learners.exists()
    assert list(learners.iterdir()) == [], "a health check creates no workspace"


def test_multi_tenancy_requires_host_mode(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Settings(data_dir=tmp_path / "d", tenancy="multi", model_provider="codex").prepare()


# --- isolation --------------------------------------------------------------


def test_each_learner_has_a_directory_and_a_database_of_their_own(
    multi: TestClient, multi_settings: Settings
) -> None:
    seed_a(multi)
    multi.post("/api/piles", json={"title": "Cardiology", "tier": "high"}, headers=B)
    root = multi_settings.resolve_data_dir() / LEARNERS_DIRNAME
    assert sorted(path.name for path in root.iterdir()) == ["lrn_a", "lrn_b"]
    for learner in ("lrn_a", "lrn_b"):
        assert (root / learner / "vademecum.sqlite3").exists()
        assert (root / learner / "attachments" / "sources").is_dir()
        assert (root / learner).stat().st_mode & 0o777 == 0o700
    assert list((root / "lrn_a" / "attachments" / "sources").iterdir()), "A's original is in A's directory"
    assert not list((root / "lrn_b" / "attachments" / "sources").iterdir())


def test_a_learner_sees_only_their_own_records(multi: TestClient) -> None:
    mine = seed_a(multi)

    assert multi.get("/api/piles", headers=B).json() == []
    assert multi.get("/api/flags", headers=B).json() == []
    assert multi.get("/api/points", headers=B).json() == []
    assert multi.get("/api/today", headers=B).json()["open_flag_count"] == 0
    assert multi.get("/api/tutor", headers=B).json()["eligible"] == 0

    # Every id A holds is nothing to B.
    assert multi.get(f"/api/piles/{mine['pile']['id']}", headers=B).status_code == 404
    assert multi.get(f"/api/piles/{mine['pile']['id']}/sources", headers=B).status_code == 404
    assert multi.get(f"/api/sources/{mine['source']['id']}", headers=B).status_code == 404
    assert multi.get(f"/api/sources/{mine['source']['id']}/segments", headers=B).status_code == 404
    assert multi.get(f"/api/flags/{mine['flag']['id']}", headers=B).status_code == 404
    assert multi.patch(f"/api/flags/{mine['flag']['id']}", json={"status": "addressed"}, headers=B).status_code == 404
    assert multi.delete(f"/api/flags/{mine['flag']['id']}", headers=B).status_code == 404
    assert multi.patch(f"/api/sources/{mine['source']['id']}", json={"excluded": True}, headers=B).status_code == 404
    assert multi.delete(f"/api/sources/{mine['source']['id']}", headers=B).status_code == 404
    assert multi.get(f"/api/piles/{mine['pile']['id']}/build/status", headers=B).status_code == 404
    assert multi.get(f"/api/piles/{mine['pile']['id']}/build/pending", headers=B).status_code == 404

    # A's pending turn cannot be answered by B, even against B's own pile.
    theirs = multi.post("/api/piles", json={"title": "Other", "tier": "low"}, headers=B).json()
    stolen = multi.post(
        f"/api/piles/{theirs['id']}/build/submit",
        json={"turn_id": mine["turn"]["turn_id"], "result": SYNTHESIS},
        headers=B,
    )
    assert stolen.status_code == 409
    assert stolen.json()["error"]["code"] == "unknown_turn"

    # And A still sees everything.
    assert [pile["id"] for pile in multi.get("/api/piles", headers=A).json()] == [mine["pile"]["id"]]
    assert multi.get(f"/api/flags/{mine['flag']['id']}", headers=A).status_code == 200
    assert multi.get(f"/api/piles/{mine['pile']['id']}/build/pending", headers=A).json()["turns"]


def test_exports_and_backups_are_per_learner(multi: TestClient, multi_settings: Settings) -> None:
    seed_a(multi)
    multi.post("/api/flags", json={"text": "B's own gap"}, headers=B)
    exported_a = multi.post("/api/export", headers=A)
    exported_b = multi.post("/api/export", headers=B)
    assert exported_a.status_code == 201 and exported_b.status_code == 201
    assert len(multi.get("/api/export", headers=A).json()) == 1
    assert len(multi.get("/api/export", headers=B).json()) == 1
    root = multi_settings.resolve_data_dir() / LEARNERS_DIRNAME
    a_export = next((root / "lrn_a" / "exports").iterdir()).read_text(encoding="utf-8")
    b_export = next((root / "lrn_b" / "exports").iterdir()).read_text(encoding="utf-8")
    assert "Unsure about vasopressors" in a_export and "B's own gap" not in a_export
    assert "B's own gap" in b_export and "Unsure about vasopressors" not in b_export
    assert multi.post("/api/backup", headers=A).status_code == 201
    assert multi.get("/api/backup", headers=B).json() == []


def test_the_owners_model_connection_is_out_of_reach(multi: TestClient) -> None:
    for path in ("/api/model/status",):
        response = multi.get(path, headers=A)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_in_this_mode"
    assert multi.post("/api/model/login", headers=A).status_code == 409


def test_no_response_carries_a_learner_id_or_a_path(multi: TestClient, multi_settings: Settings) -> None:
    mine = seed_a(multi)
    bodies = [
        multi.get("/api/piles", headers=A).text,
        multi.get(f"/api/piles/{mine['pile']['id']}", headers=A).text,
        multi.get("/api/today", headers=A).text,
        multi.get("/api/workspace", headers=A).text,
        multi.get(f"/api/piles/{mine['pile']['id']}/build/pending", headers=A).text,
        multi.post("/api/export", headers=A).text,
    ]
    for body in bodies:
        assert "lrn_a" not in body
        assert str(multi_settings.resolve_data_dir()) not in body
        assert "/Users/" not in body


# --- deletion ---------------------------------------------------------------


def test_deleting_a_workspace_removes_it_and_signs_the_assistant_out(
    multi: TestClient, multi_settings: Settings, resolver: StaticResolver
) -> None:
    seed_a(multi)
    multi.post("/api/flags", json={"text": "B's own gap"}, headers=B)
    described = multi.get("/api/workspace", headers=A).json()
    assert described["deletable"] is True
    assert "ChatGPT conversation" in described["note"]

    refused = multi.request("DELETE", "/api/workspace", json={"confirm": "yes"}, headers=A)
    assert refused.status_code == 422, "the exact confirmation phrase is required"

    deleted = multi.request("DELETE", "/api/workspace", json={"confirm": "delete everything"}, headers=A)
    assert deleted.status_code == 200, deleted.text
    body = deleted.json()
    assert body["deleted"] is True
    assert body["files_removed"] >= 2
    assert body["connections_revoked"] == 1

    root = multi_settings.resolve_data_dir() / LEARNERS_DIRNAME
    assert not (root / "lrn_a").exists()
    assert (root / "lrn_b" / "vademecum.sqlite3").exists()
    assert multi.get("/api/piles", headers=A).status_code == 401, "A's token is gone"
    assert multi.get("/api/flags", headers=B).json()[0]["text"] == "B's own gap"


def test_a_single_owner_workspace_is_not_deleted_through_the_api(client: TestClient) -> None:
    response = client.request("DELETE", "/api/workspace", json={"confirm": "delete everything"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_in_this_mode"
    assert client.get("/api/workspace").json()["deletable"] is False


def test_every_learner_shares_one_pubmed_throttle(multi_settings: Settings, resolver: StaticResolver) -> None:
    """No provider override: the real fetcher is built, once, and never used."""
    app = create_app(multi_settings, transport_factory=refusing_factory(), token_resolver=resolver)
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        assert client.get("/api/flags", headers=A).status_code == 200
        assert client.get("/api/flags", headers=B).status_code == 200
        fetcher = app.state.literature_fetcher
        assert fetcher is not None
        workspaces = app.state.workspaces
        watchers = [workspaces._open[learner].watcher for learner in ("lrn_a", "lrn_b")]  # noqa: SLF001
        assert all(watcher is not None for watcher in watchers)
        assert all(watcher._provider._fetcher is fetcher for watcher in watchers)  # noqa: SLF001


def test_single_tenancy_ignores_the_token_header(client: TestClient) -> None:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert client.get(f"/api/piles/{pile['id']}", headers=as_learner("anything")).status_code == 200
    assert client.get("/api/health").json()["tenancy"] == "single"
