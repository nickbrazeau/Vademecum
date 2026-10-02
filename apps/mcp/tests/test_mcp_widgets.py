"""The two cards (ADR 0009 phase 3): registered as MCP Apps resources, linked
from the tools whose results they draw, self-contained, and honest."""

from __future__ import annotations

import re

import pytest
from mcp import Client
from tests.mcp_support import call

from vademecum_mcp.widgets import MIME_TYPE, TODAY_URI, TUTOR_URI

pytestmark = pytest.mark.anyio

DRAWN_BY_TUTOR = {
    "tutor_next_question",
    "tutor_reveal",
    "tutor_grade",
    "tutor_record_grade",
    "tutor_self_assess",
    "tutor_advance",
}
CALLABLE_FROM_CARD = {"tutor_reveal", "tutor_advance"}


async def test_the_cards_are_resources_with_the_apps_mime_type(mcp_client: Client) -> None:
    listed = (await mcp_client.list_resources()).resources
    by_uri = {str(resource.uri): resource for resource in listed}
    assert set(by_uri) == {TODAY_URI, TUTOR_URI}
    for uri, resource in by_uri.items():
        assert resource.mime_type == MIME_TYPE, uri
        meta = resource.meta or {}
        assert meta["ui"]["prefersBorder"] is True
        assert meta["ui"]["csp"] == {"connectDomains": [], "resourceDomains": [], "frameDomains": []}
        assert meta["openai/widgetDescription"]
        assert "domain" not in meta["ui"], "no public URL in stdio mode"


async def test_the_tools_point_at_their_cards(mcp_client: Client) -> None:
    tools = {tool.name: tool for tool in (await mcp_client.list_tools()).tools}
    assert tools["get_today"].meta["ui"]["resourceUri"] == TODAY_URI
    assert tools["get_today"].meta["openai/outputTemplate"] == TODAY_URI
    for name in DRAWN_BY_TUTOR:
        meta = tools[name].meta or {}
        assert meta["ui"]["resourceUri"] == TUTOR_URI, name
        assert len(meta["openai/toolInvocation/invoking"]) <= 64
        if name in CALLABLE_FROM_CARD:
            assert meta["ui"]["visibility"] == ["model", "app"], name
            assert meta["openai/widgetAccessible"] is True, name
        else:
            assert "visibility" not in meta["ui"], name
            assert "openai/widgetAccessible" not in meta, name
    for name, tool in tools.items():
        if name not in DRAWN_BY_TUTOR and name != "get_today":
            assert not (tool.meta or {}).get("ui"), f"{name} has no card"


async def test_each_card_is_complete_as_served(mcp_client: Client) -> None:
    for uri in (TODAY_URI, TUTOR_URI):
        result = await mcp_client.read_resource(uri)
        html = result.contents[0].text
        assert html.lstrip().lower().startswith("<!doctype html>")
        assert "<script src" not in html and "<link " not in html and "@import" not in html
        assert not re.search(r"https?://", html), "no external URL of any kind"
        assert "Educational only" in html
        assert "prefers-color-scheme: dark" in html
        assert 'data-theme' in html
        assert "ui/notifications/tool-result" in html and "toolOutput" in html
        assert "innerHTML" in html and "function esc(" in html, "everything drawn is escaped"
        for forbidden in ("streak", "due count", "quota", "review queue"):
            assert forbidden not in html.lower()


async def test_the_tutor_card_only_reaches_reveal_and_advance(mcp_client: Client) -> None:
    html = (await mcp_client.read_resource(TUTOR_URI)).contents[0].text
    called = set(re.findall(r'callTool\("([a-z_]+)"', html))
    assert called == {"tutor_reveal", "tutor_advance"}
    assert "tutor_grade" not in called and "build_start" not in html


async def test_the_domain_is_declared_when_the_public_url_is_known(api) -> None:
    from vademecum_mcp.server import create_server

    server = create_server(api, public_url="https://vademecum.example")
    async with Client(server) as client:
        listed = (await client.list_resources()).resources
        for resource in listed:
            assert resource.meta["ui"]["domain"] == "https://vademecum.example"
            assert resource.meta["openai/widgetDomain"] == "https://vademecum.example"


async def test_a_card_result_carries_what_the_card_draws(mcp_client: Client) -> None:
    today = await call(mcp_client, "get_today", {})
    for key in ("worth_a_look", "held", "tutor", "literature", "recent_flags", "open_flag_count"):
        assert key in today
    question = await call(mcp_client, "tutor_next_question", {})
    for key in ("question", "cycle", "empty_reason"):
        assert key in question
