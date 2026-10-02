"""The web app inside the conversation (ADR 0014).

Two tools. ``open_vademecum`` is for the model: it returns almost nothing and
is drawn by the app resource, so the host shows the dashboard in the chat.
``app_request`` is for the app: the frame it runs in has no network, so each
request the React code would have made to the API becomes one call to this
tool, which forwards it to the API on loopback -- but only for the routes
listed here. The list is the boundary: nothing that removes, retires, exports,
backs up, uploads a file, or signs in to ChatGPT is on it. Those are done in
the browser dashboard, through the source folder, or by the assistant's own
tools, each with its own confirmation. The tool is marked as callable from the
app, not by the model; a host that honours that never offers it to the model,
and a host that does not gets a tool that reaches nothing the model's own
tools do not already reach.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from ..api_client import ApiClient, ApiError
from ..widgets import APP_URI, tool_meta
from ._shared import READ

Method = Literal["GET", "POST", "PATCH", "PUT"]

ID = r"[A-Za-z0-9_-]{1,64}"
QUERY = r"(\?[A-Za-z0-9_=&%.+-]{0,200})?"

# (method, path pattern) the app may reach. Mirrors apps/web/src/lib/api.ts,
# minus what the docstring above keeps out.
ALLOWED: tuple[tuple[str, str], ...] = (
    ("GET", r"/api/health"),
    ("GET", r"/api/today"),
    ("GET", r"/api/improvement-map"),
    ("PUT", r"/api/improvement-map/topics/specialty"),
    ("PUT", r"/api/improvement-map/positions"),
    ("GET", r"/api/piles"),
    ("POST", r"/api/piles"),
    ("PATCH", rf"/api/piles/{ID}"),
    ("GET", rf"/api/piles/{ID}/items"),
    ("POST", rf"/api/piles/{ID}/items"),
    ("GET", rf"/api/piles/{ID}/sources"),
    ("GET", rf"/api/sources/{ID}"),
    ("PATCH", rf"/api/sources/{ID}"),
    ("GET", rf"/api/sources/{ID}/images"),
    ("POST", r"/api/sources/scan"),
    ("GET", rf"/api/piles/{ID}/build/preview"),
    ("POST", rf"/api/piles/{ID}/build"),
    ("GET", rf"/api/piles/{ID}/build/status"),
    ("POST", rf"/api/piles/{ID}/build/cancel"),
    ("POST", rf"/api/piles/{ID}/recheck"),
    ("GET", rf"/api/points{QUERY}"),
    ("GET", rf"/api/points/{ID}/schematics"),
    ("GET", r"/api/tutor"),
    ("GET", r"/api/tutor/next"),
    ("POST", r"/api/tutor/advance"),
    ("POST", r"/api/tutor/grade"),
    ("POST", r"/api/tutor/reveal"),
    ("POST", r"/api/tutor/self-assess"),
    ("GET", rf"/api/tutor/history{QUERY}"),
    ("GET", r"/api/literature/topics"),
    ("POST", r"/api/literature/topics"),
    ("PATCH", rf"/api/literature/topics/{ID}"),
    ("GET", r"/api/literature/suggestions"),
    ("POST", r"/api/literature/check"),
    ("GET", rf"/api/literature/updates{QUERY}"),
    ("PATCH", rf"/api/literature/updates/{ID}"),
    ("GET", r"/api/literature/settings"),
    ("PUT", r"/api/literature/settings"),
    ("GET", rf"/api/flags{QUERY}"),
    ("POST", r"/api/flags"),
    ("PATCH", rf"/api/flags/{ID}"),
    ("GET", r"/api/model/status"),
)
_COMPILED = [(method, re.compile(pattern + r"\Z")) for method, pattern in ALLOWED]

NOT_ALLOWED = (
    "That is not something the dashboard does from inside a conversation. Removing, "
    "retiring, exporting, backing up, adding files and signing in are done in the browser "
    "dashboard, through the source folder, or by asking the assistant."
)

# For the app only. Both spellings, as the cards use (widgets.tool_meta).
APP_ONLY = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)


def allowed(method: str, path: str) -> bool:
    return any(method == m and pattern.match(path) for m, pattern in _COMPILED)


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(
        annotations=READ,
        meta=tool_meta(APP_URI, invoking="Opening Vademecum…", invoked="Vademecum"),
    )
    async def open_vademecum(
        view: Annotated[
            Literal["today", "tutor", "sources", "map"],
            Field(description="Which page to open on: today (the cover sheet), tutor, sources, or map."),
        ] = "today",
    ) -> dict[str, Any]:
        """Show the Vademecum dashboard inside this conversation, drawn by the
        host: Today, the source library, the Tutor, and the Improvement Map as
        a graph. Call this first whenever the owner opens Vademecum, starts a
        session, or asks to see Vademecum, the dashboard, the web app, their
        sources, the Tutor or the map; pick the view that matches. If the host
        cannot draw it, open_dashboard opens the same thing in their browser.
        Everything the dashboard shows comes from this Mac."""
        return {
            "app": "vademecum",
            "view": view,
            "note": "The dashboard is drawn by the host. If nothing appears, offer open_dashboard.",
        }

    @mcp.tool(
        annotations=APP_ONLY,
        meta={"ui": {"visibility": ["app"]}, "openai/widgetAccessible": True},
    )
    async def app_request(
        method: Method,
        path: Annotated[str, Field(description="An /api path the dashboard reads or writes.", max_length=300)],
        body: Annotated[dict[str, Any] | None, Field(description="The JSON body, for a write.")] = None,
    ) -> dict[str, Any]:
        """For the Vademecum dashboard drawn in this conversation, not for you:
        the app's own requests to Vademecum on this Mac, limited to the routes
        the dashboard uses. Use the named tools instead."""
        if not allowed(method, path):
            return {"ok": False, "status": 403, "error": {"code": "not_allowed", "message": NOT_ALLOWED}}
        try:
            if method == "GET":
                result = await api.get(path)
            elif method == "POST":
                result = await api.post(path, body)
            elif method == "PATCH":
                result = await api.patch(path, body or {})
            else:
                result = await api.put(path, body or {})
        except ApiError as exc:
            return {"ok": False, "status": exc.status, "error": {"code": exc.code, "message": exc.message}}
        return {"ok": True, "status": 200, "body": result}
