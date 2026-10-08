"""ADR 0008's boundary as tests: one loopback client, no provider, no paths,
no free text in the logs, and the documents describing all of it."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from tests.mcp_support import LECTURE, REPO_ROOT, call
from mcp import Client

from vademecum.config import ConfigError
from vademecum_mcp.api_client import ApiClient
from vademecum_mcp.config import McpSettings

pytestmark = pytest.mark.anyio

MCP_SOURCE = REPO_ROOT / "apps" / "mcp" / "src" / "vademecum_mcp"
MARKER = "ZZQXMARKERZZ-particular-clinical-detail"


def _sources() -> list[Path]:
    return sorted(MCP_SOURCE.rglob("*.py"))


def test_the_api_client_refuses_anything_but_loopback() -> None:
    for url in ("http://10.0.0.5:8765", "https://api.example.com", "http://vademecum.local:8765"):
        with pytest.raises(ConfigError):
            ApiClient(url, timeout=1)


def test_the_public_url_must_be_https_or_loopback() -> None:
    McpSettings(mcp_public_url="https://my-mac.tail.ts.net").resolve_public_url()
    McpSettings(mcp_public_url="http://127.0.0.1:8766/").resolve_public_url()
    for bad in ("http://my-mac.tail.ts.net", "https://x.example/mcp", "https://x.example/?a=1", ""):
        with pytest.raises(ConfigError):
            McpSettings(mcp_public_url=bad).resolve_public_url()


def test_the_package_names_no_provider_endpoint_and_no_key() -> None:
    forbidden = ("openai.com", "anthropic.com", "api_key", "apikey", "sk-", "embedding")
    offenders = []
    for path in _sources():
        text = path.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == [], offenders


def test_the_only_http_client_lives_in_one_file() -> None:
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        if path.name == "api_client.py":
            assert "import httpx" in text
            continue
        for needle in ("import httpx", "import requests", "import urllib.request", "import aiohttp"):
            assert needle not in text, f"{path.name} imports an HTTP client: {needle}"


async def test_no_tool_result_carries_a_filesystem_path(mcp_client: Client, mcp_settings) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    await call(
        mcp_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )
    await call(mcp_client, "flag_knowledge_gap", {"text": "Unsure about vasopressors"})
    bodies = [
        await call(mcp_client, "get_today", {}),
        await call(mcp_client, "list_piles", {}),
        await call(mcp_client, "get_pile", {"pile_id": pile["id"]}),
        await call(mcp_client, "build_preview", {"pile_id": pile["id"]}),
        await call(mcp_client, "tutor_overview", {}),
        await call(mcp_client, "literature_topics", {}),
        await call(mcp_client, "get_model_status", {}),
        await call(mcp_client, "get_improvement_map", {}),
    ]
    data_dir = str(mcp_settings.resolve_data_dir())
    for body in bodies:
        text = json.dumps(body)
        assert data_dir not in text
        assert "/Users/" not in text


async def test_free_text_never_reaches_the_logs(
    mcp_client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        flag = await call(mcp_client, "flag_knowledge_gap", {"text": MARKER, "topic": MARKER})
        await call(mcp_client, "list_flags", {})
        await call(mcp_client, "update_flag", {"flag_id": flag["id"], "text": MARKER + " edited"})
        pile = await call(mcp_client, "create_pile", {"title": MARKER, "confidence": "low"})
        await call(
            mcp_client,
            "add_text_source",
            {"pile_id": pile["id"], "title": "Note", "text": MARKER, "confidence": "low"},
        )
        await call(mcp_client, "get_today", {})
    assert MARKER not in caplog.text


async def test_a_refused_call_logs_no_content_either(
    mcp_client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected value is not logged by either process, and the failure line
    the SDK writes carries only the API's own sentence."""
    with caplog.at_level(logging.DEBUG):
        # Passes the tool's own schema, refused by the API: a blank title.
        result = await mcp_client.call_tool(
            "create_pile", {"title": " ", "confidence": "mid", "description": MARKER}
        )
        # Refused by the SDK before the API sees it: over the length bound.
        rejected = await mcp_client.call_tool("flag_knowledge_gap", {"text": MARKER * 400})
    assert result.is_error and rejected.is_error
    assert MARKER not in caplog.text
    assert "http_request" in caplog.text, "the API still logs the request's shape"


async def test_request_urls_are_not_logged_by_the_http_client(
    mcp_client: Client, caplog: pytest.LogCaptureFixture
) -> None:
    """httpx would otherwise write every URL, query string included, at INFO."""
    with caplog.at_level(logging.DEBUG):
        await mcp_client.call_tool("list_flags", {"status": "open", "limit": 7})
    assert "HTTP Request:" not in caplog.text
    assert "limit=7" not in caplog.text


# --- the documents ------------------------------------------------------------

ADR = REPO_ROOT / "docs" / "adr" / "0008-mcp-server-for-chat-hosts.md"
README = REPO_ROOT / "docs" / "technical-overview.md"
AGENTS = REPO_ROOT / "AGENTS.md"


def test_the_adr_exists_and_states_the_new_transmission_surface() -> None:
    text = ADR.read_text(encoding="utf-8").lower()
    assert "tool result" in text
    assert "host" in text and "provider" in text
    assert "passphrase" in text
    assert "no api key" in text


def test_the_readme_describes_the_mcp_server_honestly() -> None:
    text = README.read_text(encoding="utf-8").lower()
    assert "mcp" in text
    assert "tunnel" in text
    assert "passphrase" in text
    # What a chat host sees is processed by that host's provider; the README
    # has to say so rather than let "local-first" imply otherwise.
    assert "conversation" in text
    for overclaim in ("end-to-end encrypted", "nothing leaves your mac"):
        assert overclaim not in text


def test_agents_md_records_the_mcp_boundary() -> None:
    text = AGENTS.read_text(encoding="utf-8").lower()
    assert "mcp" in text
    assert "0008" in text
