"""open_dashboard: the web app in the owner's browser, from the conversation."""

from __future__ import annotations

import pytest
from tests.mcp_support import call
from mcp import Client

from vademecum.app import static_root
from vademecum_mcp.tools import dashboard

pytestmark = pytest.mark.anyio


async def test_the_dashboard_opens_in_a_browser_on_this_mac(mcp_client: Client, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(dashboard.webbrowser, "open", lambda url: opened.append(url) or True)

    result = await call(mcp_client, "open_dashboard", {})

    if static_root() is None:
        # No build in this checkout: the tool says so and opens nothing.
        assert result["opened"] is False and "not been built" in result["note"]
        assert opened == []
    else:
        assert result["opened"] is True
        assert result["url"] == "http://127.0.0.1:8765"
        assert opened == ["http://127.0.0.1:8765"]
        assert "/" not in result["note"]


async def test_a_host_that_forbids_launching_still_gets_the_address(mcp_client: Client, monkeypatch) -> None:
    def refuse(url: str) -> bool:
        raise OSError("sandbox")

    monkeypatch.setattr(dashboard.webbrowser, "open", refuse)
    result = await call(mcp_client, "open_dashboard", {})
    if static_root() is not None:
        assert result["opened"] is False
        assert result["url"] == "http://127.0.0.1:8765"
        assert "link" in result["note"]


async def test_the_tool_is_read_only_and_local(mcp_client: Client) -> None:
    tool = next(t for t in (await mcp_client.list_tools()).tools if t.name == "open_dashboard")
    assert tool.annotations.read_only_hint is True
    assert tool.annotations.open_world_hint is False
    assert "browser" in tool.description
