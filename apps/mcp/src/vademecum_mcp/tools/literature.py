"""The public-literature watch: topics, checks and updates.

Only short public topic phrases ever reach PubMed, and the API validates them
again before sending. Nothing here can put a passage, a flag or an answer into
a search.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient
from ._shared import READ, TRANSMITS, WRITE, call, listing

UpdateState = Literal["unread", "acknowledged", "dismissed"]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=READ)
    async def literature_topics() -> dict[str, Any]:
        """The public topics the owner watches on PubMed, the watch settings
        (whether the weekly check is on -- it is opt-in and runs only while
        Vademecum runs on the Mac), and suggested topics drawn from what builds
        have already produced."""
        topics = await call(api.get("/api/literature/topics"))
        settings = await call(api.get("/api/literature/settings"))
        suggestions = await call(api.get("/api/literature/suggestions", params={"limit": 12}))
        return {
            "topics": topics,
            "settings": settings,
            "suggestions": suggestions.get("suggestions", []),
            "note": suggestions.get("note", ""),
        }

    @mcp.tool(annotations=WRITE)
    async def literature_add_topic(
        label: Annotated[str, Field(min_length=1, max_length=120, description="How the owner refers to it.")],
        query: Annotated[
            str,
            Field(
                min_length=2,
                max_length=200,
                description=(
                    "A short public search phrase for PubMed: a condition, a drug class, "
                    "a test. This phrase is sent to PubMed; nothing from the owner's "
                    "material or notes may be in it."
                ),
            ),
        ],
    ) -> dict[str, Any]:
        """Watch a public topic on PubMed. Adding a topic does not switch on the
        weekly check; use literature_check to look now."""
        return await call(api.post("/api/literature/topics", {"label": label, "query": query}))

    @mcp.tool(annotations=TRANSMITS)
    async def literature_check(
        topic_id: Annotated[
            str | None, Field(max_length=64, description="One topic, or omit to check every enabled topic.")
        ] = None,
    ) -> dict[str, Any]:
        """Look for new or changed published literature now. Sends the watched
        topic's short public search phrase, and public PubMed identifiers of
        papers already linked, to PubMed (NCBI E-utilities). Never sends source
        excerpts, file names, flags or answers."""
        return await call(api.post("/api/literature/check", {"topic_id": topic_id}))

    @mcp.tool(annotations=READ)
    async def literature_updates(
        state: Annotated[UpdateState | None, Field(description="Default: unread.")] = "unread",
        limit: Annotated[int, Field(ge=1, le=200)] = 20,
    ) -> dict[str, Any]:
        """New papers, corrections and retractions found for watched topics,
        with why each is relevant. A new paper is a candidate worth a look, not
        a statement that practice has changed."""
        updates = await call(
            api.get("/api/literature/updates", params={"state": state, "limit": limit})
        )
        return listing(updates)

    @mcp.tool(annotations=WRITE)
    async def literature_set_update_state(
        update_id: Annotated[str, Field(max_length=64)],
        state: Annotated[UpdateState, Field(description="acknowledged, dismissed, or unread.")],
    ) -> dict[str, Any]:
        """Mark a literature update acknowledged or dismissed, or unread again."""
        return await call(api.patch(f"/api/literature/updates/{update_id}", {"state": state}))
