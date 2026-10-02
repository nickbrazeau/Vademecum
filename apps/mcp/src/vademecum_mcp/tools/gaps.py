"""Knowledge Gap Flags: one required field, captured in one action."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient
from ._shared import READ, WRITE, call, listing

FlagStatus = Literal["open", "addressed"]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=WRITE)
    async def flag_knowledge_gap(
        text: Annotated[
            str,
            Field(
                min_length=1,
                max_length=4000,
                description="What the owner noticed they were unsure about, in their words.",
            ),
        ],
        topic: Annotated[
            str | None,
            Field(max_length=120, description="A short topic to file it under, if the owner gave one."),
        ] = None,
        pile_id: Annotated[str | None, Field(max_length=64)] = None,
    ) -> dict[str, Any]:
        """Record a moment of self-identified uncertainty as a Knowledge Gap
        Flag. Only the text is required; do not ask the owner to categorise,
        rate or schedule anything. Never include patient names, dates of birth,
        record numbers or other identifiers -- restate the gap in general terms
        if the owner's wording contains one."""
        return await call(
            api.post("/api/flags", {"text": text, "topic": topic, "pile_id": pile_id})
        )

    @mcp.tool(annotations=READ)
    async def list_flags(
        status: Annotated[FlagStatus | None, Field(description="open or addressed; omit for both.")] = None,
        limit: Annotated[int, Field(ge=1, le=500)] = 50,
    ) -> dict[str, Any]:
        """The owner's Knowledge Gap Flags, newest first."""
        flags = await call(api.get("/api/flags", params={"status": status, "limit": limit}))
        return listing(flags)

    @mcp.tool(annotations=WRITE)
    async def update_flag(
        flag_id: Annotated[str, Field(max_length=64)],
        status: Annotated[
            FlagStatus | None,
            Field(description="'addressed' when the owner has closed the gap; 'open' to reopen it."),
        ] = None,
        topic: Annotated[str | None, Field(max_length=120)] = None,
        text: Annotated[str | None, Field(min_length=1, max_length=4000)] = None,
        pile_id: Annotated[str | None, Field(max_length=64)] = None,
    ) -> dict[str, Any]:
        """Edit a flag's text or topic, or mark it addressed or open. Flags are
        never deleted from here."""
        payload: dict[str, Any] = {}
        for key, value in (("status", status), ("topic", topic), ("text", text), ("pile_id", pile_id)):
            if value is not None:
                payload[key] = value
        if not payload:
            return await call(api.get(f"/api/flags/{flag_id}"))
        return await call(api.patch(f"/api/flags/{flag_id}", payload))
