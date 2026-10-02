"""The two cards ChatGPT can render inline: Today, and the Tutor question.

Each is one self-contained HTML document -- inline style, inline script, no
external asset, no network of its own -- registered as an MCP resource with
the MCP Apps mime type and linked from the tools whose results it draws. The
host renders it in a sandboxed frame and hands it the tool's structured
content; the card is a view of that content and nothing else.

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
TODAY_URI = "ui://vademecum/today.html"
TUTOR_URI = "ui://vademecum/tutor.html"

TODAY_DESCRIPTION = (
    "The learner's cover sheet: learning points worth a look, what is held, where "
    "Tutor stands, unread literature and recent knowledge-gap flags."
)
TUTOR_DESCRIPTION = (
    "One Tutor question with where the cycle stands; after grading, the reference "
    "answer and the feedback. The learner answers in the conversation."
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
        TODAY_URI,
        name="Today card",
        description=TODAY_DESCRIPTION,
        mime_type=MIME_TYPE,
        meta=_resource_meta(domain, TODAY_DESCRIPTION),
    )
    def today_card() -> str:
        return _read("today.html")

    @mcp.resource(
        TUTOR_URI,
        name="Tutor card",
        description=TUTOR_DESCRIPTION,
        mime_type=MIME_TYPE,
        meta=_resource_meta(domain, TUTOR_DESCRIPTION),
    )
    def tutor_card() -> str:
        return _read("tutor.html")
