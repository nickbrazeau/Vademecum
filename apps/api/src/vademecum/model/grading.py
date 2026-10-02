"""Tutor grading: one turn, reference-based, sending only what is on screen.

Exactly four things are transmitted: the question, its reference answer, its
rubric, and the learner's typed answer. Not the source excerpts, not the other
questions, not the learning point, not any history. The disclosure the interface
shows is a description of this function, and this function is the reason it is
true.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable

# In host mode (ADR 0009) the same four things are handed to the learner's
# own ChatGPT through a pending turn instead of a Codex turn; see the two
# request-scoped halves at the end of this module.

from ..appserver.errors import BridgeError
from ..storage import learning, tutor
from . import prompts, schemas

# Phrases that mean "decide about this actual patient". Deliberately a small,
# blunt list: this is a prompt for a boundary conversation, NOT a detector, and
# it is not presented as one. Missing a case costs nothing that the grading
# instructions and the uncertainty field do not already cover.
PATIENT_SPECIFIC_HINTS = (
    "my patient",
    "this patient",
    "a patient of mine",
    "should i give",
    "should i start",
    "should i stop",
    "what should i do for",
    "on the ward now",
    "in front of me",
    "right now in clinic",
)

PATIENT_SPECIFIC_NOTICE = (
    "This reads as a question about a specific patient. Vademecum is an "
    "educational tool and cannot advise on an individual's care — it has no "
    "clinical context, no examination and no responsibility for the outcome. "
    "Nothing was sent to a model. Please restate it as a general learning "
    "question, without identifiers, or ask a colleague or the relevant "
    "specialist about this patient."
)


class PatientSpecificRequest(RuntimeError):
    """A request that reads as being about an individual patient."""

    def __init__(self) -> None:
        super().__init__("patient_specific")
        self.message = PATIENT_SPECIFIC_NOTICE


@dataclass(frozen=True)
class Disclosure:
    """Exactly what a Grade would transmit, for the interface to show first."""

    headline: str
    bullets: tuple[str, ...]
    destination: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "bullets": list(self.bullets),
            "destination": self.destination,
        }


GRADING_DISCLOSURE = Disclosure(
    headline="Grading sends four things, and nothing else.",
    bullets=(
        "The question on screen.",
        "Its reference answer and rubric.",
        "The answer you typed.",
        "Nothing from your other material, your other answers, or your files.",
    ),
    destination=(
        "Sent to OpenAI through the Codex process on this Mac, using your ChatGPT "
        "sign-in. No API key is used."
    ),
)

# Host mode (ADR 0009): the same four things, handed to the learner's own
# ChatGPT inside their conversation. Vademecum sends nothing to a model.
HOST_GRADING_DISCLOSURE = Disclosure(
    headline="Grading shows your ChatGPT four things, and nothing else.",
    bullets=GRADING_DISCLOSURE.bullets,
    destination=(
        "Graded by your own ChatGPT, inside your conversation, on your ChatGPT "
        "license. Vademecum sends nothing to any model provider and holds no API key."
    ),
)


def disclosure(mode: str) -> Disclosure:
    return HOST_GRADING_DISCLOSURE if mode == "host" else GRADING_DISCLOSURE


def looks_patient_specific(text: str) -> bool:
    """A blunt check for an individual-patient request.

    Not a safety detector and not described as one. AGENTS.md asks for the
    educational boundary to be shown when a prompt reads this way; a
    conservative substring check does that for the obvious phrasings, and the
    grading instructions plus the `uncertainty` field handle the rest without
    anyone pretending the coverage is complete.
    """
    lowered = " ".join(text.lower().split())
    return any(hint in lowered for hint in PATIENT_SPECIFIC_HINTS)


class StaleGrade(RuntimeError):
    """The question changed, or stopped being askable, while grading ran."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class GradeSnapshot:
    """Exactly what was asked and answered, captured before any model sees it.

    A model turn takes seconds -- or, in host mode, as long as the learner's
    ChatGPT takes -- and in that window a source can be excluded or a run can
    rewrite the reference answer. Recording a result against whatever the
    question says *now* would attach a grade to text the model never saw. The
    snapshot is what makes that detectable.
    """

    question_id: str
    version: int
    prompt: str
    reference_answer: str
    rubric: str
    answer: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "version": self.version,
            "prompt": self.prompt,
            "reference_answer": self.reference_answer,
            "rubric": self.rubric,
            "answer": self.answer,
        }


def snapshot_for_grade(
    connection: sqlite3.Connection, *, question_id: str, answer: str
) -> tuple[GradeSnapshot, learning.Question]:
    """Check eligibility and the patient-specific boundary, then snapshot."""
    question = tutor.ensure_gradable(connection, question_id)
    if looks_patient_specific(answer):
        raise PatientSpecificRequest()
    snapshot = GradeSnapshot(
        question_id=question_id,
        version=question.version,
        prompt=question.prompt,
        reference_answer=question.reference_answer,
        rubric=question.rubric,
        answer=answer,
    )
    return snapshot, question


def record_graded(
    connection: sqlite3.Connection, snapshot: GradeSnapshot, payload: dict[str, Any]
) -> tutor.Attempt:
    """Record a validated grade, refusing if the question moved meanwhile."""
    current = learning.get_question(connection, snapshot.question_id)
    if current.version != snapshot.version or current.reference_answer != snapshot.reference_answer:
        raise StaleGrade(
            "This question was rebuilt while it was being graded, so the result "
            "would have been about a different reference answer. Nothing was "
            "recorded. Ask for the question again."
        )
    if not learning.is_still_eligible(connection, snapshot.question_id):
        raise StaleGrade(
            learning.hold_reason_for(connection, snapshot.question_id)
            + " Nothing was recorded."
        )
    return tutor.record_attempt(
        connection,
        question_id=snapshot.question_id,
        outcome=payload["outcome"],
        graded_by="model",
        answer=snapshot.answer,
        feedback=payload.get("feedback", ""),
        strengths=payload.get("strengths", ""),
        missing_or_unsafe=payload.get("missing_or_unsafe", ""),
        improved_answer=payload.get("improved_answer", ""),
        uncertainty=payload.get("uncertainty", ""),
        asked_version=snapshot.version,
        asked_prompt=snapshot.prompt,
        asked_reference=snapshot.reference_answer,
        asked_rubric=snapshot.rubric,
    )


async def grade(
    connection: sqlite3.Connection,
    *,
    question_id: str,
    answer: str,
    turn_factory: Callable[[], Any],
) -> tuple[tutor.Attempt, learning.Question]:
    """Grade one answer through a turn runner (Codex mode).

    Raises rather than inventing or misattributing a result. Eligibility is
    checked twice against the live database: once before the turn and once
    after it, against the snapshot.
    """
    snapshot, question = snapshot_for_grade(connection, question_id=question_id, answer=answer)
    runner = turn_factory()
    reply = await runner.run(
        instructions=prompts.BASE_INSTRUCTIONS,
        developer_instructions=prompts.GRADING_DEVELOPER,
        prompt=prompts.grading_prompt(
            question=snapshot.prompt,
            reference_answer=snapshot.reference_answer,
            rubric=snapshot.rubric,
            answer=answer,
        ),
        output_schema=schemas.GRADING_SCHEMA,
    )
    attempt = record_graded(connection, snapshot, reply.payload)
    return attempt, question


# --- host mode (ADR 0009): the same grade, in two request-scoped halves --------


def prepare_grade(
    connection: sqlite3.Connection, host_turns: Any, *, question_id: str, answer: str
) -> tuple[Any, learning.Question]:
    """File a pending grading turn for the learner's ChatGPT.

    The snapshot travels with the turn as its context, so the recording half
    can make the same staleness checks the Codex path makes after its await.
    """
    snapshot, question = snapshot_for_grade(connection, question_id=question_id, answer=answer)
    turn = host_turns.create(
        kind="grading",
        scope_kind="question",
        scope_id=question_id,
        instructions=prompts.BASE_INSTRUCTIONS,
        rules=prompts.GRADING_DEVELOPER,
        material=prompts.grading_prompt(
            question=snapshot.prompt,
            reference_answer=snapshot.reference_answer,
            rubric=snapshot.rubric,
            answer=answer,
        ),
        output_schema=schemas.GRADING_SCHEMA,
        context=snapshot.as_dict(),
    )
    return turn, question


def record_grade(
    connection: sqlite3.Connection, host_turns: Any, *, turn_id: str, result: dict[str, Any]
) -> tuple[tutor.Attempt, learning.Question]:
    """Accept ChatGPT's grade for a pending turn, once, if it validates."""
    turn, context = host_turns.submit(turn_id, result)
    if turn.kind != "grading":
        from .host import REFUSALS, SubmissionRefused

        raise SubmissionRefused("unknown_turn", REFUSALS["unknown_turn"])
    snapshot = GradeSnapshot(**context)
    attempt = record_graded(connection, snapshot, result)
    return attempt, learning.get_question(connection, snapshot.question_id)


def self_assess(
    connection: sqlite3.Connection, *, question_id: str, answer: str, outcome: str
) -> tutor.Attempt:
    """Record the learner's own judgement when the model was unavailable.

    Stored as `self_assessed` with `graded_by='self'` whatever outcome they
    chose, so nothing downstream can read a self-assessment as a grade. The
    interface labels it the same way.
    """
    if outcome not in {"correct", "partially_correct", "incorrect"}:
        raise ValueError(f"unknown self-assessment {outcome!r}")
    return tutor.record_attempt(
        connection,
        question_id=question_id,
        outcome="self_assessed",
        graded_by="self",
        answer=answer,
        feedback=f"You judged this yourself as: {outcome.replace('_', ' ')}.",
        uncertainty="The model did not grade this. It is your own assessment.",
    )


# Grading-specific failure copy. Deliberately NOT reused from the build's
# vocabulary: a learner who pressed Grade should not be told about piles or
# batches, and -- crucially -- must not be handed a no-transmission guarantee we
# cannot make. Once the turn has been dispatched we do not know how far it got.
GRADING_FAILURE = {
    # The same category can come from preflight or an unauthorized reply after
    # dispatch. Category alone cannot establish whether a prompt was sent.
    "signed_out": (
        "Codex needs a ChatGPT sign-in. Sign in on the Model page and try again. "
        "We cannot tell from this error whether the request was received; "
        "nothing was graded or recorded, and your answer is still here."
    ),
    "unsupported_credential": (
        "Codex is signed in with a credential Vademecum will not use — only "
        "ChatGPT sign-in is supported, and no API key is ever used. Nothing was "
        "sent. Sign in with ChatGPT on the Model page and try again."
    ),
    "account_unreadable": (
        "Vademecum could not confirm which account Codex is signed in with, so it "
        "did not send anything. Open the Model page to check the connection, then "
        "try again."
    ),
    "codex_not_found": (
        "Codex is not installed where Vademecum expects it, so nothing was sent. "
        "Install the ChatGPT app or the Codex CLI and try again."
    ),
    "spawn_failed": "Codex would not start on this Mac, so nothing was sent.",
    "not_started": "The Codex connection is not running, so nothing was sent.",
    # Dispatched: we genuinely cannot say what was received.
    "timeout": (
        "The grading request timed out. We cannot tell from this error whether "
        "your answer was received; nothing was graded or recorded. "
        "Your answer is still here, and you can retry."
    ),
    "process_exited": (
        "The Codex connection stopped part-way through grading. We cannot tell how "
        "far the request got; nothing was graded or recorded."
    ),
    "write_failed": (
        "The Codex connection dropped mid-request. We cannot tell how far it got; "
        "nothing was graded or recorded."
    ),
    "unavailable": (
        "The Codex connection was lost. If the request had already left this Mac we "
        "cannot tell how far it got; nothing was graded or recorded."
    ),
    "rate_limited": (
        "Your Codex usage limit has been reached, so this answer was not graded. "
        "Your answer is still here."
    ),
    "context_exceeded": (
        "This question and answer were too long for the model to take in one go. "
        "Nothing was graded or recorded."
    ),
    "invalid_output": (
        "The model replied with something that did not match the required "
        "structure, so it was rejected rather than shown to you as a grade."
    ),
    "unsafe_tool_event": (
        "The grading turn tried to do something Vademecum does not allow, so it was "
        "stopped and nothing was recorded. This is a safety stop, not your answer."
    ),
    "output_too_large": (
        "The model's reply was far larger than a grade should be, so it was "
        "rejected rather than shown to you."
    ),
    "interrupted": "Grading was stopped before it finished. Nothing was recorded.",
    "protocol": (
        "Codex answered with something this version of Vademecum could not read, so "
        "nothing was graded or recorded."
    ),
    "refused": "Codex refused the grading request. Nothing was graded or recorded.",
    "method_not_found": (
        "This version of Codex does not offer what Vademecum asked for. Updating "
        "Codex may fix it. Nothing was graded or recorded."
    ),
}

# Every category that is only reachable BEFORE the prompt is written to the
# child. Only these may promise that nothing was sent.
PRE_DISPATCH = frozenset(
    {
        "unsupported_credential",
        "account_unreadable",
        "codex_not_found",
        "spawn_failed",
        "not_started",
    }
)

UNKNOWN_FAILURE = (
    "Grading did not complete. If the request had already left this Mac we cannot "
    "tell how far it got; nothing was graded or recorded. Your answer is still here."
)


def unavailable_reason(error: BridgeError) -> str:
    """Plain language for a grading failure, keyed by category only.

    The default is deliberately the uncertain one. A category this build has not
    seen before might well be reachable after the prompt was written, so the
    fallback cannot be a promise.
    """
    return GRADING_FAILURE.get(error.category, UNKNOWN_FAILURE)


def promised_no_transmission(category: str) -> bool:
    """Whether a category may honestly claim nothing was sent. Tested."""
    return category in PRE_DISPATCH
