"""Tutor: the cycle, grading, reveal and the self-assessment fallback."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server import MCPServer
from pydantic import Field

from ..api_client import ApiClient, ApiError
from ..widgets import TUTOR_URI, tool_meta
from ._shared import READ, TRANSMITS, WRITE, call

# Every Tutor result is drawn by the same card; the card shows whichever of
# question, reference and attempt the result carries. Reveal and advance may
# be called from the card's own buttons; grading never is.
CARD = tool_meta(TUTOR_URI, invoking="Asking…", invoked="Tutor")
CARD_CALLABLE = tool_meta(TUTOR_URI, invoking="Asking…", invoked="Tutor", callable_from_card=True)
GRADED = tool_meta(TUTOR_URI, invoking="Grading…", invoked="Graded")

QuestionId = Annotated[str, Field(max_length=64, description="The question's id from tutor_next_question.")]
Answer = Annotated[str, Field(max_length=8000, description="The owner's answer, in their own words.")]


def register(mcp: MCPServer, api: ApiClient) -> None:
    @mcp.tool(annotations=READ)
    async def tutor_overview() -> dict[str, Any]:
        """Where Tutor stands: how many questions are eligible, how many are
        held and why, how many answers have been recorded, where the shuffled
        cycle is, and the disclosure that says what Grade sends. Remaining is
        how many are left before the shuffle is redrawn -- a fact about the
        shuffle, never a number owed.

        For a Socratic session (open questions, voice or text) use socratic_start,
        socratic_turn and socratic_finish. If those tools are not in your list,
        the connector's tool list is out of date: tell the owner to refresh the
        Vademecum connector in the app's settings, or remove and add it again."""
        data = await call(api.get("/api/tutor"))
        return {
            **data,
            "socratic": "Socratic sessions use socratic_start, socratic_turn and socratic_finish. If you do not have those "
            "tools, the connector's tool list is stale: ask the owner to refresh the Vademecum connector in settings.",
        }

    @mcp.tool(annotations=READ, meta=CARD)
    async def tutor_next_question() -> dict[str, Any]:
        """The question to ask now, without its reference answer. Idempotent:
        calling it again returns the same question until tutor_advance. Put the
        prompt to the owner and let them answer in their own words; do not
        answer it for them, and do not reveal or hint at the reference before
        they have tried. If question is null, empty_reason says why."""
        return await call(api.get("/api/tutor/next"))

    @mcp.tool(annotations=READ, meta=CARD_CALLABLE)
    async def tutor_reveal(question_id: QuestionId) -> dict[str, Any]:
        """Show the reference answer and rubric for a question, with the source
        passages they come from. Local to the Mac; nothing is transmitted. Use
        after the owner has answered, or when they ask to see it."""
        return await call(api.post("/api/tutor/reveal", {"question_id": question_id}))

    @mcp.tool(annotations=TRANSMITS, meta=GRADED)
    async def tutor_grade(question_id: QuestionId, answer: Answer) -> dict[str, Any]:
        """Grade the owner's answer against the question's reference answer and
        rubric: the question, its reference and rubric, and the typed answer,
        and nothing else. If the reply carries `pending`, this workspace runs
        in host mode and YOU are the grader: follow pending.rules using only
        pending.material, produce one JSON object matching pending.output_schema
        (outcome, feedback, strengths, missing_or_unsafe, improved_answer,
        uncertainty), and call tutor_record_grade with it. Do not grade from
        your own knowledge where the rubric is silent; use uncertainty. Without
        `pending`, the Mac's own model connection grades and the result is the
        feedback itself. If grading is unavailable the result says so and
        includes the reference so the owner can use tutor_self_assess."""
        try:
            return await api.post(
                "/api/tutor/grade", {"question_id": question_id, "answer": answer}
            )
        except ApiError as exc:
            if exc.status == 503:
                revealed = await call(
                    api.post("/api/tutor/reveal", {"question_id": question_id})
                )
                return {
                    "attempt": None,
                    "graded": False,
                    "unavailable": exc.message,
                    "self_assess_available": True,
                    "question": revealed.get("question"),
                }
            raise
        finally:
            pass

    @mcp.tool(annotations=WRITE, meta=GRADED)
    async def tutor_record_grade(
        turn_id: Annotated[str, Field(max_length=64, description="The pending grading turn's turn_id.")],
        result: Annotated[
            dict[str, Any],
            Field(
                description=(
                    "Your grade as one JSON object matching the turn's output_schema: "
                    "outcome (correct, partially_correct, incorrect or unable_to_grade), "
                    "feedback, strengths, missing_or_unsafe, improved_answer, uncertainty."
                )
            ),
        ],
    ) -> dict[str, Any]:
        """Record the grade you produced for a pending grading turn (host mode).
        Vademecum validates it against the schema and refuses it if the
        question changed or stopped being askable while you graded; nothing is
        recorded in that case and the reply says so. On success the attempt is
        stored as a model grade and the reference answer is returned."""
        return await call(
            api.post("/api/tutor/grade/submit", {"turn_id": turn_id, "result": result})
        )

    @mcp.tool(annotations=WRITE, meta=GRADED)
    async def tutor_self_assess(
        question_id: QuestionId,
        answer: Answer,
        outcome: Annotated[
            Literal["correct", "partially_correct", "incorrect"],
            Field(description="The owner's own judgment after reading the reference."),
        ],
    ) -> dict[str, Any]:
        """Record the owner's own judgment of their answer when model grading is
        unavailable. Stored as self-assessed, clearly apart from a grade. Only
        the owner decides the outcome; never choose it for them."""
        return await call(
            api.post(
                "/api/tutor/self-assess",
                {"question_id": question_id, "answer": answer, "outcome": outcome},
            )
        )

    @mcp.tool(annotations=WRITE, meta=CARD_CALLABLE)
    async def tutor_advance(question_id: QuestionId) -> dict[str, Any]:
        """Mark the current question served and move to the next one in the
        cycle. Call after the owner is done with a question, whether graded,
        self-assessed or skipped."""
        return await call(api.post("/api/tutor/advance", {"question_id": question_id}))

    @mcp.tool(annotations=READ)
    async def tutor_history(
        limit: Annotated[int, Field(ge=1, le=200)] = 10,
    ) -> dict[str, Any]:
        """Recent attempts, newest first: what was asked, the outcome, who
        graded it (model or self), and the feedback. The owner's typed answers
        are not included."""
        attempts = await call(api.get("/api/tutor/history", params={"limit": limit}))
        return {"count": len(attempts), "items": attempts}
