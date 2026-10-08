"""What every tool module shares: error translation and a few annotations."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..api_client import ApiError

T = TypeVar("T")

# Read-only: answers from the Mac, changes nothing, contacts nothing outside it.
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)
# Writes a record on the Mac. Nothing leaves the machine.
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
# Starts something that transmits: a model turn on the Mac's own connection (Codex or Claude), or a PubMed
# lookup. The host shows these differently, which is the point of saying so.
TRANSMITS = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=True)


async def call(awaitable: Awaitable[T]) -> T:
    """Run one API call, turning a refusal into a message the model can read.

    The API's message is already plain language and never echoes what was
    submitted. Nothing else about the failure -- no status line, no body, no
    traceback -- crosses into the tool result.
    """
    try:
        return await awaitable
    except ApiError as exc:
        raise ToolError(exc.message) from None


def listing(items: list[Any], **extra: Any) -> dict[str, Any]:
    """A list, as an object: structured output wants a top-level object."""
    return {"count": len(items), "items": items, **extra}
