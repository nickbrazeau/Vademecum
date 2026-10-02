"""Tutor: the cycle, grading, reference reveal, and the self-assessment fallback."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, status

from ..appserver.errors import BridgeError
from ..model import grading
from ..storage import learning, tutor
from ..storage.sources import ConflictError
from . import schemas
from .deps import get_connection, get_host_turns, get_model_mode, get_turn_factory

router = APIRouter(prefix="/tutor", tags=["tutor"])

# Host mode (ADR 0009): the desk cannot grade, and says where grading happens.
HOST_MODE_MESSAGE = (
    "Grading happens in ChatGPT for this workspace. Open Vademecum in ChatGPT and "
    "ask it to grade this answer, or record your own assessment here. Your answer "
    "is still here."
)


@router.get("")
def overview(
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
) -> dict:
    data = tutor.overview(connection).as_dict()
    data["disclosure"] = grading.disclosure(mode).as_dict()
    data["mode"] = mode
    return data


@router.get("/next")
def next_question(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    """The question to ask now. Idempotent, so a refresh keeps your place."""
    return tutor.next_question(connection).as_dict(include_reference=False)


@router.post("/advance")
def advance(
    payload: schemas.TutorAdvance,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Mark the current question served and hand back the next one."""
    learning.get_question(connection, payload.question_id)
    return tutor.advance_question(connection, payload.question_id).as_dict(include_reference=False)


@router.post("/reveal")
def reveal(
    payload: schemas.TutorReveal,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Show the reference answer. Local only -- nothing is transmitted."""
    question = learning.get_question(connection, payload.question_id)
    return {"question": question.as_dict(include_reference=True)}


@router.post("/grade")
async def grade(
    payload: schemas.TutorGrade,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Grade an answer. The only route in Tutor that transmits anything.

    In host mode nothing is transmitted by this process: the same four things
    are filed as a pending turn for the learner's ChatGPT, and the result comes
    back through ``/tutor/grade/submit``. The reply is shaped as a refusal so
    the desk shows the message and keeps the typed answer.
    """
    host_turns = get_host_turns(request)
    if host_turns is not None:
        try:
            turn, question = grading.prepare_grade(
                connection, host_turns, question_id=payload.question_id, answer=payload.answer
            )
        except grading.PatientSpecificRequest as exc:
            return _boundary(connection, payload.question_id, exc.message)
        except tutor.NotEligible as exc:
            return _refused(connection, payload.question_id, exc.reason, "not_eligible")
        return {
            "attempt": None,
            "refused": "host_mode",
            "message": HOST_MODE_MESSAGE,
            "pending": turn.as_dict(),
            "question": question.as_dict(include_reference=False),
        }
    try:
        attempt, question = await grading.grade(
            connection,
            question_id=payload.question_id,
            answer=payload.answer,
            turn_factory=get_turn_factory(request),
        )
    except grading.PatientSpecificRequest as exc:
        # Not an error: a boundary. 422 rather than 5xx, with the educational
        # explanation and a reveal so the session is not simply blocked.
        return _boundary(connection, payload.question_id, exc.message)
    except tutor.NotEligible as exc:
        return _refused(connection, payload.question_id, exc.reason, "not_eligible")
    except grading.StaleGrade as exc:
        return _refused(connection, payload.question_id, exc.reason, "stale")
    except BridgeError as exc:
        from fastapi import HTTPException

        # 503 is what the interface branches on to offer self-assessment.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=grading.unavailable_reason(exc),
        ) from exc

    return {
        "attempt": attempt.as_dict(),
        "question": question.as_dict(include_reference=True),
    }


def _boundary(connection: sqlite3.Connection, question_id: str, message: str) -> dict:
    question = learning.get_question(connection, question_id)
    return {
        "attempt": None,
        "refused": "patient_specific",
        "message": message,
        "question": question.as_dict(include_reference=True),
    }


def _refused(
    connection: sqlite3.Connection, question_id: str, message: str, code: str
) -> dict:
    question = learning.get_question(connection, question_id)
    return {
        "attempt": None,
        "refused": code,
        "message": message,
        "question": question.as_dict(include_reference=False),
    }


@router.post("/grade/submit")
def grade_submit(
    payload: schemas.TurnSubmission,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Record ChatGPT's grade for a pending turn (host mode only, ADR 0009).

    Validated against the grading schema, checked against the snapshot of what
    was asked, and recorded as a model grade -- exactly what the Codex path
    does after its await.
    """
    host_turns = get_host_turns(request)
    if host_turns is None:
        raise ConflictError(
            "host_mode_only",
            "This Vademecum grades through its own model turns; there is nothing to submit.",
        )
    turn = host_turns.get(payload.turn_id)
    question_id = turn.scope_id if turn is not None and turn.scope_kind == "question" else ""
    try:
        attempt, question = grading.record_grade(
            connection, host_turns, turn_id=payload.turn_id, result=payload.result
        )
    except tutor.NotEligible as exc:
        return _refused(connection, question_id, exc.reason, "not_eligible")
    except grading.StaleGrade as exc:
        return _refused(connection, question_id, exc.reason, "stale")
    return {
        "attempt": attempt.as_dict(),
        "question": question.as_dict(include_reference=True),
    }


@router.post("/self-assess")
def self_assess(
    payload: schemas.TutorSelfAssess,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Record the learner's own judgement. Stored as self-assessed, never a grade."""
    attempt = grading.self_assess(
        connection,
        question_id=payload.question_id,
        answer=payload.answer,
        outcome=payload.outcome,
    )
    return {"attempt": attempt.as_dict()}


@router.get("/history")
def history(
    limit: int = 20, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    return [
        attempt.as_dict()
        for attempt in tutor.recent_attempts(connection, limit=max(1, min(limit, 200)))
    ]
