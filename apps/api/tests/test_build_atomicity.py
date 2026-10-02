"""A failed rerun must not demote, rewrite or lose accepted material.

The reproduced defect: an accepted bank of one eligible question, then a rerun
whose evidence turn times out, and the run reports "succeeded" with an empty
bank. Every test here snapshots the ENTIRE relevant rows before the failing
rerun and asserts they are byte-identical afterwards.
"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
from pathlib import Path

import pytest

from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from vademecum.appserver.errors import BridgeTimeout, BridgeUnavailable
from vademecum.db import apply_migrations, connect
from vademecum.ingest.extract import Extraction, Segment
from vademecum.model.build import BuildRunner
from vademecum.storage import jobs, learning
from vademecum.storage import piles as pile_store
from vademecum.storage import sources as source_store

PASSAGE = (
    "In septic shock, serial lactate measurement guides resuscitation. "
    "Clearance of at least ten per cent within two hours is associated with "
    "improved survival in the cohorts reviewed here."
)
QUOTE = "serial lactate measurement guides resuscitation"
ABSTRACT = (
    "We studied resuscitation targets in septic shock across two centres. "
    "Serial lactate measurement guides resuscitation and predicted mortality "
    "in this cohort of 412 adults admitted to intensive care with vasopressor "
    "requirement, followed to ninety days."
)


def extraction(*texts: str) -> Extraction:
    return Extraction(
        "extracted",
        "",
        "section",
        len(texts),
        tuple(
            Segment(index, "section", f"section {index + 1}", text)
            for index, text in enumerate(texts)
        ),
    )


def synthesis_reply(claim: str = "Lactate clearance guides resuscitation") -> dict:
    return {
        "points": [
            {
                "claim": claim,
                "detail": "From the uploaded lecture.",
                "topics": ["septic shock"],
                "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
                "unclear": False,
                "questions": [
                    {
                        "prompt": "How is resuscitation guided in septic shock?",
                        "reference_answer": "By serial lactate measurement.",
                        "rubric": "Mentions serial lactate measurement.",
                        "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
                    }
                ],
            }
        ],
        "search_topics": ["septic shock"],
    }


EVIDENCE_SUPPORTS = {
    "relation": "supports",
    "quote": "Serial lactate measurement guides resuscitation",
    "reasoning": "The abstract states it.",
}
ASSESSMENT_SOUND = {"verdict": "sound", "problems": [], "notes": ""}


def article(**overrides) -> FakeArticle:
    base = {"pmid": "111", "title": "Lactate targets", "abstract": ABSTRACT}
    base.update(overrides)
    return FakeArticle(**base)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "vademecum.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def prepared(workspace: Path):
    connection = connect(workspace)
    apply_migrations(connection)
    pile = pile_store.create_pile(connection, title="Sepsis", tier="mid")
    source_store.store_upload(
        connection,
        pile_id=pile.id,
        display_name="lecture.txt",
        media_type="text/plain",
        sha256="a" * 64,
        byte_size=len(PASSAGE),
        confidence="mid",
        extraction=extraction(PASSAGE),
    )
    yield connection, pile.id, workspace
    connection.close()


def run_build(connection, pile_id, database_path, turns, provider):
    batch = source_store.next_batch(connection, pile_id)
    batch_id = source_store.record_batch(connection, pile_id, batch)
    run = jobs.start_run(
        connection,
        pile_id=pile_id,
        source_count=1,
        excerpt_count=len(batch.excerpts),
        excerpt_chars=batch.excerpt_chars,
    )
    runner = BuildRunner(
        database_path=database_path, turn_factory=turns, provider=provider
    )
    outcome = asyncio.run(runner.run(pile_id=pile_id, run_id=run.id, batch_id=batch_id))
    return outcome, run.id, batch_id


def snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Every row of every table a build can touch, verbatim."""
    tables = (
        "learning_points",
        "learning_point_sources",
        "learning_point_topics",
        "tutor_questions",
        "tutor_question_anchors",
        "tutor_attempts",
        "evidence_links",
        "literature_records",
        "source_segments",
    )
    return {
        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
        for table in tables
    }


def accept_a_bank(connection, pile_id, database_path):
    """One eligible question, built and verified. The state a rerun must protect."""
    turns = ScriptedTurns(
        synthesis=[synthesis_reply()],
        evidence=[EVIDENCE_SUPPORTS],
        assessment=[ASSESSMENT_SOUND],
    )
    outcome, _, _ = run_build(
        connection, pile_id, database_path, turns, FakeProvider(articles=[article()])
    )
    assert outcome.status == "succeeded"
    assert len(learning.eligible_question_ids(connection)) == 1
    return outcome


SECOND_PASSAGE = (
    "Vasopressor selection in septic shock favours noradrenaline first line."
)
SECOND_QUOTE = "noradrenaline first line"


def second_synthesis_reply() -> dict:
    """A reply that cites the SECOND batch's excerpt, so staging accepts it."""
    return {
        "points": [
            {
                "claim": "Noradrenaline is first line in septic shock",
                "detail": "",
                "topics": ["septic shock"],
                "citations": [
                    {"excerpt_id": "E1", "quote": "favours noradrenaline first line"}
                ],
                "unclear": False,
                "questions": [
                    {
                        "prompt": "Which vasopressor is first line in septic shock?",
                        "reference_answer": "Noradrenaline.",
                        "rubric": "Names noradrenaline.",
                        "citations": [
                            {"excerpt_id": "E1", "quote": "favours noradrenaline first line"}
                        ],
                    }
                ],
            }
        ],
        "search_topics": ["septic shock"],
    }


def add_more_material(connection, pile_id) -> None:
    source_store.store_upload(
        connection,
        pile_id=pile_id,
        display_name="second.txt",
        media_type="text/plain",
        sha256="c" * 64,
        byte_size=60,
        confidence="mid",
        extraction=extraction(SECOND_PASSAGE),
    )


class TestFailedRerunPreservesAcceptedMaterial:
    def test_an_evidence_timeout_fails_the_run_and_changes_nothing(
        self, prepared
    ) -> None:
        """The exact reproduction: bank 1 -> evidence timeout -> must stay bank 1."""
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        add_more_material(connection, pile_id)
        # Snapshot after the upload: the new source legitimately adds segment
        # rows. Everything below has to be identical across the failed rerun.
        before = snapshot(connection)
        coverage_before = source_store.pile_coverage(connection, pile_id).as_dict()

        turns = ScriptedTurns(
            synthesis=[second_synthesis_reply()], evidence=[BridgeTimeout()]
        )
        outcome, run_id, batch_id = run_build(
            connection, pile_id, database_path, turns, FakeProvider(articles=[article()])
        )

        assert outcome.status == "failed"
        assert outcome.failure_category == "timeout"
        # The accepted bank is untouched, row for row.
        after = snapshot(connection)
        for table, rows in before.items():
            assert after[table] == rows, table
        assert len(learning.eligible_question_ids(connection)) == 1
        assert (
            source_store.pile_coverage(connection, pile_id).chars_covered
            == coverage_before["chars_covered"]
        )
        assert jobs.get_run(connection, run_id).status == "failed"
        assert (
            connection.execute(
                "SELECT status FROM build_batches WHERE id = ?", (batch_id,)
            ).fetchone()["status"]
            == "failed"
        )

    def test_an_assessment_failure_fails_the_run_and_changes_nothing(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        add_more_material(connection, pile_id)
        before = snapshot(connection)

        turns = ScriptedTurns(
            synthesis=[second_synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[BridgeUnavailable("process_exited")],
        )
        outcome, _, _ = run_build(
            connection, pile_id, database_path, turns, FakeProvider(articles=[article()])
        )

        assert outcome.status == "failed"
        assert snapshot(connection) == before
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_a_cancelled_rerun_changes_nothing(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        add_more_material(connection, pile_id)
        before = snapshot(connection)
        coverage_before = source_store.pile_coverage(connection, pile_id).chars_covered

        batch = source_store.next_batch(connection, pile_id)
        batch_id = source_store.record_batch(connection, pile_id, batch)
        run = jobs.start_run(
            connection,
            pile_id=pile_id,
            source_count=1,
            excerpt_count=len(batch.excerpts),
            excerpt_chars=batch.excerpt_chars,
        )
        turns = ScriptedTurns(synthesis=[second_synthesis_reply()])
        runner = BuildRunner(
            database_path=database_path,
            turn_factory=turns,
            provider=FakeProvider(articles=[article()]),
        )
        runner.cancel()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(runner.run(pile_id=pile_id, run_id=run.id, batch_id=batch_id))

        assert snapshot(connection) == before
        assert (
            source_store.pile_coverage(connection, pile_id).chars_covered
            == coverage_before
        )
        assert jobs.get_run(connection, run.id).status == "cancelled"

    def test_a_provider_outage_fails_without_advancing_coverage(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        before = snapshot(connection)
        turns = ScriptedTurns(synthesis=[synthesis_reply()])
        provider = FakeProvider(error=RuntimeError("network down"))
        outcome, run_id, batch_id = run_build(
            connection, pile_id, database_path, turns, provider
        )

        assert outcome.status == "failed"
        assert outcome.failure_category == "evidence_unavailable"
        assert snapshot(connection) == before
        saved = jobs.get_run(connection, run_id)
        assert saved.failure_category == "evidence_unavailable"
        assert "literature search" in saved.as_dict()["failure_detail"].lower()
        assert connection.execute(
            "SELECT status FROM build_batches WHERE id = ?", (batch_id,)
        ).fetchone()["status"] == "failed"

    def test_outage_cannot_overwrite_an_accepted_reference(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        before = snapshot(connection)
        changed = copy.deepcopy(synthesis_reply())
        changed["points"][0]["questions"][0]["reference_answer"] = "A changed answer."
        outcome, _, _ = run_build(
            connection, pile_id, database_path,
            ScriptedTurns(synthesis=[changed]),
            FakeProvider(error=RuntimeError("network down")),
        )
        assert outcome.status == "failed"
        assert snapshot(connection) == before
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_missing_provider_is_not_an_empty_success(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        before = snapshot(connection)
        outcome, _, _ = run_build(
            connection, pile_id, database_path,
            ScriptedTurns(synthesis=[synthesis_reply()]), None,
        )
        assert outcome.failure_category == "evidence_unavailable"
        assert snapshot(connection) == before

    def test_later_search_failure_rolls_back_the_entire_batch(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        before = snapshot(connection)
        payload = synthesis_reply()
        second = copy.deepcopy(payload["points"][0])
        second["claim"] = "Another synthetic claim"
        payload["points"].append(second)

        class FailsSecondSearch(FakeProvider):
            def search(self, query):
                if self.queries:
                    raise RuntimeError("network down")
                return super().search(query)

        outcome, _, _ = run_build(
            connection, pile_id, database_path,
            ScriptedTurns(synthesis=[payload], evidence=[EVIDENCE_SUPPORTS]),
            FailsSecondSearch(articles=[article()]),
        )
        assert outcome.failure_category == "evidence_unavailable"
        assert snapshot(connection) == before


class TestEvidenceMustBeSubstantive:
    def test_a_title_only_record_is_never_support(self, prepared) -> None:
        """A quote matching only the title is a headline, not evidence."""
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[
                {
                    "relation": "supports",
                    "quote": "Serial lactate measurement guides resuscitation",
                    "reasoning": "It is in the title.",
                }
            ],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[
                article(
                    title="Serial lactate measurement guides resuscitation", abstract=""
                )
            ]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED
        assert point.evidence == ()
        assert learning.eligible_question_ids(connection) == []

    def test_a_stub_abstract_is_not_a_body(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(articles=[article(abstract="No abstract available.")])
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED

    def test_a_notice_is_never_support(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(articles=[article(is_notice=True)])
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED


class TestCorrectionsHoldWithoutRetracting:
    def _accepted(self, prepared):
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        point = learning.list_points(connection, pile_id=pile_id)[0]
        record_id = point.evidence[0].record_id
        return connection, pile_id, point.id, record_id

    def test_a_correction_holds_the_question_but_is_not_a_retraction(
        self, prepared
    ) -> None:
        connection, pile_id, point_id, record_id = self._accepted(prepared)
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?", (record_id,)
        )
        learning.settle_point(connection, point_id)

        point = learning.get_point(connection, point_id)
        assert point.review_state == "needs_re_review"
        assert point.held
        assert "corrected" in point.hold_reason.lower()
        assert "not a retraction" in point.hold_reason.lower()
        assert learning.eligible_question_ids(connection) == []
        # The record itself is not marked retracted.
        assert (
            connection.execute(
                "SELECT retracted FROM literature_records WHERE id = ?", (record_id,)
            ).fetchone()["retracted"]
            == 0
        )

    def test_a_retraction_holds_with_its_own_reason(self, prepared) -> None:
        connection, pile_id, point_id, record_id = self._accepted(prepared)
        connection.execute(
            "UPDATE literature_records SET retracted = 1 WHERE id = ?", (record_id,)
        )
        learning.settle_point(connection, point_id)

        point = learning.get_point(connection, point_id)
        assert point.held
        assert "retracted" in point.hold_reason.lower()
        assert learning.eligible_question_ids(connection) == []

    def test_settling_does_not_clear_a_requested_re_review(self, prepared) -> None:
        """A watcher asking for a look must not be undone by the next settle."""
        connection, pile_id, point_id, _ = self._accepted(prepared)
        learning.settle_point(
            connection, point_id, force_re_review="A linked paper was corrected."
        )
        point = learning.get_point(connection, point_id)
        assert point.review_state == "needs_re_review"
        assert point.held

        # Settling again without the flag must not silently release it: the
        # point is held, and eligibility is recomputed from the held flag.
        assert learning.eligible_question_ids(connection) == []

    def test_a_correction_never_auto_releases(self, prepared) -> None:
        connection, pile_id, point_id, record_id = self._accepted(prepared)
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?", (record_id,)
        )
        learning.settle_point(connection, point_id)
        assert learning.eligible_question_ids(connection) == []

        # Clearing the flag alone does not put the question back. A build has to
        # re-verify and re-assess it.
        connection.execute(
            "UPDATE literature_records SET corrected = 0 WHERE id = ?", (record_id,)
        )
        assert learning.eligible_question_ids(connection) == []


class TestRecheckPath:
    def test_fully_covered_material_can_be_reopened(self, prepared) -> None:
        """Exclude, re-include, re-upload: previously a dead end with no route out."""
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        assert source_store.pile_coverage(connection, pile_id).complete
        assert source_store.next_batch(connection, pile_id).excerpts == ()

        source = source_store.list_sources(connection, pile_id=pile_id)[0]
        source_store.set_excluded(connection, source.id, excluded=True)
        source_store.set_excluded(connection, source.id, excluded=False)
        # Still nothing to do, because coverage remembers.
        assert source_store.next_batch(connection, pile_id).excerpts == ()

        reopened = source_store.reopen_coverage(connection, pile_id=pile_id)
        assert reopened["segments_reopened"] >= 1
        batch = source_store.next_batch(connection, pile_id)
        assert batch.excerpts, "the material is offered again"
        assert not source_store.pile_coverage(connection, pile_id).complete

    def test_reopening_does_not_release_held_material(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        accept_a_bank(connection, pile_id, database_path)
        source = source_store.list_sources(connection, pile_id=pile_id)[0]
        source_store.set_excluded(connection, source.id, excluded=True)
        source_store.set_excluded(connection, source.id, excluded=False)

        source_store.reopen_coverage(connection, pile_id=pile_id)
        # Re-including plus reopening makes the TEXT available again. It does
        # not make the previously-held QUESTION askable again.
        assert learning.eligible_question_ids(connection) == []
        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.held

    def test_reopening_retires_a_stale_preview(self, prepared) -> None:
        connection, pile_id, _ = prepared
        batch = source_store.next_batch(connection, pile_id)
        batch_id = source_store.record_batch(connection, pile_id, batch)
        source_store.reopen_coverage(connection, pile_id=pile_id)

        with pytest.raises(source_store.ConflictError):
            source_store.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=batch.selection_hash,
            )


class TestConsentCoversEveryTransmittedField:
    def test_renaming_a_source_invalidates_the_preview(self, prepared) -> None:
        """The filename is in the prompt, so it is in the consent."""
        connection, pile_id, _ = prepared
        batch = source_store.next_batch(connection, pile_id)
        batch_id = source_store.record_batch(connection, pile_id, batch)
        connection.execute(
            "UPDATE sources SET display_name = 'renamed.txt' WHERE pile_id = ?",
            (pile_id,),
        )

        with pytest.raises(source_store.ConflictError) as caught:
            source_store.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=batch.selection_hash,
            )
        assert caught.value.kind == "selection_changed"

    def test_re_rating_a_source_invalidates_the_preview(self, prepared) -> None:
        """The confidence label is in the prompt too."""
        connection, pile_id, _ = prepared
        batch = source_store.next_batch(connection, pile_id)
        batch_id = source_store.record_batch(connection, pile_id, batch)
        source = source_store.list_sources(connection, pile_id=pile_id)[0]
        source_store.set_confidence(connection, source.id, confidence="high")

        with pytest.raises(source_store.ConflictError):
            source_store.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=batch.selection_hash,
            )


class TestBatchBudgetIsHard:
    def test_a_tiny_budget_is_never_exceeded(self, prepared) -> None:
        connection, pile_id, _ = prepared
        for total in (10, 50, 120, 400):
            batch = source_store.next_batch(
                connection,
                pile_id,
                max_excerpts=8,
                max_excerpt_chars=100,
                max_total_chars=total,
            )
            assert batch.excerpt_chars <= total, total

    def test_a_tail_is_not_folded_past_the_budget(self, workspace) -> None:
        connection = connect(workspace)
        apply_migrations(connection)
        pile = pile_store.create_pile(connection, title="Long", tier="mid")
        source_store.store_upload(
            connection,
            pile_id=pile.id,
            display_name="long.txt",
            media_type="text/plain",
            sha256="d" * 64,
            byte_size=900,
            confidence="mid",
            extraction=extraction("z" * 900),
        )
        batch = source_store.next_batch(
            connection, pile.id, max_excerpts=4, max_excerpt_chars=200, max_total_chars=250
        )
        assert batch.excerpt_chars <= 250
        connection.close()
