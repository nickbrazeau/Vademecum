"""The source folder through the tools (ADR 0012): where it is, syncing it,
re-rating a pile, removing a source with a word, export and backup."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from mcp import Client
from tests.mcp_support import API_ORIGIN, LECTURE, call, call_expecting_error

from fake_appserver import AccountScript, ScriptedTransport, factory  # noqa: E402
from fake_model import FakeProvider  # noqa: E402
from vademecum.app import create_app  # noqa: E402
from vademecum.config import Settings as ApiSettings  # noqa: E402
from vademecum_mcp.api_client import ApiClient  # noqa: E402
from vademecum_mcp.config import McpSettings  # noqa: E402
from vademecum_mcp.server import create_server  # noqa: E402

pytestmark = pytest.mark.anyio


def drop(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    old = time.time() - 60
    os.utime(path, (old, old))
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    return tmp_path / "Vademecum"


@pytest.fixture
async def folder_client(tmp_path: Path, folder: Path, provider: FakeProvider) -> AsyncIterator[Client]:
    api_settings = ApiSettings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", sources_dir=folder)
    mcp_settings = McpSettings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)
    app = create_app(
        api_settings,
        transport_factory=factory(ScriptedTransport(responder=AccountScript())),
        provider_factory=lambda: provider,
    )
    async with app.router.lifespan_context(app):
        api = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=app))
        try:
            async with Client(create_server(api, settings=mcp_settings)) as client:
                yield client
        finally:
            await api.aclose()


async def test_the_assistant_can_say_where_the_folder_is(folder_client: Client, folder: Path) -> None:
    where = await call(folder_client, "sources_folder", {})
    assert where["folder"] == str(folder)
    assert where["piles"] == str(folder / "piles")
    assert "highconfidence" in where["layout"]
    assert "patient" in where["note"]
    assert (folder / "piles" / "highconfidence").is_dir(), "startup laid the folder out"


async def test_sync_brings_files_in_and_the_pile_is_rated_by_its_tier(folder_client: Client, folder: Path) -> None:
    drop(folder / "piles" / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE.encode())
    report = await call(folder_client, "sync_sources", {})
    assert report["piles_created"] == [{"pile": "Sepsis", "confidence": "high"}]
    assert [entry["filename"] for entry in report["stored"]] == ["lecture.txt"]
    piles = await call(folder_client, "list_piles", {})
    sepsis = piles["items"][0]
    assert sepsis["title"] == "Sepsis" and sepsis["tier"] == "high"
    preview = await call(folder_client, "build_preview", {"pile_id": sepsis["id"]})
    assert preview["blocked_reason"] == ""
    again = await call(folder_client, "sync_sources", {})
    assert again["stored"] == [] and again["already_present"] == 1


async def test_update_pile_and_remove_source(folder_client: Client, folder: Path) -> None:
    drop(folder / "piles" / "mediumconfidence" / "ICU" / "notes.txt", LECTURE.encode())
    await call(folder_client, "sync_sources", {})
    pile = (await call(folder_client, "list_piles", {}))["items"][0]
    updated = await call(folder_client, "update_pile", {"pile_id": pile["id"], "confidence": "low", "description": "Course notes"})
    assert updated["tier"] == "low" and updated["description"] == "Course notes"
    detail = await call(folder_client, "get_pile", {"pile_id": pile["id"]})
    source_id = detail["sources"][0]["id"]
    message = await call_expecting_error(folder_client, "remove_source", {"source_id": source_id, "confirm": "yes"})
    assert "remove" in message.lower()
    removed = await call(folder_client, "remove_source", {"source_id": source_id, "confirm": "remove"})
    assert removed["deleted"] == source_id
    assert (await call(folder_client, "get_pile", {"pile_id": pile["id"]}))["sources"] == []


async def test_export_and_backup_return_names_not_paths(folder_client: Client, folder: Path) -> None:
    drop(folder / "piles" / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE.encode())
    await call(folder_client, "sync_sources", {})
    exported = await call(folder_client, "export_workspace", {})
    backed = await call(folder_client, "backup_workspace", {})
    for result in (exported, backed):
        assert result["filename"] and result["directory"] in {"exports", "backups"}
        assert "/" not in result["filename"]
        assert "/Users/" not in str(result)


async def test_without_a_folder_the_tools_say_so(mcp_client: Client) -> None:
    where = await call(mcp_client, "sources_folder", {})
    assert where["folder"] is None
    assert "web app" in where["note"]
