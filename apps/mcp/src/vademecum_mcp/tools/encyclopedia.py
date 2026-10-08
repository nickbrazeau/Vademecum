"""The encyclopedia and the board bank (ADR 0023): pages to read, questions to ask."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient
from ._shared import READ, TRANSMITS, WRITE, call, listing

EntryId = Annotated[str, Field(max_length=64, description="The page's id from encyclopedia_list or encyclopedia_page.")]
BoardId = Annotated[str, Field(max_length=64, description="The question's id from board_next_question.")]
Choice = Annotated[int, Field(ge=0, le=4, description="The chosen option, 0 for A through 4 for E.")]


Where = Annotated[
    Literal["shuffled", "need"],
    Field(description='"shuffled": the pass through every question; "need": where the learner model says it helps most.'),
]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=READ)
    async def encyclopedia_page(
        random: Annotated[bool, Field(description="True for another page chosen now; false for the page of the day.")] = False,
    ) -> dict[str, Any]:
        """The encyclopedia page of the day, or another page: one topic compiled
        from the owner's own learning points, every paragraph naming the
        points and sources it rests on. Read it with the owner, or put it to
        them as a review; it counts nothing and asks for nothing back."""
        return await call(api.get("/api/encyclopedia/page", params={"random": "true" if random else "false"}))

    @mcp.tool(annotations=READ)
    async def encyclopedia_list(
        q: Annotated[str, Field(max_length=100, description="Words to search titles and text for; empty for every page.")] = "",
    ) -> dict[str, Any]:
        """Every encyclopedia page by title, with how many points and
        questions each has, and how many topics still wait to be compiled."""
        data = await call(api.get("/api/encyclopedia", params={"q": q} if q else None))
        return listing(data.get("entries", []), counts=data.get("counts"), note=data.get("note", ""))

    @mcp.tool(annotations=READ)
    async def encyclopedia_read(entry_id: EntryId) -> dict[str, Any]:
        """One page in full, with its citations resolved to claims and sources."""
        return await call(api.get(f"/api/encyclopedia/{entry_id}"))

    @mcp.tool(annotations=READ)
    async def board_next_question(where: Where = "shuffled") -> dict[str, Any]:
        """The board-style question to ask now: a vignette and five options,
        without the key. Idempotent until board_advance. Put the stem and the
        options to the owner and let them choose; do not reveal or hint at the
        answer. If question is null, empty_reason says why. where="need" asks
        from the page the owner's learner model says needs it most (ADR 0031)."""
        return await call(api.get("/api/tutor/board/next", params={"focus": "need"} if where == "need" else None))

    @mcp.tool(annotations=WRITE)
    async def board_answer(question_id: BoardId, choice: Choice) -> dict[str, Any]:
        """Check the owner's choice against the key, locally: nothing is
        transmitted. Returns whether it was right, the key, the explanation of
        each option, and the page's points and sources the answer rests on."""
        return await call(api.post("/api/tutor/board/answer", {"question_id": question_id, "choice": choice}))

    @mcp.tool(annotations=WRITE)
    async def board_advance(question_id: BoardId, where: Where = "shuffled") -> dict[str, Any]:
        """Mark the current board question served and move to the next one. Pass the
        same where as board_next_question: "need" moves to the next page that needs it."""
        payload: dict[str, Any] = {"question_id": question_id}
        if where == "need":
            payload["focus"] = "need"
        return await call(api.post("/api/tutor/board/advance", payload))

    @mcp.tool(annotations=READ)
    async def flashcard_next(
        not_id: Annotated[str, Field(max_length=64, description="The card just shown, so another is drawn.")] = "",
    ) -> dict[str, Any]:
        """Draw a flashcard: a front to put to the owner, the back to reveal once
        they have answered, the points and sources it rests on, and why this
        card came up (a flagged topic, an exam area below the mark, a missed
        board question, a card they asked to see again). A weighted draw, not a
        queue: nothing is due. Then flashcard_review with how it went."""
        return await call(api.get("/api/flashcards/next", params={"not_id": not_id} if not_id else None))

    @mcp.tool(annotations=WRITE)
    async def flashcard_review(
        card_id: Annotated[str, Field(max_length=64, description="The card's id from flashcard_next.")],
        rating: Annotated[Literal["again", "good"], Field(description="'again' to see it more often, 'good' when it was known.")],
    ) -> dict[str, Any]:
        """Record how the owner did on a card and draw the next one. Local."""
        return await call(api.post("/api/flashcards/review", {"card_id": card_id, "rating": rating}))

    @mcp.tool(annotations=READ)
    async def dissect_status() -> dict[str, Any]:
        """Where the dissection agent stands: which pile it is working through,
        batches built, pages compiled, questions written, its phase (building,
        compiling, backing off, complete) and the last error, plus the
        disclosure that says what starting it consents to."""
        return await call(api.get("/api/encyclopedia/dissection"))

    @mcp.tool(annotations=TRANSMITS)
    async def dissect_start(
        pile_id: Annotated[str, Field(max_length=64, description="The pile to work through, from list_piles, or 'all' for every pile.")] = "all",
    ) -> dict[str, Any]:
        """Start, or resume, the agent that works through one pile, or every
        pile, until all is built and every topic has a page with its literature
        review and board questions, backing off and retrying on failure,
        resuming after a restart, and afterwards building whatever is dropped
        into a pile as soon as the folder scan sees it. A standing
        consent: show the owner dissect_status's disclosure and wait for an
        explicit yes before calling this. Stop it with dissect_stop."""
        return await call(api.post("/api/encyclopedia/dissection", {"pile_id": pile_id}))

    @mcp.tool(annotations=WRITE)
    async def dissect_stop() -> dict[str, Any]:
        """Stop the dissection agent and withdraw the standing consent."""
        return await call(api.post("/api/encyclopedia/dissection/stop", {}))
