"""JSON output schemas for every model turn.

Each is `additionalProperties: false` with explicit lengths and enums, and is
sent as the turn's `outputSchema` *and* validated again locally on the way back
(``appserver.turns.validate_against``). Constraining generation is not the same
as trusting the result.

What no schema here has a field for is as deliberate as what it does: there is
no `verified`, no `confidence`, no `certainty`, no `guideline_grade`. A model
cannot assert that a claim is established, because there is nowhere for it to
say so. Support is decided by ``storage.learning.support_for`` from records that
had to be earned.
"""

from __future__ import annotations

from typing import Any

MAX_CLAIM = 400
MAX_DETAIL = 1200
MAX_QUOTE = 400
MAX_TOPIC = 60
MAX_PROMPT = 600
MAX_ANSWER = 1600
MAX_RUBRIC = 1200
MAX_FEEDBACK = 1200

MAX_POINTS_PER_BATCH = 12
MAX_QUESTIONS_PER_POINT = 2


def _string(maximum: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": maximum}


# --- synthesis ---------------------------------------------------------------
#
# `excerpt_id` is the opaque handle the prompt gave for each excerpt. The model
# never sees a source id, a filename path, or a segment id; it answers with the
# handle, and the caller maps it back. A handle it invented maps to nothing and
# the citation is dropped.

CITATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["excerpt_id", "quote"],
    "properties": {
        "excerpt_id": _string(40),
        # Must appear verbatim in that excerpt. Checked against the stored text,
        # not taken on trust (storage.learning.quote_matches_stored_text).
        "quote": _string(MAX_QUOTE),
    },
}

QUESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt", "reference_answer", "rubric", "citations"],
    "properties": {
        "prompt": _string(MAX_PROMPT),
        "reference_answer": _string(MAX_ANSWER),
        "rubric": _string(MAX_RUBRIC),
        "citations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": CITATION_SCHEMA,
        },
    },
}

POINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["claim", "detail", "topics", "citations", "unclear", "questions"],
    "properties": {
        "claim": _string(MAX_CLAIM),
        "detail": _string(MAX_DETAIL),
        "topics": {
            "type": "array",
            "maxItems": 5,
            "items": _string(MAX_TOPIC),
        },
        "citations": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": CITATION_SCHEMA,
        },
        # The only thing the model may say about certainty, and it can only
        # lower it: `true` means the material was unclear. There is no field in
        # which it can claim a thing is established.
        "unclear": {"type": "boolean"},
        "questions": {
            "type": "array",
            "maxItems": MAX_QUESTIONS_PER_POINT,
            "items": QUESTION_SCHEMA,
        },
    },
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["points", "search_topics"],
    "properties": {
        "points": {
            "type": "array",
            "maxItems": MAX_POINTS_PER_BATCH,
            "items": POINT_SCHEMA,
        },
        # Short public topic words for the literature check. These are the ONLY
        # thing derived from the material that may leave for a provider, and
        # they are validated and length-capped before they can.
        "search_topics": {
            "type": "array",
            "maxItems": 6,
            "items": _string(MAX_TOPIC),
        },
    },
}


# --- evidence verification ---------------------------------------------------
#
# One claim against one retrieved record. The model is given the record's own
# title and abstract as supplied text and must quote from THAT text; the caller
# then checks the quote really is in it. A relation without a locatable quote is
# discarded, which is what makes a fabricated PMID worthless here.

EVIDENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation", "quote", "reasoning"],
    "properties": {
        "relation": {
            "type": "string",
            "enum": ["supports", "contradicts", "unclear", "unrelated"],
        },
        "quote": _string(MAX_QUOTE),
        "reasoning": _string(600),
    },
}


# --- question assessment -----------------------------------------------------
#
# The second, independent gate (see storage/learning.py). This turn sees the
# question, its reference answer, its rubric and the cited passage, and judges
# whether the passage actually supports the answer -- a different question from
# "is the quote real", which the caller has already checked mechanically.

ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "problems", "notes"],
    "properties": {
        "verdict": {"type": "string", "enum": ["sound", "unsound"]},
        "problems": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "string",
                "enum": [
                    "answer_not_in_passage",
                    "question_ambiguous",
                    "question_unanswerable",
                    "answer_incomplete",
                    "answer_unsafe",
                    "rubric_mismatch",
                    "patient_specific",
                ],
            },
        },
        "notes": _string(600),
    },
}


# --- grading -----------------------------------------------------------------

GRADING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "outcome",
        "feedback",
        "strengths",
        "missing_or_unsafe",
        "improved_answer",
        "uncertainty",
    ],
    "properties": {
        "outcome": {
            "type": "string",
            "enum": ["correct", "partially_correct", "incorrect", "unable_to_grade"],
        },
        "feedback": _string(MAX_FEEDBACK),
        "strengths": _string(MAX_FEEDBACK),
        "missing_or_unsafe": _string(MAX_FEEDBACK),
        "improved_answer": _string(MAX_ANSWER),
        # The reference-gap flag AGENTS.md requires: set when the rubric did not
        # cover what the learner said, so a low mark is not read as a verdict.
        "uncertainty": _string(600),
    },
}


# --- exam reports (ADR 0020) ----------------------------------------------------
#
# A score report read into content areas. `quote` must appear verbatim in the
# report's own text (checked by the server); `specialty` is one of the ids the
# prompt lists, or "" when none fits. Nothing here is a judgement about the
# learner: it is what the report says, located.

MAX_AREAS_PER_REPORT = 60

AREA_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topic", "specialty", "standing", "quote", "note"],
    "properties": {
        "topic": _string(MAX_TOPIC),
        "specialty": _string(40),
        "standing": {"type": "string", "enum": ["below", "at", "above"]},
        "quote": _string(200),
        "note": _string(200),
    },
}

REPORT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["areas"],
    "properties": {
        "areas": {"type": "array", "maxItems": MAX_AREAS_PER_REPORT, "items": AREA_SCHEMA},
    },
}


# --- filing flags (ADR 0021) --------------------------------------------------------

MAX_FLAGS_PER_FILING = 50

FILED_FLAG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "topic", "specialty"],
    "properties": {
        "id": _string(64),
        "topic": _string(MAX_TOPIC),
        "specialty": _string(40),
    },
}

FLAGS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["flags"],
    "properties": {
        "flags": {"type": "array", "maxItems": MAX_FLAGS_PER_FILING, "items": FILED_FLAG_SCHEMA},
    },
}


# --- a case's study notes (ADR 0022) ---------------------------------------------
#
# Each teaching point carries the verbatim quote it rests on; the server drops
# a point whose quote is not in the public text, so a title alone yields no
# points, only think-first prompts. There is no field for a diagnosis.

MAX_CASE_POINTS = 6
MAX_CASE_PROMPTS = 4

CASE_POINT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["point", "quote"],
    "properties": {
        "point": _string(300),
        "quote": _string(MAX_QUOTE),
    },
}

CASE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["one_liner", "teaching_points", "think_first", "specialty", "credit"],
    "properties": {
        "one_liner": _string(200),
        "teaching_points": {"type": "array", "maxItems": MAX_CASE_POINTS, "items": CASE_POINT_SCHEMA},
        "think_first": {"type": "array", "maxItems": MAX_CASE_PROMPTS, "items": _string(200)},
        "specialty": _string(40),
        "credit": _string(200),
    },
}
