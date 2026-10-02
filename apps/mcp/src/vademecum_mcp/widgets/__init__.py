"""What a host can draw inline: the dashboard, and the Tutor question card.

Each is one self-contained HTML document -- inline style, inline script, no
external asset, no network of its own -- registered as an MCP resource with
the MCP Apps mime type and linked from the tools whose results it draws. The
host renders it in a sandboxed frame and hands it the tool's structured
content. The Tutor card is a view of that content and nothing else; the
dashboard (ADR 0014) is the web app itself, which loads what it shows through
the app_request tool. The cover sheet (get_today) and open_vademecum are both
drawn by the dashboard, so opening Vademecum means seeing it.

What a card may do: show the result, ask for the reference answer, move to the
next question. What it may not do: grade, build, or reach anything the tools
do not already return. The learner's answer is typed in the conversation, as
before, because grading is the assistant's act, not the card's.
"""

from __future__ import annotations

from importlib import resources
from typing import Any

from mcp.server import MCPServer

MIME_TYPE = "text/html;profile=mcp-app"
TUTOR_URI = "ui://vademecum/tutor.html"
# The whole web app, built into one document (ADR 0014; apps/web `build:app`).
APP_URI = "ui://vademecum/app.html"

TUTOR_DESCRIPTION = (
    "One Tutor question with where the cycle stands; after grading, the reference "
    "answer and the feedback. The learner answers in the conversation."
)
APP_DESCRIPTION = (
    "The Vademecum dashboard: Today, the source library, the Tutor and the Improvement "
    "Map, as the web app shows them. Reads and writes through the app_request tool only."
)


def _read(name: str) -> str:
    return resources.files(__package__).joinpath(name).read_text(encoding="utf-8")


def _resource_meta(domain: str | None, description: str) -> dict[str, Any]:
    ui: dict[str, Any] = {
        "prefersBorder": True,
        # No fetch, no asset, no frame: the card is complete as served.
        "csp": {"connectDomains": [], "resourceDomains": [], "frameDomains": []},
    }
    if domain:
        ui["domain"] = domain
    return {
        "ui": ui,
        "openai/widgetDescription": description,
        "openai/widgetPrefersBorder": True,
        "openai/widgetCSP": {"connect_domains": [], "resource_domains": []},
        **({"openai/widgetDomain": domain} if domain else {}),
    }


def tool_meta(uri: str, *, invoking: str, invoked: str, callable_from_card: bool = False) -> dict[str, Any]:
    """What a tool declares to be drawn by a card (both spellings the host reads)."""
    meta: dict[str, Any] = {
        "ui": {"resourceUri": uri},
        "openai/outputTemplate": uri,
        "openai/toolInvocation/invoking": invoking[:64],
        "openai/toolInvocation/invoked": invoked[:64],
    }
    if callable_from_card:
        meta["ui"]["visibility"] = ["model", "app"]
        meta["openai/widgetAccessible"] = True
    return meta


def register(mcp: MCPServer, *, domain: str | None = None) -> None:
    @mcp.resource(
        TUTOR_URI,
        name="Tutor card",
        description=TUTOR_DESCRIPTION,
        mime_type=MIME_TYPE,
        meta=_resource_meta(domain, TUTOR_DESCRIPTION),
    )
    def tutor_card() -> str:
        return _read("tutor.html")

    @mcp.resource(
        APP_URI,
        name="Vademecum dashboard",
        description=APP_DESCRIPTION,
        mime_type=MIME_TYPE,
        meta=_resource_meta(domain, APP_DESCRIPTION),
    )
    def app_document() -> str:
        return _read("app.html")
