"""Build learning material: preview, consent, start, status, cancel.

Consent is to specific characters (ADR 0007). ``build_preview`` returns the
exact excerpts and the disclosure that names what would be sent; ``build_start``
quotes back the preview's ``batch_id`` and ``selection_hash``, and the API
refuses the send if anything the owner read has changed since. Through a chat
host the owner reads the preview in the conversation, so the tool descriptions
tell the assistant to show it and to wait for an explicit yes.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient
from ._shared import READ, TRANSMITS, WRITE, call

PileId = Annotated[str, Field(description="A pile id from list_piles.", max_length=64)]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=WRITE)
    async def build_preview(pile_id: PileId) -> dict[str, Any]:
        """Show exactly what the next 'Build learning material' batch would send
        for a pile, and record the consent request for it. Nothing is
        transmitted by this call. Present the disclosure (headline, bullets and
        destination) and the list of excerpts with their file names and
        locations to the owner, and ask for an explicit yes before calling
        build_start with this preview's batch_id and selection_hash. If
        blocked_reason is non-empty, say why and stop. Call it again for a fresh
        preview if the owner changes anything first."""
        return await call(api.get(f"/api/piles/{pile_id}/build/preview"))

    @mcp.tool(annotations=TRANSMITS)
    async def build_start(
        pile_id: PileId,
        batch_id: Annotated[str, Field(max_length=64, description="From build_preview.")],
        selection_hash: Annotated[
            str, Field(min_length=64, max_length=64, description="From the same build_preview.")
        ],
    ) -> dict[str, Any]:
        """Start a build of the previewed excerpts into learning points and
        questions. Only call this after the owner has read the preview and said
        yes. Short public topic words go from Vademecum to PubMed to look for
        evidence. If the reply's `pending` list is non-empty, this workspace
        runs in host mode and YOU are the model: do the first pending turn as
        its `rules` say, using only its `material`, and submit the result with
        build_submit; keep going with each `next` until it is null. If `pending`
        is empty, the Mac's own model connection runs the build; poll
        build_status. A stale preview is refused: call build_preview again."""
        return await call(
            api.post(
                f"/api/piles/{pile_id}/build",
                {"batch_id": batch_id, "selection_hash": selection_hash},
            )
        )

    @mcp.tool(annotations=READ)
    async def build_pending(pile_id: PileId) -> dict[str, Any]:
        """The model work waiting on you for a pile's running build (host mode).
        Each turn carries instructions, rules, the exact material you may use,
        and the output_schema your result must match. Empty when nothing is
        waiting or when the Mac runs its own model turns."""
        return await call(api.get(f"/api/piles/{pile_id}/build/pending"))

    @mcp.tool(annotations=WRITE)
    async def build_submit(
        pile_id: PileId,
        turn_id: Annotated[str, Field(max_length=64, description="The pending turn's turn_id.")],
        result: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "One JSON object matching the turn's output_schema exactly: every "
                    "required field, no extra fields, enum values exact, quotes copied "
                    "verbatim from the material."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Submit your result for one pending build turn. Vademecum validates it
        against the schema and checks every quote against the material before
        anything is stored; a refusal names the problem and leaves the turn
        pending so you can correct and resubmit. The reply's `next` is the
        following turn to do, or null when the run has finished (see `run`).
        Never invent quotes, identifiers or support; if the material does not
        carry something, say so through the fields provided."""
        return await call(
            api.post(
                f"/api/piles/{pile_id}/build/submit", {"turn_id": turn_id, "result": result}
            )
        )

    @mcp.tool(annotations=READ)
    async def build_status(pile_id: PileId) -> dict[str, Any]:
        """The latest build run for a pile (stage, outcome, counts of points and
        questions produced or held), whether one is running now, the pile's
        coverage, and a summary of the whole learning bank."""
        return await call(api.get(f"/api/piles/{pile_id}/build/status"))

    @mcp.tool(annotations=WRITE)
    async def build_cancel(pile_id: PileId) -> dict[str, Any]:
        """Stop a running build for a pile. Anything already committed stays; a
        cancelled batch advances no coverage, so its passages are offered
        again next time."""
        return await call(api.post(f"/api/piles/{pile_id}/build/cancel"))

    @mcp.tool(annotations=READ)
    async def get_model_status() -> dict[str, Any]:
        """Whether the Mac's Codex connection is signed in to ChatGPT, which
        plan, and how much of its usage window is used. No account identifiers
        are ever included. Signing in is done on the Mac, not from here."""
        return await call(api.get("/api/model/status"))
