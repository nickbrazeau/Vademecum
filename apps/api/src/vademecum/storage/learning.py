"""The generated learning bank: points, their provenance, and their questions.

Two rules, both easy to lose.

**How much the owner trusts a source never decides whether a claim is
supported.** A pile marked "High" produces `source_supported` points, exactly as
a pile marked "Low" does. `evidence_supported` is reachable only through
``apply_evidence``, which needs a record a provider actually returned and a
quote actually found in it. :func:`support_for` takes no confidence argument at
all -- not because it is careful, but because it has nothing to be careless
with.

**Tutor asks only what has been checked from outside your own material.**
Eligibility requires four independent things to hold at once:

1. the point is `evidence_supported` -- published literature was retrieved and
   matched, so the claim does not rest on the uploaded material alone;
2. nothing linked to it is retracted, contradicting, or awaiting re-review;
3. every quoted anchor is present in the stored segment it names, and its source
   is still included; and
4. the question, its reference answer and its rubric passed a *separate*
   assessment pass that judged them against the cited text -- ``assessment ==
   'sound'``.

Point 4 is not point 3 restated. A question can quote a real passage perfectly
and still be ambiguous, unanswerable, or have a reference answer the passage
does not support. Quoting accurately is necessary and nowhere near sufficient.

`source_supported` material is genuinely useful and is kept and shown -- as
draft learning material, visibly awaiting verification. It is simply never
asked.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from ..db import transaction
from .common import NotFoundError, content_hash, new_id, utc_now

# --- support vocabulary ------------------------------------------------------

SOURCE_SUPPORTED = "source_supported"
EVIDENCE_SUPPORTED = "evidence_supported"
UNCERTAIN = "uncertain"
CONFLICTING = "conflicting"

SUPPORTS: tuple[str, ...] = (SOURCE_SUPPORTED, EVIDENCE_SUPPORTED, UNCERTAIN, CONFLICTING)

GRADE_NONE = "none"
GRADE_ABSTRACT = "abstract_only"
GRADE_FULL = "guideline_or_full_text"

# The sentence the interface shows for each. None of them says "verified", and
# none of them says a human has looked at it, because none of that is true.
SUPPORT_LABEL = {
    SOURCE_SUPPORTED: "Source-supported",
    EVIDENCE_SUPPORTED: "Evidence-supported, machine reviewed",
    UNCERTAIN: "Uncertain",
    CONFLICTING: "Conflicting",
}

SUPPORT_MEANING = {
    SOURCE_SUPPORTED: (
        "Draft. Your own uploaded material says this, at the location cited, but "
        "nothing outside your material has been checked yet and no human has "
        "reviewed it. Tutor does not ask draft material."
    ),
    EVIDENCE_SUPPORTED: (
        "Published literature returned by PubMed was compared against this "
        "claim and matched, quote and identifier included. Machine-reviewed "
        "only: no human has approved it and it is not clinically validated."
    ),
    UNCERTAIN: (
        "Your material is unclear on this, or the check against published "
        "literature could not be completed. It stays out of Tutor."
    ),
    CONFLICTING: (
        "Two of your sources disagree, or your material and the literature "
        "disagree. It stays out of Tutor until you look at it."
    ),
}

GRADE_LABEL = {
    GRADE_NONE: "No external evidence",
    GRADE_ABSTRACT: "Abstract only",
    GRADE_FULL: "Guideline or full text",
}

# The ONLY support level Tutor will ask. `source_supported` is deliberately
# absent: material that rests on the uploaded file alone is draft material, kept
# and shown and visibly held, never asked. Widening this set is the single
# change that would break the product's central promise, so it is one frozen
# name in one place with a test asserting its contents.
ELIGIBLE_SUPPORTS = frozenset({EVIDENCE_SUPPORTED})

# Why a question built from good material is still not asked yet. Shown to the
# owner so "held" never reads as "broken".
AWAITING_EVIDENCE = (
    "Held: this rests on your uploaded material alone. Tutor asks only questions "
    "whose claim was also matched against published literature. Add a literature "
    "topic that covers it, or run the check again once one exists."
)
AWAITING_ASSESSMENT = (
    "Held: the question and its reference answer have not been independently "
    "assessed against the passage they cite."
)
UNSOUND_QUESTION = (
    "Held: an independent assessment found the question or its reference answer "
    "was not supported by the passage it cites."
)


class ConflictError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Citation:
    source_id: str
    display_name: str
    confidence: str
    locator: str
    quote: str
    segment_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceRef:
    record_id: str
    relation: str
    quote: str
    evidence_grade: str
    pmid: str | None
    doi: str | None
    title: str
    journal: str
    published_on: str | None
    retracted: bool
    corrected: bool
    url: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LearningPoint:
    id: str
    pile_id: str
    generation_id: str | None
    claim: str
    detail: str
    support: str
    evidence_grade: str
    review_state: str
    held: bool
    hold_reason: str
    created_at: str
    updated_at: str
    topics: tuple[str, ...] = field(default_factory=tuple)
    citations: tuple[Citation, ...] = field(default_factory=tuple)
    evidence: tuple[EvidenceRef, ...] = field(default_factory=tuple)
    question_count: int = 0
    # Which numbered evidence basis the `evidence` above belongs to. Superseded
    # links are retained as audit history and are not returned here.
    evidence_basis: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pile_id": self.pile_id,
            "generation_id": self.generation_id,
            "claim": self.claim,
            "detail": self.detail,
            "support": self.support,
            "support_label": SUPPORT_LABEL[self.support],
            "support_meaning": SUPPORT_MEANING[self.support],
            "evidence_grade": self.evidence_grade,
            "evidence_grade_label": GRADE_LABEL.get(self.evidence_grade, self.evidence_grade),
            "review_state": self.review_state,
            "held": self.held,
            "hold_reason": self.hold_reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "topics": list(self.topics),
            "citations": [citation.as_dict() for citation in self.citations],
            "evidence": [reference.as_dict() for reference in self.evidence],
            "question_count": self.question_count,
            "evidence_basis": self.evidence_basis,
        }


@dataclass(frozen=True)
class Anchor:
    source_id: str
    display_name: str
    locator: str
    quote: str
    segment_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Question:
    id: str
    learning_point_id: str
    pile_id: str
    generation_id: str | None
    prompt: str
    reference_answer: str
    rubric: str
    status: str
    hold_reason: str
    created_at: str
    updated_at: str
    assessment: str = "unassessed"
    assessment_notes: str = ""
    version: int = 1
    retired_at: str | None = None
    retired_reason: str = ""
    anchors: tuple[Anchor, ...] = field(default_factory=tuple)
    claim: str = ""
    support: str = SOURCE_SUPPORTED
    evidence_grade: str = GRADE_NONE
    topics: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self, *, include_reference: bool = True) -> dict[str, Any]:
        """The question as the API returns it.

        ``include_reference`` is what keeps the reference answer off the screen
        until the learner has answered or asked for it. The rubric goes with it:
        a rubric is a list of the points the answer should make, so showing it
        early is showing the answer in a different font.
        """
        data: dict[str, Any] = {
            "id": self.id,
            "learning_point_id": self.learning_point_id,
            "pile_id": self.pile_id,
            "prompt": self.prompt,
            "status": self.status,
            "assessment": self.assessment,
            "version": self.version,
            "hold_reason": self.hold_reason,
            "created_at": self.created_at,
            "claim": self.claim,
            "support": self.support,
            "support_label": SUPPORT_LABEL.get(self.support, self.support),
            "support_meaning": SUPPORT_MEANING.get(self.support, ""),
            "evidence_grade": self.evidence_grade,
            "evidence_grade_label": GRADE_LABEL.get(self.evidence_grade, self.evidence_grade),
            "topics": list(self.topics),
            "anchors": [anchor.as_dict() for anchor in self.anchors],
        }
        if include_reference:
            data["reference_answer"] = self.reference_answer
            data["rubric"] = self.rubric
        return data


# --- support decision --------------------------------------------------------


def support_for(
    *,
    source_citations: int,
    evidence_relations: tuple[str, ...],
    model_uncertain: bool,
) -> tuple[str, str]:
    """Decide a point's support, and why it is held if it is.

    Note the signature. There is no `confidence` parameter, and there is no
    `model_says_verified` parameter either. A model asserting that something is
    established, and an owner marking a pile "High", are the two ways this
    decision could be corrupted, and neither is reachable from here.

    Returns ``(support, hold_reason)``. A non-empty hold reason means the point
    and its questions stay out of Tutor.
    """
    if any(relation == "contradicts" for relation in evidence_relations):
        return (
            CONFLICTING,
            "Published literature returned for this topic contradicts the claim.",
        )
    if source_citations == 0:
        return (
            UNCERTAIN,
            "No passage in your uploaded material could be quoted for this claim.",
        )
    if model_uncertain:
        return (
            UNCERTAIN,
            "The synthesis marked this claim as unclear in the material it read.",
        )
    if any(relation == "supports" for relation in evidence_relations):
        return EVIDENCE_SUPPORTED, ""
    return SOURCE_SUPPORTED, ""


def point_hash(claim: str) -> str:
    """The identity of a claim, for deduplication.

    Normalised hard on purpose: the same point restated with different casing,
    spacing or trailing punctuation across three lectures is one point with
    three citations, which is what the owner wants to see. Two genuinely
    different claims will not collide, because nothing is removed but layout.
    """
    normalised = " ".join(claim.lower().split()).strip(" .;:")
    return content_hash(normalised)


def question_hash(prompt: str) -> str:
    normalised = " ".join(prompt.lower().split()).strip(" .;:?")
    return content_hash(normalised)


# --- reads -------------------------------------------------------------------


def _topics(connection: sqlite3.Connection, point_id: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT topic FROM learning_point_topics WHERE learning_point_id = ? ORDER BY topic",
        (point_id,),
    ).fetchall()
    return tuple(row["topic"] for row in rows)


def _citations(connection: sqlite3.Connection, point_id: str) -> tuple[Citation, ...]:
    rows = connection.execute(
        """
        SELECT lps.source_id, lps.segment_id, lps.locator, lps.quote,
               s.display_name, s.confidence
          FROM learning_point_sources lps
          JOIN sources s ON s.id = lps.source_id
         WHERE lps.learning_point_id = ?
         ORDER BY s.display_name, lps.locator
        """,
        (point_id,),
    ).fetchall()
    return tuple(
        Citation(
            source_id=row["source_id"],
            display_name=row["display_name"],
            confidence=row["confidence"],
            locator=row["locator"],
            quote=row["quote"],
            segment_id=row["segment_id"],
        )
        for row in rows
    )


def _evidence(connection: sqlite3.Connection, point_id: str) -> tuple[EvidenceRef, ...]:
    rows = connection.execute(
        """
        SELECT el.record_id, el.relation, el.quote, el.evidence_grade,
               r.pmid, r.doi, r.title, r.journal, r.published_on,
               r.retracted, r.corrected, r.url
          FROM evidence_links el
          JOIN literature_records r ON r.id = el.record_id
         WHERE el.learning_point_id = ? AND el.superseded_at IS NULL
         ORDER BY r.published_on DESC
        """,
        (point_id,),
    ).fetchall()
    return tuple(
        EvidenceRef(
            record_id=row["record_id"],
            relation=row["relation"],
            quote=row["quote"],
            evidence_grade=row["evidence_grade"],
            pmid=row["pmid"],
            doi=row["doi"],
            title=row["title"],
            journal=row["journal"],
            published_on=row["published_on"],
            retracted=bool(row["retracted"]),
            corrected=bool(row["corrected"]),
            url=row["url"],
        )
        for row in rows
    )


def _point(connection: sqlite3.Connection, row: sqlite3.Row) -> LearningPoint:
    keys = row.keys()
    return LearningPoint(
        id=row["id"],
        pile_id=row["pile_id"],
        generation_id=row["generation_id"],
        claim=row["claim"],
        detail=row["detail"],
        support=row["support"],
        evidence_grade=row["evidence_grade"],
        review_state=row["review_state"],
        held=bool(row["held"]),
        hold_reason=row["hold_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        topics=_topics(connection, row["id"]),
        citations=_citations(connection, row["id"]),
        evidence=_evidence(connection, row["id"]),
        question_count=row["question_count"] if "question_count" in keys else 0,
        evidence_basis=row["evidence_basis"] if "evidence_basis" in keys else 1,
    )


_POINT_SELECT = """
SELECT p.*,
       (SELECT COUNT(*) FROM tutor_questions q
         WHERE q.learning_point_id = p.id AND q.status = 'eligible') AS question_count
FROM learning_points p
"""


def list_points(
    connection: sqlite3.Connection,
    *,
    pile_id: str | None = None,
    held: bool | None = None,
    limit: int | None = None,
) -> list[LearningPoint]:
    sql = _POINT_SELECT
    clauses: list[str] = []
    params: list[Any] = []
    if pile_id is not None:
        clauses.append("p.pile_id = ?")
        params.append(pile_id)
    if held is not None:
        clauses.append("p.held = ?")
        params.append(1 if held else 0)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY p.created_at DESC, p.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return [_point(connection, row) for row in connection.execute(sql, params).fetchall()]


def points_for_topic(connection: sqlite3.Connection, topic: str) -> list[LearningPoint]:
    """The unheld, machine-reviewed points filed under one topic: what a page is compiled from."""
    rows = connection.execute(
        _POINT_SELECT
        + " JOIN learning_point_topics t ON t.learning_point_id = p.id"
        " WHERE t.topic = ? AND p.held = 0 AND p.review_state = 'machine_reviewed'"
        " ORDER BY p.created_at, p.id",
        (topic,),
    ).fetchall()
    return [_point(connection, row) for row in rows]


def get_point(connection: sqlite3.Connection, point_id: str) -> LearningPoint:
    row = connection.execute(_POINT_SELECT + " WHERE p.id = ?", (point_id,)).fetchone()
    if row is None:
        raise NotFoundError("learning point", point_id)
    return _point(connection, row)


def _anchors(connection: sqlite3.Connection, question_id: str) -> tuple[Anchor, ...]:
    rows = connection.execute(
        """
        SELECT a.source_id, a.segment_id, a.locator, a.quote, s.display_name
          FROM tutor_question_anchors a
          JOIN sources s ON s.id = a.source_id
         WHERE a.question_id = ?
         ORDER BY s.display_name, a.locator
        """,
        (question_id,),
    ).fetchall()
    return tuple(
        Anchor(
            source_id=row["source_id"],
            display_name=row["display_name"],
            locator=row["locator"],
            quote=row["quote"],
            segment_id=row["segment_id"],
        )
        for row in rows
    )


_QUESTION_SELECT = """
SELECT q.*, p.claim AS claim, p.support AS support, p.evidence_grade AS evidence_grade
FROM tutor_questions q
JOIN learning_points p ON p.id = q.learning_point_id
"""


def _question(connection: sqlite3.Connection, row: sqlite3.Row) -> Question:
    return Question(
        id=row["id"],
        learning_point_id=row["learning_point_id"],
        pile_id=row["pile_id"],
        generation_id=row["generation_id"],
        prompt=row["prompt"],
        reference_answer=row["reference_answer"],
        rubric=row["rubric"],
        status=row["status"],
        hold_reason=row["hold_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        assessment=row["assessment"],
        assessment_notes=row["assessment_notes"],
        version=row["version"],
        retired_at=row["retired_at"],
        retired_reason=row["retired_reason"],
        anchors=_anchors(connection, row["id"]),
        claim=row["claim"],
        support=row["support"],
        evidence_grade=row["evidence_grade"],
        topics=_topics(connection, row["learning_point_id"]),
    )


def get_question(connection: sqlite3.Connection, question_id: str) -> Question:
    row = connection.execute(
        _QUESTION_SELECT + " WHERE q.id = ?", (question_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("question", question_id)
    return _question(connection, row)


def list_questions(
    connection: sqlite3.Connection,
    *,
    pile_id: str | None = None,
    status: str | None = None,
) -> list[Question]:
    sql = _QUESTION_SELECT
    clauses: list[str] = []
    params: list[Any] = []
    if pile_id is not None:
        clauses.append("q.pile_id = ?")
        params.append(pile_id)
    if status is not None:
        clauses.append("q.status = ?")
        params.append(status)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY q.created_at DESC, q.id"
    return [_question(connection, row) for row in connection.execute(sql, params).fetchall()]


def eligible_question_ids(connection: sqlite3.Connection) -> list[str]:
    """Every question Tutor may serve, in a stable order.

    Every condition is re-read from the database on every call. Nothing here
    trusts a cached flag: ``status = 'eligible'`` is necessary but not
    sufficient, and the joins below re-prove support, review state, retraction,
    assessment and source inclusion each time. That is what makes exclusion
    effective the instant it is set -- including between a question being served
    and the same question being graded.

    Stable rather than random: the shuffle belongs to the durable cycle, and
    shuffling here too would make it unreproducible for no gain.
    """
    rows = connection.execute(
        """
        SELECT q.id FROM tutor_questions q
          JOIN learning_points p ON p.id = q.learning_point_id
         WHERE q.status = 'eligible'
           AND q.assessment = 'sound'
           AND q.retired_at IS NULL
           AND p.held = 0
           AND p.review_state = 'machine_reviewed'
           AND p.support = 'evidence_supported'
           -- at least one live supporting record: not retracted, not corrected,
           -- and not itself a notice about some other paper...
           AND EXISTS (SELECT 1 FROM evidence_links el
                         JOIN literature_records r ON r.id = el.record_id
                        WHERE el.learning_point_id = p.id
                          AND el.superseded_at IS NULL
                          AND el.relation = 'supports'
                          AND r.retracted = 0 AND r.corrected = 0
                          AND COALESCE(r.is_notice, 0) = 0)
           -- ...and nothing retracted, corrected, notice or contradicting on it.
           -- A correction is not a retraction, but it is still a reason for a
           -- person to look before the claim is asked again.
           AND NOT EXISTS (SELECT 1 FROM evidence_links el
                             JOIN literature_records r ON r.id = el.record_id
                            WHERE el.learning_point_id = p.id
                              AND el.superseded_at IS NULL
                              AND (r.retracted = 1 OR r.corrected = 1
                                   OR COALESCE(r.is_notice, 0) = 1
                                   OR el.relation = 'contradicts'))
           -- every anchor must exist and sit in an included, readable source
           AND EXISTS (SELECT 1 FROM tutor_question_anchors a WHERE a.question_id = q.id)
           AND NOT EXISTS (SELECT 1 FROM tutor_question_anchors a
                             LEFT JOIN sources s ON s.id = a.source_id
                            WHERE a.question_id = q.id
                              AND (s.id IS NULL OR s.excluded = 1 OR s.status != 'extracted'
                                   OR a.segment_id IS NULL))
           -- and so must every source the claim itself cites
           AND NOT EXISTS (SELECT 1 FROM learning_point_sources lps
                             LEFT JOIN sources s ON s.id = lps.source_id
                            WHERE lps.learning_point_id = p.id
                              AND (s.id IS NULL OR s.excluded = 1))
         ORDER BY q.created_at, q.id
        """
    ).fetchall()
    return [row["id"] for row in rows]


def is_still_eligible(connection: sqlite3.Connection, question_id: str) -> bool:
    """Re-check one question against the live database.

    Called again at grade time. A question that was eligible when it was served
    and whose source has since been excluded must not be graded as though
    nothing had changed, and a boolean carried in a session would not notice.
    """
    return question_id in set(eligible_question_ids(connection))


def hold_reason_for(connection: sqlite3.Connection, question_id: str) -> str:
    """Why this question is not eligible, in the owner's terms."""
    question = get_question(connection, question_id)
    if question.retired_at:
        return question.retired_reason or "This question has been retired."
    point = get_point(connection, question.learning_point_id)
    if point.held:
        return point.hold_reason or SUPPORT_MEANING[point.support]
    if point.review_state != "machine_reviewed":
        return (
            "Held: the evidence behind this changed and it is waiting to be "
            "checked again."
        )
    if point.support not in ELIGIBLE_SUPPORTS:
        return AWAITING_EVIDENCE if point.support == SOURCE_SUPPORTED else SUPPORT_MEANING[
            point.support
        ]
    if question.assessment == "unassessed":
        return AWAITING_ASSESSMENT
    if question.assessment == "unsound":
        return question.assessment_notes or UNSOUND_QUESTION
    excluded = connection.execute(
        "SELECT s.display_name FROM tutor_question_anchors a JOIN sources s"
        " ON s.id = a.source_id WHERE a.question_id = ? AND s.excluded = 1 LIMIT 1",
        (question_id,),
    ).fetchone()
    if excluded is not None:
        return f"Held: you excluded {excluded['display_name']}, which this question cites."
    return question.hold_reason or "Held pending verification."


def bank_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    """Counts for the honest empty state, and for Today."""
    points = connection.execute(
        "SELECT support, held, COUNT(*) AS n FROM learning_points GROUP BY support, held"
    ).fetchall()
    questions = connection.execute(
        "SELECT status, COUNT(*) AS n FROM tutor_questions GROUP BY status"
    ).fetchall()
    needs_review = connection.execute(
        "SELECT COUNT(*) AS n FROM learning_points WHERE review_state = 'needs_re_review'"
    ).fetchone()["n"]
    by_support = {support: 0 for support in SUPPORTS}
    for row in points:
        by_support[row["support"]] = by_support.get(row["support"], 0) + row["n"]
    reasons = connection.execute(
        "SELECT hold_reason, COUNT(*) AS n FROM tutor_questions"
        " WHERE status = 'held' AND hold_reason != '' GROUP BY hold_reason"
        " ORDER BY n DESC LIMIT 4"
    ).fetchall()
    return {
        # `questions_eligible` is the live query, not the stored flag: the flag
        # is a cache and the joins are the truth.
        "points": sum(row["n"] for row in points),
        "points_by_support": by_support,
        "points_held": sum(row["n"] for row in points if row["held"]),
        "questions_total": sum(row["n"] for row in questions),
        "questions_eligible": len(eligible_question_ids(connection)),
        "questions_held": sum(row["n"] for row in questions if row["status"] == "held"),
        "questions_retired": sum(row["n"] for row in questions if row["status"] == "retired"),
        "points_needing_re_review": needs_review,
        "hold_reasons": [row["hold_reason"] for row in reasons],
    }


# --- writes ------------------------------------------------------------------


@dataclass(frozen=True)
class DraftCitation:
    source_id: str
    segment_id: str | None
    locator: str
    quote: str


@dataclass(frozen=True)
class DraftPoint:
    claim: str
    detail: str
    topics: tuple[str, ...]
    citations: tuple[DraftCitation, ...]
    model_uncertain: bool = False


@dataclass(frozen=True)
class DraftQuestion:
    prompt: str
    reference_answer: str
    rubric: str
    anchors: tuple[DraftCitation, ...]
    hold_reason: str = ""


def upsert_point(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    generation_id: str,
    draft: DraftPoint,
) -> tuple[str, bool]:
    """Write a point, merging into an existing one with the same claim.

    Returns ``(point_id, created)``. Merging keeps every citation from every
    source that made the claim: the point is one row, the provenance is many,
    and the owner can see all four lectures that said it.

    Support is recomputed from what is on the row *after* the merge, so a claim
    that a second source contradicts does not stay `source_supported` because it
    was written first.
    """
    now = utc_now()
    digest = point_hash(draft.claim)
    existing = connection.execute(
        "SELECT id FROM learning_points WHERE pile_id = ? AND content_hash = ?",
        (pile_id, digest),
    ).fetchone()

    point_id = existing["id"] if existing is not None else new_id("lpt")
    created = existing is None

    with transaction(connection) as tx:
        if created:
            tx.execute(
                "INSERT INTO learning_points (id, pile_id, generation_id, claim, detail,"
                " support, evidence_grade, review_state, held, hold_reason, content_hash,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 'none', 'machine_reviewed', 1,"
                " 'Waiting for verification.', ?, ?, ?)",
                (
                    point_id,
                    pile_id,
                    generation_id,
                    draft.claim,
                    draft.detail,
                    UNCERTAIN,
                    digest,
                    now,
                    now,
                ),
            )
        else:
            tx.execute(
                "UPDATE learning_points SET generation_id = ?, detail = ?, updated_at = ?"
                " WHERE id = ?",
                (generation_id, draft.detail or "", now, point_id),
            )
        for topic in draft.topics:
            tx.execute(
                "INSERT OR IGNORE INTO learning_point_topics (learning_point_id, topic)"
                " VALUES (?, ?)",
                (point_id, topic.strip()),
            )
        for citation in draft.citations:
            tx.execute(
                "INSERT OR IGNORE INTO learning_point_sources"
                " (id, learning_point_id, source_id, segment_id, locator, quote, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("cit"),
                    point_id,
                    citation.source_id,
                    citation.segment_id,
                    citation.locator,
                    citation.quote,
                    now,
                ),
            )
    return point_id, created


def settle_point(
    connection: sqlite3.Connection,
    point_id: str,
    *,
    model_uncertain: bool = False,
    force_re_review: str = "",
) -> LearningPoint:
    """Recompute a point's support from what is recorded against it.

    Called after citations and evidence links are in place. Everything it reads
    is a row that something else had to earn: a citation exists because a quote
    matched a stored segment, an evidence link exists because a quote matched
    text a provider returned.
    """
    citations = connection.execute(
        "SELECT COUNT(*) AS n FROM learning_point_sources WHERE learning_point_id = ?",
        (point_id,),
    ).fetchone()["n"]
    links = connection.execute(
        """
        SELECT el.relation, el.evidence_grade, r.retracted, r.corrected,
               COALESCE(r.is_notice, 0) AS is_notice
          FROM evidence_links el
          JOIN literature_records r ON r.id = el.record_id
         -- The CURRENT basis only. Superseded links are the audit trail of what
         -- the claim used to rest on; they must not keep holding it after it has
         -- been re-verified against something clean.
         WHERE el.learning_point_id = ? AND el.superseded_at IS NULL
        """,
        (point_id,),
    ).fetchall()

    # A retracted paper supports nothing. It is dropped from the relations that
    # decide support and handled below as a reason to re-review.
    # A retracted paper supports nothing, and neither does a correction NOTICE
    # about another paper. Both are dropped from the relations that decide
    # support and handled below as reasons to re-review.
    live = [
        row
        for row in links
        if not row["retracted"] and not row["is_notice"]
    ]
    relations = tuple(row["relation"] for row in live)
    support, hold_reason = support_for(
        source_citations=citations,
        evidence_relations=relations,
        model_uncertain=model_uncertain,
    )

    grade = GRADE_NONE
    if support == EVIDENCE_SUPPORTED:
        grade = (
            GRADE_FULL
            if any(
                row["evidence_grade"] == GRADE_FULL and row["relation"] == "supports"
                for row in live
            )
            else GRADE_ABSTRACT
        )

    # Three distinct states, kept distinct. A correction is NOT a retraction: it
    # does not invalidate the paper, but it does mean a person should look
    # before the claim is asked again. All three hold; none auto-releases.
    retracted_links = [row for row in links if row["retracted"]]
    corrected_links = [row for row in links if row["corrected"] and not row["retracted"]]
    notice_links = [row for row in links if row["is_notice"]]

    review_state = "machine_reviewed"
    if retracted_links or corrected_links or notice_links:
        review_state = "needs_re_review"
    if not hold_reason:
        if retracted_links:
            hold_reason = (
                "A paper this point was linked to has been retracted. It is held "
                "until you re-check it."
            )
        elif corrected_links:
            hold_reason = (
                "A paper this point was linked to has been corrected (an erratum "
                "or expression of concern). That is not a retraction, but it is "
                "held until you re-check it."
            )
        elif notice_links:
            hold_reason = (
                "A record linked to this point is a correction or retraction "
                "notice about another paper, not evidence in its own right. It "
                "is held until you re-check it."
            )

    # An explicit re-review request from the literature watcher is honoured even
    # when the links themselves look clean -- otherwise settling a point would
    # quietly clear the flag that asked a person to look at it.
    if force_re_review:
        review_state = "needs_re_review"
        if not hold_reason:
            hold_reason = force_re_review

    held = 1 if hold_reason else 0
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE learning_points SET support = ?, evidence_grade = ?, review_state = ?,"
            " held = ?, hold_reason = ?, updated_at = ? WHERE id = ?",
            (support, grade, review_state, held, hold_reason, utc_now(), point_id),
        )
        # A held point's questions are held with it, so a claim that lost its
        # support cannot keep being asked.
        if held:
            tx.execute(
                "UPDATE tutor_questions SET status = 'held', hold_reason = ?, updated_at = ?"
                " WHERE learning_point_id = ? AND status = 'eligible'",
                (hold_reason, utc_now(), point_id),
            )
    return get_point(connection, point_id)


def upsert_question(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    point_id: str,
    generation_id: str,
    draft: DraftQuestion,
) -> tuple[str, bool]:
    """Write a question and its anchors, deduplicating on the prompt.

    A question starts `held`. :func:`release_question` is the only thing that
    makes it eligible, and it checks the anchors first.
    """
    now = utc_now()
    digest = question_hash(draft.prompt)
    existing = connection.execute(
        "SELECT id FROM tutor_questions WHERE pile_id = ? AND content_hash = ?",
        (pile_id, digest),
    ).fetchone()
    question_id = existing["id"] if existing is not None else new_id("tqn")
    created = existing is None
    hold_reason = draft.hold_reason or "Waiting for verification."

    with transaction(connection) as tx:
        if created:
            tx.execute(
                "INSERT INTO tutor_questions (id, learning_point_id, pile_id, generation_id,"
                " prompt, reference_answer, rubric, status, hold_reason, content_hash,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'held', ?, ?, ?, ?)",
                (
                    question_id,
                    point_id,
                    pile_id,
                    generation_id,
                    draft.prompt,
                    draft.reference_answer,
                    draft.rubric,
                    hold_reason,
                    digest,
                    now,
                    now,
                ),
            )
        else:
            # A rerun may rewrite the reference answer. The row is kept (so the
            # cycle and history keep pointing at it) but the version is bumped
            # when the content actually changed, and it drops back to
            # unassessed+held: a rewritten answer has not been assessed.
            current = tx.execute(
                "SELECT reference_answer, rubric, version FROM tutor_questions WHERE id = ?",
                (question_id,),
            ).fetchone()
            changed = (
                current["reference_answer"] != draft.reference_answer
                or current["rubric"] != draft.rubric
            )
            tx.execute(
                "UPDATE tutor_questions SET learning_point_id = ?, generation_id = ?,"
                " reference_answer = ?, rubric = ?, version = ?, updated_at = ?,"
                " assessment = CASE WHEN ? THEN 'unassessed' ELSE assessment END,"
                " assessed_at = CASE WHEN ? THEN NULL ELSE assessed_at END,"
                " status = CASE WHEN ? THEN 'held' ELSE status END,"
                " hold_reason = CASE WHEN ? THEN ? ELSE hold_reason END,"
                " retired_at = NULL, retired_reason = '' WHERE id = ?",
                (
                    point_id,
                    generation_id,
                    draft.reference_answer,
                    draft.rubric,
                    current["version"] + (1 if changed else 0),
                    now,
                    changed,
                    changed,
                    changed,
                    changed,
                    AWAITING_ASSESSMENT,
                    question_id,
                ),
            )
            tx.execute(
                "DELETE FROM tutor_question_anchors WHERE question_id = ?", (question_id,)
            )
        for anchor in draft.anchors:
            tx.execute(
                "INSERT INTO tutor_question_anchors"
                " (id, question_id, source_id, segment_id, locator, quote, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("anc"),
                    question_id,
                    anchor.source_id,
                    anchor.segment_id,
                    anchor.locator,
                    anchor.quote,
                    now,
                ),
            )
    return question_id, created


def record_assessment(
    connection: sqlite3.Connection,
    question_id: str,
    *,
    assessment: str,
    notes: str = "",
) -> Question:
    """Store the verdict of the independent question-assessment pass.

    Written only by ``model/verification.py``, after a turn that saw the
    question, its reference answer, its rubric and the cited passage, and judged
    whether the passage actually supports the answer. No route reaches this.
    """
    if assessment not in {"unassessed", "sound", "unsound"}:
        raise ValueError(f"unknown assessment {assessment!r}")
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE tutor_questions SET assessment = ?, assessment_notes = ?,"
            " assessed_at = ?, updated_at = ? WHERE id = ?",
            (assessment, notes, utc_now(), utc_now(), question_id),
        )
    return get_question(connection, question_id)


def release_question(connection: sqlite3.Connection, question_id: str) -> Question:
    """Make a question eligible, if every gate is passed.

    The gates, in order, each with its own reason:

    * the point is not held, is not awaiting re-review, and is
      `evidence_supported` -- published literature was matched to the claim;
    * no linked record is retracted and none contradicts;
    * a reference answer exists (grading is reference-based, AGENTS.md);
    * at least one anchor exists, its source is included and readable, and its
      quote is present in the stored segment it names; and
    * the independent assessment pass returned `sound`.

    The quote check catches a fabricated citation. The assessment gate catches
    the different failure where the quote is real but the question built on it
    is not answerable from it. Both are required.
    """
    question = get_question(connection, question_id)
    point = get_point(connection, question.learning_point_id)

    reason = ""
    if question.retired_at:
        reason = question.retired_reason or "This question has been retired."
    elif point.held:
        reason = point.hold_reason or "The learning point behind this question is held."
    elif point.review_state != "machine_reviewed":
        reason = "Held: the evidence behind this changed and it needs checking again."
    elif point.support == SOURCE_SUPPORTED:
        reason = AWAITING_EVIDENCE
    elif point.support not in ELIGIBLE_SUPPORTS:
        reason = SUPPORT_MEANING[point.support]
    elif not question.reference_answer.strip():
        reason = "No reference answer was produced, so this could not be graded against one."
    elif not question.anchors:
        reason = "No source location was cited for the answer."
    else:
        for anchor in question.anchors:
            source = connection.execute(
                "SELECT excluded, status FROM sources WHERE id = ?", (anchor.source_id,)
            ).fetchone()
            if source is None:
                reason = f"The source cited at {anchor.locator} is no longer present."
                break
            if source["excluded"]:
                reason = f"Held: you excluded {anchor.display_name}, which this cites."
                break
            if source["status"] != "extracted":
                reason = f"{anchor.display_name} has no readable text to check this against."
                break
            if not quote_matches_stored_text(connection, anchor.segment_id, anchor.quote):
                reason = (
                    f"The quoted passage was not found at {anchor.locator} in "
                    f"{anchor.display_name}."
                )
                break
        else:
            if question.assessment == "unassessed":
                reason = AWAITING_ASSESSMENT
            elif question.assessment != "sound":
                reason = question.assessment_notes or UNSOUND_QUESTION

    status = "held" if reason else "eligible"
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE tutor_questions SET status = ?, hold_reason = ?, updated_at = ?"
            " WHERE id = ?",
            (status, reason, utc_now(), question_id),
        )
    return get_question(connection, question_id)


# A quote shorter than this is not evidence of anything: "the patient" appears
# in every document ever written.
MIN_QUOTE_CHARS = 24


def quote_in(haystack: str, quote: str) -> bool:
    """Whether *quote* really appears in *haystack*, whitespace-insensitively.

    Used wherever a model's quote must be checked against the exact text it was
    given -- the consented excerpt slice, or the abstract a provider returned.
    An empty or trivially short quote is refused rather than trivially matching:
    ``"" in anything`` is true, and that is how a fabricated citation slips past
    a naive containment check.
    """
    cleaned = normalise_quote(quote)
    if len(cleaned) < MIN_QUOTE_CHARS:
        return False
    return cleaned in normalise_quote(haystack)


def normalise_quote(text: str) -> str:
    """Whitespace-insensitive, case-insensitive comparison text.

    PDF extraction inserts line breaks where a line wrapped, so a quote copied
    from a rendered page rarely matches byte for byte. Nothing else is removed:
    a quote that differs in a word, a number or a negation still fails.
    """
    return " ".join(text.lower().split())


def quote_matches_stored_text(
    connection: sqlite3.Connection, segment_id: str | None, quote: str
) -> bool:
    """Whether *quote* appears in the stored segment it claims to come from."""
    if segment_id is None:
        return False
    row = connection.execute(
        "SELECT text FROM source_segments WHERE id = ?", (segment_id,)
    ).fetchone()
    if row is None:
        return False
    return quote_in(row["text"], quote)


# Columns of literature_records, for the single-record upsert below.
_RECORD_FIELDS = (
    "pmid", "doi", "title", "journal", "abstract", "published_on", "provider_date",
    "publication_types", "retracted", "corrected", "correction_notes", "url", "priority",
)


def upsert_evidence_record(connection: sqlite3.Connection, article: Any) -> str:
    """Store one retrieved record and return its id, deduping on PMID then DOI.

    The build needs a record id for a paper it found while verifying a claim,
    which may belong to no watched topic at all. Deduplication matches the two
    partial unique indexes so an evidence record and a topic result converge on
    one row.
    """
    row = article.as_row() if hasattr(article, "as_row") else dict(article)
    values = {key: row.get(key) for key in _RECORD_FIELDS}
    pmid = values["pmid"] or None
    doi = values["doi"] or None
    if pmid is None and doi is None:
        raise ValueError("a record with neither PMID nor DOI cannot be deduplicated")

    existing = None
    if pmid is not None:
        existing = connection.execute(
            "SELECT id FROM literature_records WHERE pmid = ?", (pmid,)
        ).fetchone()
    if existing is None and doi is not None:
        existing = connection.execute(
            "SELECT id FROM literature_records WHERE doi = ?", (doi,)
        ).fetchone()

    now = utc_now()
    with transaction(connection) as tx:
        if existing is None:
            record_id = new_id("lrc")
            tx.execute(
                "INSERT INTO literature_records (id, pmid, doi, title, journal, abstract,"
                " published_on, provider_date, publication_types, retracted, corrected,"
                " correction_notes, url, priority, first_seen_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record_id, pmid, doi, values["title"] or "", values["journal"] or "",
                    values["abstract"] or "", values["published_on"], values["provider_date"],
                    values["publication_types"] or "[]", 1 if values["retracted"] else 0,
                    1 if values["corrected"] else 0, values["correction_notes"] or "[]",
                    values["url"] or "", values["priority"] or "other", now, now,
                ),
            )
            return record_id
        record_id = existing["id"]
        tx.execute(
            "UPDATE literature_records SET title = ?, journal = ?, abstract = ?,"
            " published_on = ?, provider_date = ?, publication_types = ?,"
            " retracted = MAX(retracted, ?), corrected = MAX(corrected, ?),"
            " correction_notes = ?, url = ?, priority = ?, updated_at = ? WHERE id = ?",
            (
                values["title"] or "", values["journal"] or "", values["abstract"] or "",
                values["published_on"], values["provider_date"],
                values["publication_types"] or "[]", 1 if values["retracted"] else 0,
                1 if values["corrected"] else 0, values["correction_notes"] or "[]",
                values["url"] or "", values["priority"] or "other", now, record_id,
            ),
        )
        return record_id


def open_evidence_basis(
    connection: sqlite3.Connection,
    point_id: str,
    *,
    generation_id: str,
    reason: str = "Superseded by a later verification pass.",
) -> int:
    """Retire the current basis and start a new one. Returns its number.

    A verification pass that finds fresh evidence is establishing a NEW basis for
    the claim, not adding to the old one. Without this, a paper that was
    corrected last month keeps holding the point for ever, even after the claim
    has been re-verified against a clean paper today -- there is no way back, and
    recovery is impossible by construction.

    Nothing is deleted. Superseded links stay as the audit trail of what the
    claim once rested on and why it was withdrawn.
    """
    row = connection.execute(
        "SELECT evidence_basis FROM learning_points WHERE id = ?", (point_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("learning point", point_id)
    basis = int(row["evidence_basis"]) + 1
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE evidence_links SET superseded_at = ?, superseded_reason = ?"
            " WHERE learning_point_id = ? AND superseded_at IS NULL",
            (now, reason, point_id),
        )
        tx.execute(
            "UPDATE learning_points SET evidence_basis = ?, updated_at = ? WHERE id = ?",
            (basis, now, point_id),
        )
    return basis


def apply_evidence(
    connection: sqlite3.Connection,
    *,
    point_id: str,
    record_id: str,
    relation: str,
    quote: str,
    evidence_grade: str,
    basis: int | None = None,
    generation_id: str | None = None,
) -> None:
    """Record a verified link between a claim and a published record.

    Callable only from the verifier, and only after the quote has been found in
    the text the provider returned. There is no route, no schema field and no
    interface control that reaches this.

    Writes into the point's CURRENT basis unless one is named. A conflict on
    (point, record) within the live basis updates it, which is what a re-run over
    the same paper should do.
    """
    if relation not in {"supports", "contradicts", "unclear"}:
        raise ValueError(f"unknown relation {relation!r}")
    if evidence_grade not in {GRADE_ABSTRACT, GRADE_FULL}:
        raise ValueError(f"unknown evidence grade {evidence_grade!r}")
    if basis is None:
        row = connection.execute(
            "SELECT evidence_basis FROM learning_points WHERE id = ?", (point_id,)
        ).fetchone()
        basis = int(row["evidence_basis"]) if row is not None else 1
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO evidence_links"
            " (id, learning_point_id, record_id, relation, quote, evidence_grade,"
            "  verified_at, basis, superseded_at, generation_id, superseded_reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '')"
            " ON CONFLICT (learning_point_id, record_id) WHERE superseded_at IS NULL"
            " DO UPDATE SET relation = excluded.relation, quote = excluded.quote,"
            " evidence_grade = excluded.evidence_grade, verified_at = excluded.verified_at,"
            " basis = excluded.basis, generation_id = excluded.generation_id",
            (
                new_id("evl"),
                point_id,
                record_id,
                relation,
                quote,
                evidence_grade,
                utc_now(),
                basis,
                generation_id,
            ),
        )


def flag_points_for_record(
    connection: sqlite3.Connection, record_id: str, *, reason: str
) -> list[str]:
    """Mark every point linked to *record_id* as needing re-review.

    Used when a watched paper turns out to be corrected or retracted. A
    correction is not a retraction: this marks both for a look, and only
    :func:`settle_point` -- reading `literature_records.retracted` -- turns a
    retraction into a hold that pulls questions out of Tutor.
    """
    rows = connection.execute(
        "SELECT learning_point_id FROM evidence_links"
        " WHERE record_id = ? AND superseded_at IS NULL",
        (record_id,),
    ).fetchall()
    ids = [row["learning_point_id"] for row in rows]
    if not ids:
        return []
    now = utc_now()
    with transaction(connection) as tx:
        for point_id in ids:
            tx.execute(
                "UPDATE learning_points SET review_state = 'needs_re_review', updated_at = ?"
                " WHERE id = ?",
                (now, point_id),
            )
    for point_id in ids:
        settle_point(connection, point_id)
    if reason:
        # The reason is kept on the point that has no other hold reason, so the
        # interface can say why it is asking for a look.
        with transaction(connection) as tx:
            for point_id in ids:
                tx.execute(
                    "UPDATE learning_points SET hold_reason = ? WHERE id = ? AND hold_reason = ''",
                    (reason, point_id),
                )
    return ids


def pile_has_generated_material(connection: sqlite3.Connection, pile_id: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM learning_points WHERE pile_id = ? LIMIT 1", (pile_id,)
    ).fetchone()
    return row is not None


def retire_pile_material(
    connection: sqlite3.Connection,
    pile_id: str,
    *,
    reason: str = "You removed the generated material for this pile.",
) -> dict[str, int]:
    """Retire a pile's generated material. Nothing is deleted.

    Questions are retired, not dropped, and points are held rather than removed.
    Deleting would cascade away the owner's answer history, and an answer whose
    question no longer exists is history that cannot be read. Retired questions
    leave the Tutor cycle immediately (``eligible_question_ids`` excludes
    ``retired_at IS NOT NULL``) and stay available to the history view.
    """
    now = utc_now()
    with transaction(connection) as tx:
        questions = tx.execute(
            "UPDATE tutor_questions SET status = 'retired', retired_at = ?,"
            " retired_reason = ?, updated_at = ? WHERE pile_id = ? AND retired_at IS NULL",
            (now, reason, now, pile_id),
        ).rowcount
        points = tx.execute(
            "UPDATE learning_points SET held = 1, hold_reason = ?, updated_at = ?"
            " WHERE pile_id = ? AND held = 0",
            (reason, now, pile_id),
        ).rowcount
    kept = connection.execute(
        "SELECT COUNT(*) AS n FROM tutor_attempts WHERE question_id IN"
        " (SELECT id FROM tutor_questions WHERE pile_id = ?)",
        (pile_id,),
    ).fetchone()["n"]
    return {
        "points": max(points, 0),
        "questions": max(questions, 0),
        "attempts_kept": kept,
    }


def topic_suggestions(
    connection: sqlite3.Connection, *, limit: int = 12
) -> list[dict[str, Any]]:
    """Public topic phrases worth watching, from topics already generated.

    No model call: these are the tags the synthesis pass already produced, so the
    owner does not have to retype what the build inferred. Creating a watch from
    one is a separate, explicit action, and it does NOT enable weekly polling.
    """
    rows = connection.execute(
        """
        SELECT lpt.topic AS topic, COUNT(DISTINCT lpt.learning_point_id) AS point_count,
               SUM(CASE WHEN p.support = 'evidence_supported' THEN 1 ELSE 0 END) AS supported
          FROM learning_point_topics lpt
          JOIN learning_points p ON p.id = lpt.learning_point_id
         GROUP BY lpt.topic
         ORDER BY point_count DESC, lpt.topic
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    watched = {
        (row["label"] or "").strip().lower()
        for row in connection.execute("SELECT label FROM literature_topics")
    }
    suggestions: list[dict[str, Any]] = []
    for row in rows:
        topic = (row["topic"] or "").strip()
        if not topic:
            continue
        suggestions.append(
            {
                "topic": topic,
                "point_count": row["point_count"],
                "supported_count": row["supported"] or 0,
                "already_watched": topic.lower() in watched,
                # Quoted so a multi-word phrase stays one term. The provider
                # validates the character set again before anything is sent.
                "query": f'"{topic}"',
            }
        )
    return suggestions


def topics_in_use(connection: sqlite3.Connection, limit: int = 40) -> list[str]:
    """Topic tags across the bank, most-used first. Used to suggest watches."""
    rows = connection.execute(
        "SELECT topic, COUNT(*) AS n FROM learning_point_topics"
        " GROUP BY topic ORDER BY n DESC, topic LIMIT ?",
        (limit,),
    ).fetchall()
    return [row["topic"] for row in rows]


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
