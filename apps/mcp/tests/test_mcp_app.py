"""The web app inside the conversation (ADR 0014): one resource, one tool for
the model, one tool for the app with a fixed list of routes."""

from __future__ import annotations

import re

import pytest
from tests.mcp_support import LECTURE, call
from mcp import Client

from vademecum_mcp.tools.app import ALLOWED, allowed
from vademecum_mcp.widgets import APP_URI, MIME_TYPE

pytestmark = pytest.mark.anyio


async def test_the_app_document_is_complete_as_served(mcp_client: Client) -> None:
    listed = {str(r.uri): r for r in (await mcp_client.list_resources()).resources}
    assert listed[APP_URI].mime_type == MIME_TYPE
    html = (await mcp_client.read_resource(APP_URI)).contents[0].text
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert "<script src" not in html and "<link " not in html and "@import" not in html
    # The only addresses in it are a link a person may follow (PubMed), XML
    # namespaces, and React's error-page prefix. Nothing is loaded from anywhere.
    harmless = ("https://pubmed.ncbi.nlm.nih.gov/", "http://www.w3.org/", "https://react.dev/errors/")
    for address in set(re.findall(r"https?://[^\"'\s)<`]+", html)):
        assert address.startswith(harmless), address
    assert "Educational only" in html
    assert 'data-theme="dark"' in html and 'data-theme="light"' in html
    for needle in ("ui/initialize", "tools/call", "app_request", "ui/notifications/size-changed"):
        assert needle in html, needle
    for forbidden in ("streak", "due count", "quota"):
        assert forbidden not in html.lower()


async def test_open_vademecum_is_drawn_by_the_app(mcp_client: Client) -> None:
    tool = next(t for t in (await mcp_client.list_tools()).tools if t.name == "open_vademecum")
    assert tool.meta["ui"]["resourceUri"] == APP_URI
    assert tool.annotations.read_only_hint is True
    result = await call(mcp_client, "open_vademecum", {})
    assert result["app"] == "vademecum" and result["view"] == "today"
    assert (await call(mcp_client, "open_vademecum", {"view": "map"}))["view"] == "map"
    assert (await mcp_client.call_tool("open_vademecum", {"view": "settings"})).is_error
    assert "Call this first" in tool.description


async def test_a_prompt_opens_the_dashboard(mcp_client: Client) -> None:
    prompts = {p.name: p for p in (await mcp_client.list_prompts()).prompts}
    assert "open_vademecum" in prompts
    got = await mcp_client.get_prompt("open_vademecum", {})
    assert "open_vademecum" in got.messages[0].content.text


async def test_app_request_reaches_the_dashboard_routes_and_only_those(mcp_client: Client) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    await call(
        mcp_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )

    today = await call(mcp_client, "app_request", {"method": "GET", "path": "/api/today"})
    assert today["ok"] is True and today["status"] == 200
    assert "worth_a_look" in today["body"]

    sources = await call(mcp_client, "app_request", {"method": "GET", "path": f"/api/piles/{pile['id']}/sources"})
    assert sources["ok"] and sources["body"][0]["display_name"] == "Lecture.md"

    flagged = await call(
        mcp_client,
        "app_request",
        {"method": "POST", "path": "/api/flags", "body": {"text": "Unsure about vasopressors"}},
    )
    assert flagged["ok"] and flagged["body"]["status"] == "open"

    # A refusal from the API comes back as data the app can show, not as a tool error.
    bad = await call(mcp_client, "app_request", {"method": "POST", "path": "/api/flags", "body": {"text": ""}})
    assert bad["ok"] is False and bad["status"] == 422
    assert bad["error"]["code"] == "invalid_request"

    # Not on the list: removal, retirement, export, backup, sign-in, the workspace.
    for method, path in (
        ("GET", "/api/export"),
        ("POST", "/api/export"),
        ("POST", "/api/backup"),
        ("POST", "/api/model/login"),
        ("POST", "/api/model/restart"),
        ("GET", "/api/workspace"),
        ("POST", f"/api/piles/{pile['id']}/sources"),
        ("GET", "/api/today/../export"),
        ("GET", "/api/flags?status=open&x=<script>"),
    ):
        refused = await call(mcp_client, "app_request", {"method": method, "path": path})
        assert refused["ok"] is False and refused["status"] == 403, (method, path)
        assert "browser dashboard" in refused["error"]["message"]

    # DELETE is not even a method the tool accepts.
    result = await mcp_client.call_tool("app_request", {"method": "DELETE", "path": "/api/flags/x"})
    assert result.is_error


def test_the_allowlist_has_no_removal_and_no_transmission_outside_the_named_routes() -> None:
    for method, pattern in ALLOWED:
        assert method in {"GET", "POST", "PATCH", "PUT"}
        assert "login" not in pattern and "export" not in pattern and "backup" not in pattern
        assert "workspace" not in pattern and "material" not in pattern
    assert allowed("GET", "/api/flags?status=open")
    assert allowed("GET", "/api/points?pile_id=pil_1&held=true")
    assert not allowed("GET", "/api/flags/../workspace")
    assert not allowed("GET", "/api/piles/x/y/z")
    assert not allowed("DELETE", "/api/flags/x")
