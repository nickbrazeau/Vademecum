"""The Socratic tutor and the podcast generator (ADR 0025), from a chat host.

In a chat host -- in voice or in text -- the assistant IS the Socratic tutor:
socratic_start hands it the page and the rules, it asks and listens, records
each exchange with socratic_turn, and closes with socratic_finish. Podcast
scripts are written on the Mac; from here they can be started, listed and read.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient
from ._shared import READ, TRANSMITS, WRITE, call, listing

SessionId = Annotated[str, Field(max_length=64, description="The session's id from socratic_start.")]
EpisodeId = Annotated[str, Field(max_length=64, description="The episode's id from podcast_list.")]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=WRITE)
    async def socratic_start(
        entry_id: Annotated[str, Field(max_length=64, description="An encyclopedia page id to work from; empty for one chosen from the owner's gaps.")] = "",
    ) -> dict[str, Any]:
        """Begin a Socratic session and become the tutor. The reply carries the
        page (compiled from the owner's own sources), further context already on
        the Mac (reviewed abstracts, related pages, case-series points), the
        rules, and the dialogue so far. The page is the grounding; use the
        context, your own knowledge and web search to assess answers and to
        probe beyond the page, saying which is which and citing what you
        searched. Open with a clinical presentation from the page and ONE open
        question; never give the answer first. Work through the differential,
        then treatment, then the underlying knowledge. Suits voice: ask,
        listen, respond briefly, ask again. After each answer call
        socratic_turn; after about eight exchanges call socratic_finish with
        the assessment."""
        started = await call(api.post("/api/socratic", {"entry_id": entry_id} if entry_id else {}))
        session = started.get("session") or {}
        material = await call(api.get(f"/api/socratic/{session['id']}/material")) if session.get("id") else {}
        return {**started, **material}

    @mcp.tool(annotations=WRITE)
    async def socratic_turn(
        session_id: SessionId,
        question: Annotated[str, Field(max_length=1200, description="The question you asked, as you asked it.")],
        answer: Annotated[str, Field(max_length=8000, description="The owner's answer, as they gave it.")],
        probe: Annotated[str, Field(max_length=20, description="differential, treatment, knowledge or wrap_up.")] = "",
    ) -> dict[str, Any]:
        """Record one exchange of the session: your question and the owner's answer."""
        return await call(api.post(f"/api/socratic/{session_id}/turn", {"question": question, "answer": answer, "probe": probe}))

    @mcp.tool(annotations=WRITE)
    async def socratic_finish(
        session_id: SessionId,
        differential: Annotated[str, Field(max_length=400, description="How they reasoned through the differential.")],
        treatment: Annotated[str, Field(max_length=400, description="How they reasoned through treatment options.")],
        knowledge_strengths: Annotated[str, Field(max_length=400)],
        knowledge_gaps: Annotated[list[str], Field(max_length=5, description="Up to five gaps, each a short topic.")],
        summary: Annotated[str, Field(max_length=600)],
    ) -> dict[str, Any]:
        """Close the session with the assessment. Each named gap becomes a flag on
        the page's topic, so the Improvement Map and the flashcards take it up.
        Tell the owner what was filed."""
        assessment = {
            "differential": differential,
            "treatment": treatment,
            "knowledge_strengths": knowledge_strengths,
            "knowledge_gaps": knowledge_gaps,
            "summary": summary,
        }
        return await call(api.post(f"/api/socratic/{session_id}/finish", {"assessment": assessment}))

    @mcp.tool(annotations=READ)
    async def podcast_list() -> dict[str, Any]:
        """The podcast episodes: title, status (draft, scripted, rendered), the pages
        each was written from, length in words, and whether audio exists on the Mac."""
        data = await call(api.get("/api/podcasts"))
        return listing(data.get("episodes", []), note=data.get("note", ""), disclosure=data.get("disclosure", ""))

    @mcp.tool(annotations=READ)
    async def podcast_read(episode_id: EpisodeId) -> dict[str, Any]:
        """One episode's script, line by line with its speaker, and the take-homes.
        Read it aloud to the owner if they ask; it is theirs."""
        return await call(api.get(f"/api/podcasts/{episode_id}"))

    @mcp.tool(annotations=TRANSMITS)
    async def podcast_create(
        entry_ids: Annotated[list[str], Field(max_length=6, description="Encyclopedia page ids to write from; empty with pick=today or pick=improvement.")] = [],
        pick: Annotated[str, Field(max_length=12, description="chosen, today, or improvement.")] = "chosen",
        title: Annotated[str, Field(max_length=120)] = "",
    ) -> dict[str, Any]:
        """Write a podcast episode's script from encyclopedia pages, on the Mac's
        own model connection. Show the owner the disclosure from podcast_list and
        wait for a yes before calling this. The audio is rendered on the Mac from
        the dashboard."""
        return await call(api.post("/api/podcasts", {"entry_ids": entry_ids, "pick": pick, "title": title}))
