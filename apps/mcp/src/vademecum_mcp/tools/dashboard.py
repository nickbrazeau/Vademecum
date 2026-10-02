"""The web app, one click from the conversation.

The API serves the built web app at its own loopback address. This tool
opens it in the learner's default browser, on this Mac, so the dashboard the
web app is -- Today, the sources, the Tutor, the Improvement Map's graph --
is a sentence away from the chat. Nothing is served anywhere new and nothing
leaves the machine: the browser talks to 127.0.0.1 exactly as the assistant
does.

In the hosted mode there is no browser on this machine to open, and the web
app is at the gateway's address instead; the tool says so.
"""

from __future__ import annotations

import asyncio
import webbrowser
from typing import Any

from mcp.server import MCPServer

from ..api_client import ApiClient, ApiError
from ..config import McpSettings
from ._shared import READ

NOT_BUILT = (
    "The web app has not been built on this Mac, so there is nothing to open. "
    "Run ./scripts/install.sh again with Node installed, or ./scripts/dev.sh for the "
    "development server."
)


def register(mcp: MCPServer, api: ApiClient, settings: McpSettings | None) -> None:
    hosted = settings is not None and settings.tenancy == "multi"

    @mcp.tool(annotations=READ)
    async def open_dashboard() -> dict[str, Any]:
        """Open the Vademecum web app -- the dashboard -- in the owner's browser
        on this Mac: Today, the source library, the Tutor, the Improvement Map
        as a graph, and the model status. Use it when the owner wants to see
        or browse rather than be told, or asks for the dashboard, the web app
        or the map. The page is served from this machine only. Building and
        grading still happen here, in the conversation."""
        if hosted:
            return {
                "opened": False,
                "note": (
                    "This Vademecum is hosted: the web app is at the same address as the "
                    "assistant connection, signed in with the owner's handle."
                ),
            }
        url = settings.api_base_url if settings is not None else api.base_url
        try:
            await api.get("/api/health")
        except ApiError:
            return {"opened": False, "note": "Vademecum is not answering on this Mac right now."}
        try:
            await api.get_bytes("/")
        except ApiError:
            return {"opened": False, "url": url, "note": NOT_BUILT}
        opened = await asyncio.to_thread(webbrowser.open, url)
        return {
            "opened": bool(opened),
            "url": url,
            "note": (
                "Opened in the owner's browser." if opened else "Could not open a browser; give the owner the address."
            ),
        }
