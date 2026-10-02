"""The "Build learning material" pipeline: one batch, staged then committed once.

Stages: claim the consented ranges, synthesise, stage drafts, verify against
retrieved literature, assess each question, then write everything in a single
transaction together with the coverage advance.

Two properties the ordering exists for:

* A failure before the commit changes no generated content or source coverage,
  so previous reference text and history are retained and the same characters
  are offered again. A newly retrieved correction/retraction is the safety
  exception: quarantine that paper immediately, even if the later build fails.
* The commit is one transaction (``db.transaction`` nests via SAVEPOINT), so a
  later exception cannot leave half a batch stored with its coverage marked
  processed, nor partially rewrite already-accepted points.

Transmission is bound to consent: only the exact excerpt slices the owner
previewed are ever sent, and every quote is checked against those same slices --
never against the wider stored segment they were cut from.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..appserver.errors import BridgeError
from ..db import connect, transaction
from ..storage import jobs, learning, sources
from . import prompts, schemas

logger = logging.getLogger("vademecum.model")

# Records fetched per point. Small: each costs a verification turn, and a wide
# net produces confident-looking matches to papers that share vocabulary.
RECORDS_PER_POINT = 4

# A relation that can support or refute a claim. Anything else is discarded.
DECISIVE = frozenset({"supports", "contradicts"})


class EvidenceUnavailable(RuntimeError):
    """Required literature retrieval did not complete; no batch may commit."""


@dataclass
class BuildOutcome:
    status: str
    failure_category: str = ""
    points: int = 0
    questions: int = 0
    held: int = 0
    eligible: int = 0
    topics: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class _StagedQuestion:
    draft: learning.DraftQuestion
    # The exact consented slices this question was built from. Only these are
    # ever transmitted again during assessment.
    passages: tuple[str, ...]


@dataclass
class _StagedPoint:
    draft: learning.DraftPoint
    questions: list[_StagedQuestion]
    topics: tuple[str, ...]
    passages: tuple[str, ...]


class BuildRunner:
    """Runs one batch. Opens a connection per stage and closes it."""

    def __init__(
        self,
        *,
        database_path: Path,
        turn_factory: Callable[[], Any],
        provider: Any | None,
        record_limit: int = RECORDS_PER_POINT,
    ) -> None:
        self._database_path = database_path
        self._turn_factory = turn_factory
        self._provider = provider
        self._record_limit = record_limit
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _check_cancelled(self) -> None:
        if self._cancelled:
            raise asyncio.CancelledError()

    def _open(self) -> sqlite3.Connection:
        return connect(self._database_path)

    async def run(self, *, pile_id: str, run_id: str, batch_id: str) -> BuildOutcome:
        try:
            return await self._run(pile_id=pile_id, run_id=run_id, batch_id=batch_id)
        except asyncio.CancelledError:
            self._abandon(run_id, batch_id, status=jobs.CANCELLED, category="cancelled")
            raise
        except sources.ConflictError as exc:
            self._abandon(run_id, batch_id, status=jobs.FAILED, category=exc.kind)
            return BuildOutcome("failed", exc.kind)
        except EvidenceUnavailable:
            self._abandon(run_id, batch_id, status=jobs.FAILED, category="evidence_unavailable")
            return BuildOutcome("failed", "evidence_unavailable")
        except BridgeError as exc:
            self._abandon(run_id, batch_id, status=jobs.FAILED, category=exc.category)
            return BuildOutcome("failed", exc.category)
        except Exception:
            logger.exception("build_failed run=%s", run_id)
            self._abandon(run_id, batch_id, status=jobs.FAILED, category="internal")
            return BuildOutcome("failed", "internal")

    def _abandon(self, run_id: str, batch_id: str, *, status: str, category: str) -> None:
        """Finish without new learning content; coverage stays untouched."""
        connection = self._open()
        try:
            with transaction(connection):
                sources.close_batch(
                    connection,
                    batch_id,
                    status="cancelled" if category == "cancelled" else "failed",
                )
                jobs.finish_run(
                    connection, run_id, status=status, failure_category=category
                )
        finally:
            connection.close()

    # --- the pipeline -------------------------------------------------------

    async def _run(self, *, pile_id: str, run_id: str, batch_id: str) -> BuildOutcome:
        connection = self._open()
        try:
            jobs.set_stage(connection, run_id, "reading your material")
            excerpts = sources.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=sources.batch_hash(connection, batch_id),
            )
            sources.attach_run(connection, batch_id, run_id)
        finally:
            connection.close()

        if not excerpts:
            self._abandon(run_id, batch_id, status=jobs.FAILED, category="no_sources")
            return BuildOutcome("failed", "no_sources")

        self._check_cancelled()
        handles = prompts.excerpt_handles(list(excerpts))

        self._stage_note(run_id, "building learning points")
        runner = self._turn_factory()
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.SYNTHESIS_DEVELOPER,
            prompt=prompts.synthesis_prompt(handles),
            output_schema=schemas.SYNTHESIS_SCHEMA,
        )
        self._check_cancelled()

        staged, search_topics = _stage(reply.payload, handles)
        if not staged:
            # The turn succeeded but nothing survived the quote check: a failure
            # with a name, not an empty success. Coverage does not advance, so
            # the same characters are offered again.
            self._abandon(run_id, batch_id, status=jobs.FAILED, category="invalid_output")
            return BuildOutcome("failed", "invalid_output")

        self._stage_note(run_id, "checking published literature")
        evidence = await self._gather_evidence(staged, search_topics)
        self._check_cancelled()

        self._stage_note(run_id, "assessing questions")
        assessments = await self._assess(staged, evidence)
        self._check_cancelled()

        return self._commit(
            pile_id=pile_id,
            run_id=run_id,
            batch_id=batch_id,
            staged=staged,
            evidence=evidence,
            assessments=assessments,
            search_topics=search_topics,
        )

    def _stage_note(self, run_id: str, stage: str) -> None:
        connection = self._open()
        try:
            jobs.set_stage(connection, run_id, stage)
        finally:
            connection.close()

    # --- verification -------------------------------------------------------

    async def _gather_evidence(
        self, staged: list[_StagedPoint], search_topics: tuple[str, ...]
    ) -> dict[int, list[dict[str, Any]]]:
        """Retrieve literature per point and check each record against the claim.

        An empty result is the normal outcome and leaves the point
        `source_supported` -- visible draft material, never asked. Missing
        evidence is never upgraded to support.
        """
        if self._provider is None:
            raise EvidenceUnavailable()

        found: dict[int, list[dict[str, Any]]] = {}
        for index, point in enumerate(staged):
            self._check_cancelled()
            query = _query_for(point, search_topics)
            if not query:
                continue
            try:
                articles = list(self._provider.search(query))
            except Exception:
                # An outage is not an empty search result. Abort before commit,
                # preserving accepted references, their evidence and coverage.
                # Do not retain provider exception text: it can contain queries.
                raise EvidenceUnavailable() from None

            # Inspect every result already returned before any model await.
            # Otherwise a timeout while verifying the first clean paper could
            # hide a correction on an existing supporting paper later in this
            # same response. The model-verification budget does not limit which
            # already-retrieved safety notices we honor.
            for article in articles:
                if any(_record_identifiers(article)) and any(
                    getattr(article, flag, False)
                    for flag in ("is_notice", "retracted", "corrected")
                ):
                    self._record_disqualification(article)

            verified: list[dict[str, Any]] = []
            for article in articles[: self._record_limit]:
                self._check_cancelled()
                if not any(_record_identifiers(article)):
                    continue  # no stable identifier: cannot establish a basis
                if any(
                    getattr(article, flag, False)
                    for flag in ("is_notice", "retracted", "corrected")
                ):
                    # Corrected papers need their own review; merely retrieving
                    # the old abstract must not reactivate a disqualified basis.
                    continue
                if self._known_disqualified(article):
                    continue
                body = _record_body(article)
                if not body:
                    # No substantive abstract was retrieved, so there is nothing
                    # to verify against. The point stays draft rather than being
                    # marked evidence-supported on the strength of a title.
                    continue
                record_text = _record_context(article)
                runner = self._turn_factory()
                # A failure here is a failure of the RUN. Swallowing it lets a
                # rerun whose verification timed out come back "succeeded" with
                # an empty bank, silently demoting material that was already
                # accepted. Nothing is written until every stage has answered.
                reply = await runner.run(
                    instructions=prompts.BASE_INSTRUCTIONS,
                    developer_instructions=prompts.EVIDENCE_DEVELOPER,
                    prompt=prompts.evidence_prompt(
                        claim=point.draft.claim,
                        detail=point.draft.detail,
                        record_text=record_text,
                    ),
                    output_schema=schemas.EVIDENCE_SCHEMA,
                )
                relation = reply.payload.get("relation")
                quote = (reply.payload.get("quote") or "").strip()
                if relation not in DECISIVE:
                    continue
                # A substantive quote must be locatable in the text the provider
                # actually returned. `learning.quote_in` refuses an empty or
                # trivially short quote, which is what stops `"" in abstract`
                # from making every record supporting evidence.
                # Grounded in the retrieved BODY, not in the title we supplied
                # alongside it for context.
                if not learning.quote_in(body, quote):
                    continue
                verified.append(
                    {
                        "article": article,
                        "relation": relation,
                        "quote": quote,
                        # We retrieved a title and an abstract, so that is what
                        # we may claim to have read -- whatever the publication
                        # type says the underlying document is.
                        "evidence_grade": learning.GRADE_ABSTRACT,
                        "text": body,
                    }
                )
            if verified:
                found[index] = verified
        return found

    def _known_disqualified(self, article: Any) -> bool:
        """A stale search result cannot undo a previously recorded safety flag."""
        connection = self._open()
        try:
            return connection.execute(
                "SELECT 1 FROM literature_records WHERE (pmid = ? OR doi = ?)"
                " AND (retracted = 1 OR corrected = 1 OR is_notice = 1) LIMIT 1",
                _record_identifiers(article),
            ).fetchone() is not None
        finally:
            connection.close()

    def _record_disqualification(self, article: Any) -> None:
        """Quarantine an already-cited paper immediately; never change its support.

        This is a safety update from the literature provider, not synthesized
        learning material. Reference text, evidence history and source coverage
        stay intact even if the rest of the build cannot finish.
        """
        connection = self._open()
        try:
            with transaction(connection):
                rows = connection.execute(
                    "SELECT id FROM literature_records WHERE pmid = ? OR doi = ?",
                    _record_identifiers(article),
                ).fetchall()
                for row in rows:
                    connection.execute(
                        "UPDATE literature_records SET retracted = MAX(retracted, ?),"
                        " corrected = MAX(corrected, ?), is_notice = MAX(is_notice, ?)"
                        " WHERE id = ?",
                        (int(bool(article.retracted)), int(bool(article.corrected)),
                         int(bool(article.is_notice)), row["id"]),
                    )
                    learning.flag_points_for_record(
                        connection, row["id"],
                        reason="The literature search identified a safety notice. Recheck this evidence.",
                    )
        finally:
            connection.close()

    async def _assess(
        self,
        staged: list[_StagedPoint],
        evidence: dict[int, list[dict[str, Any]]],
    ) -> dict[tuple[int, int], dict[str, Any]]:
        """Assess each question against BOTH its source slices and its evidence.

        Assessing against the source alone lets a question whose claim is
        externally supported carry additional, unsupported clinical content in
        its reference answer. The assessment therefore sees the verified record
        text as well, and a question with no supporting evidence is not assessed
        at all -- it cannot become eligible, so a turn spent on it buys nothing.
        """
        results: dict[tuple[int, int], dict[str, Any]] = {}
        for point_index, point in enumerate(staged):
            supporting = [
                entry
                for entry in evidence.get(point_index, [])
                if entry["relation"] == "supports"
            ]
            if not supporting:
                continue
            evidence_texts = [entry["text"] for entry in supporting]
            for question_index, question in enumerate(point.questions):
                self._check_cancelled()
                if not question.passages:
                    continue
                runner = self._turn_factory()
                # Also propagated. An assessment that never ran is not the same
                # as one that returned "unsound", and treating it as merely
                # "held" would let a failed rerun overwrite an accepted question
                # with a held one.
                reply = await runner.run(
                    instructions=prompts.BASE_INSTRUCTIONS,
                    developer_instructions=prompts.ASSESSMENT_DEVELOPER,
                    prompt=prompts.assessment_prompt(
                        question=question.draft.prompt,
                        reference_answer=question.draft.reference_answer,
                        rubric=question.draft.rubric,
                        passages=list(question.passages),
                        evidence=evidence_texts,
                    ),
                    output_schema=schemas.ASSESSMENT_SCHEMA,
                )
                results[(point_index, question_index)] = reply.payload
        return results

    # --- commit -------------------------------------------------------------

    def _commit(
        self,
        *,
        pile_id: str,
        run_id: str,
        batch_id: str,
        staged: list[_StagedPoint],
        evidence: dict[int, list[dict[str, Any]]],
        assessments: dict[tuple[int, int], dict[str, Any]],
        search_topics: tuple[str, ...],
    ) -> BuildOutcome:
        """Write everything that survived, and the coverage, in one transaction."""
        connection = self._open()
        try:
            written_points = 0
            written_questions = 0
            held = 0
            eligible = 0

            # One outer transaction. Every storage helper below opens its own,
            # which nests as a SAVEPOINT, so an exception anywhere rolls the
            # whole batch back and leaves prior accepted material untouched.
            with transaction(connection):
                jobs.set_stage(connection, run_id, "saving")

                for index, point in enumerate(staged):
                    point_id, _ = learning.upsert_point(
                        connection,
                        pile_id=pile_id,
                        generation_id=run_id,
                        draft=point.draft,
                    )
                    written_points += 1

                    found = evidence.get(index, [])
                    # Whether this run replaced the point's evidence basis. It
                    # decides whether a question's earlier assessment still
                    # applies: an assessment judged a question against a
                    # specific basis, so a NEW basis invalidates it, and no new
                    # evidence leaves it standing.
                    rebased = bool(found)
                    if found:
                        # A verification pass that found evidence establishes a
                        # NEW basis for the claim. The old links are retained as
                        # audit history but stop deciding support, which is what
                        # makes recovery possible: a claim whose paper was
                        # corrected can be re-verified against a clean one and
                        # become askable again.
                        basis = learning.open_evidence_basis(
                            connection,
                            point_id,
                            generation_id=run_id,
                            reason=(
                                "Replaced by a later verification pass on "
                                f"{len(found)} record(s)."
                            ),
                        )
                        for entry in found:
                            try:
                                record_id = learning.upsert_evidence_record(
                                    connection, entry["article"]
                                )
                            except ValueError:
                                continue  # neither PMID nor DOI: not citable
                            status = connection.execute(
                                "SELECT retracted, corrected, is_notice FROM literature_records"
                                " WHERE id = ?", (record_id,)
                            ).fetchone()
                            if any(status[field] for field in ("retracted", "corrected", "is_notice")):
                                raise sources.ConflictError(
                                    "evidence_changed",
                                    "The evidence changed during this build. Please recheck it.",
                                )
                            learning.apply_evidence(
                                connection,
                                point_id=point_id,
                                record_id=record_id,
                                relation=entry["relation"],
                                quote=entry["quote"],
                                evidence_grade=entry["evidence_grade"],
                                basis=basis,
                                generation_id=run_id,
                            )

                    learning.settle_point(
                        connection, point_id, model_uncertain=point.draft.model_uncertain
                    )

                    for question_index, question in enumerate(point.questions):
                        question_id, _ = learning.upsert_question(
                            connection,
                            pile_id=pile_id,
                            point_id=point_id,
                            generation_id=run_id,
                            draft=question.draft,
                        )
                        verdict = assessments.get((index, question_index))
                        if verdict is not None:
                            learning.record_assessment(
                                connection,
                                question_id,
                                assessment=verdict.get("verdict", "unassessed"),
                                notes=verdict.get("notes", ""),
                            )
                        elif rebased:
                            # New basis, no verdict for this question: the old
                            # assessment was about evidence that is no longer
                            # current, so it cannot stand.
                            learning.record_assessment(
                                connection,
                                question_id,
                                assessment="unassessed",
                                notes=(
                                    "The evidence behind this claim was replaced and "
                                    "the question has not been assessed against it yet."
                                ),
                            )
                        # Otherwise the stored assessment is left exactly as it
                        # was: a rerun that found nothing new must not demote a
                        # question that was already sound.
                        released = learning.release_question(connection, question_id)
                        written_questions += 1
                        if released.status == "eligible":
                            eligible += 1
                        else:
                            held += 1

                # Coverage advances here and nowhere else, inside the same
                # transaction as the material it produced.
                sources.commit_batch(connection, batch_id)
                jobs.finish_run(
                    connection,
                    run_id,
                    status=jobs.SUCCEEDED,
                    point_count=written_points,
                    question_count=written_questions,
                    held_count=held,
                )

            return BuildOutcome(
                "succeeded",
                points=written_points,
                questions=written_questions,
                held=held,
                eligible=eligible,
                topics=search_topics,
            )
        finally:
            connection.close()


# --- staging (pure) ----------------------------------------------------------


def _stage(
    payload: dict[str, Any], handles: dict[str, sources.Excerpt]
) -> tuple[list[_StagedPoint], tuple[str, ...]]:
    """Map a validated reply onto drafts, dropping unverifiable citations.

    Pure: it needs no database, because every quote is checked against the
    excerpt slice that was actually sent rather than against the wider stored
    segment. Checking against the segment would accept a quote from text beyond
    the consented range -- text the model was never given and could only have
    invented, or that the owner never agreed to send.

    Two independent checks per citation: the handle must be one we issued, and
    the quote must appear in that excerpt's exact text.
    """
    staged: list[_StagedPoint] = []
    for raw in payload.get("points", []):
        citations, passages = _citations(raw.get("citations", []), handles)
        if not citations:
            continue
        topics = tuple(
            topic.strip()[: schemas.MAX_TOPIC]
            for topic in raw.get("topics", [])
            if topic and topic.strip()
        )
        point = learning.DraftPoint(
            claim=raw["claim"].strip(),
            detail=raw.get("detail", "").strip(),
            topics=topics,
            citations=citations,
            model_uncertain=bool(raw.get("unclear")),
        )
        questions: list[_StagedQuestion] = []
        for raw_question in raw.get("questions", []):
            anchors, anchor_passages = _citations(
                raw_question.get("citations", []), handles
            )
            if not anchors or not raw_question.get("reference_answer", "").strip():
                continue
            questions.append(
                _StagedQuestion(
                    draft=learning.DraftQuestion(
                        prompt=raw_question["prompt"].strip(),
                        reference_answer=raw_question["reference_answer"].strip(),
                        rubric=raw_question.get("rubric", "").strip(),
                        anchors=anchors,
                    ),
                    passages=anchor_passages,
                )
            )
        staged.append(
            _StagedPoint(
                draft=point, questions=questions, topics=topics, passages=passages
            )
        )

    search_topics = tuple(
        topic.strip()[: schemas.MAX_TOPIC]
        for topic in payload.get("search_topics", [])
        if topic and topic.strip()
    )
    return staged, search_topics


def _citations(
    raw: list[dict[str, Any]], handles: dict[str, sources.Excerpt]
) -> tuple[tuple[learning.DraftCitation, ...], tuple[str, ...]]:
    """Surviving citations, plus the exact excerpt slices they came from."""
    kept: list[learning.DraftCitation] = []
    passages: list[str] = []
    for entry in raw:
        excerpt = handles.get(entry.get("excerpt_id", ""))
        if excerpt is None:
            continue  # a handle we never issued
        quote = (entry.get("quote") or "").strip()
        if not learning.quote_in(excerpt.text, quote):
            continue  # not in the text we actually sent
        kept.append(
            learning.DraftCitation(
                source_id=excerpt.source_id,
                segment_id=excerpt.segment_id,
                locator=excerpt.range_label,
                quote=quote,
            )
        )
        if excerpt.text not in passages:
            passages.append(excerpt.text)
    return tuple(kept), tuple(passages)


def _query_for(point: _StagedPoint, search_topics: tuple[str, ...]) -> str:
    """A short public topic string. Never a passage, never the claim verbatim."""
    words = list(point.topics) or list(search_topics)
    return " AND ".join(f'"{topic}"' for topic in words[:3])[:200]


# An abstract shorter than this is a stub ("No abstract available.", a copyright
# line) rather than a body a claim can be checked against.
MIN_ABSTRACT_CHARS = 120


def _record_identifiers(article: Any) -> tuple[str | None, str | None]:
    """Absent identifiers must never match other records' empty identifiers."""
    return tuple(
        (getattr(article, name, None) or "").strip() or None
        for name in ("pmid", "doi")
    )


def _record_body(article: Any) -> str:
    """The retrieved BODY a quote must come from, or "" if there is none.

    The title is deliberately excluded. A title is a label, not evidence: a
    claim "supported" by a quote from a title has been matched against a
    headline the provider indexed, not against anything the paper says. When a
    record has no substantive abstract we have retrieved nothing to verify
    against, and the honest result is no evidence at all -- not a weaker grade.
    """
    abstract = (getattr(article, "abstract", "") or "").strip()
    return abstract if len(abstract) >= MIN_ABSTRACT_CHARS else ""


def _record_context(article: Any) -> str:
    """What the verifier is shown: the title for context, the body for quoting.

    Only ever called when :func:`_record_body` is non-empty, so the title can
    never be the sole thing a quote could match.
    """
    title = (getattr(article, "title", "") or "").strip()
    return f"{title}\n\n{_record_body(article)}".strip()
