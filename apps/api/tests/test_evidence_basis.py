"""Recovery: a corrected claim, re-verified against clean evidence, becomes askable.

The reproduced defect: accepted claim -> its paper is corrected -> the point is
held (correct) -> the owner reopens coverage and reruns -> the SAME claim is
re-verified against a NEW clean PMID -> the run succeeds and the point is STILL
held, because `settle_point` and the eligibility query read every evidence link
ever written, including the corrected one. There was no way back.

The fix separates a point's *active* evidence basis from its retained audit
history. These tests cover both directions: successful recovery must work, and a
failed rerun must still preserve the prior basis.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from vademecum.appserver.errors import BridgeTimeout
from vademecum.db import apply_migrations, connect
from vademecum.ingest.extract import Extraction, Segment
from vademecum.model.build import BuildRunner
from vademecum.literature.pubmed import Article
from vademecum.storage import jobs, learning
from vademecum.storage import piles as pile_store
from vademecum.storage import sources as source_store

PASSAGE = (
    "In septic shock, serial lactate measurement guides resuscitation. "
    "Clearance of at least ten per cent within two hours is associated with "
    "improved survival in the cohorts reviewed here."
)
QUOTE = "serial lactate measurement guides resuscitation"
CLAIM = "Lactate clearance guides resuscitation"


def _abstract(marker: str) -> str:
    return (
        f"{marker}. We studied resuscitation targets in septic shock across two "
        "centres. Serial lactate measurement guides resuscitation and predicted "
        "mortality in this cohort of 412 adults admitted to intensive care with "
        "a vasopressor requirement, followed to ninety days."
    )


EVIDENCE_SUPPORTS = {
    "relation": "supports",
    "quote": "Serial lactate measurement guides resuscitation",
    "reasoning": "The abstract states it.",
}
ASSESSMENT_SOUND = {"verdict": "sound", "problems": [], "notes": ""}


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


def synthesis_reply() -> dict:
    return {
        "points": [
            {
                "claim": CLAIM,
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


def build_with(connection, pile_id, database_path, article, *, evidence=None):
    turns = ScriptedTurns(
        synthesis=[synthesis_reply()],
        evidence=[evidence or EVIDENCE_SUPPORTS],
        assessment=[ASSESSMENT_SOUND],
    )
    return run_build(
        connection, pile_id, database_path, turns, FakeProvider(articles=[article])
    )


def accepted(connection, pile_id, database_path):
    """One eligible question, verified against PMID 111."""
    outcome, _, _ = build_with(
        connection,
        pile_id,
        database_path,
        FakeArticle(pmid="111", title="Lactate targets", abstract=_abstract("Original")),
    )
    assert outcome.status == "succeeded"
    assert len(learning.eligible_question_ids(connection)) == 1
    return learning.list_points(connection, pile_id=pile_id)[0]


def links(connection: sqlite3.Connection, point_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT * FROM evidence_links WHERE learning_point_id = ?"
        " ORDER BY basis, verified_at",
        (point_id,),
    ).fetchall()


class TestRecoveryFromACorrection:
    def test_blank_doi_on_an_old_corrected_record_cannot_match_another_pmid(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        connection.execute(
            "UPDATE literature_records SET corrected = 1, doi = '' WHERE id = ?",
            (point.evidence[0].record_id,),
        )
        learning.settle_point(connection, point.id)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        clean = Article(pmid="222", title="Clean", abstract=_abstract("Clean"))
        assert clean.doi == ""  # actual provider default, not FakeArticle's None
        outcome, _, _ = build_with(connection, pile_id, database_path, clean)
        assert outcome.status == "succeeded" and outcome.eligible == 1
        assert [e.pmid for e in learning.get_point(connection, point.id).evidence] == ["222"]

    @pytest.mark.parametrize("flag", ["corrected", "retracted", "is_notice"])
    def test_fresh_safety_notice_holds_an_existing_basis_even_without_new_evidence(
        self, prepared, flag
    ) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        flagged = Article(pmid="111", title="Old", abstract=_abstract("Old"), **{flag: True})
        outcome, _, _ = build_with(connection, pile_id, database_path, flagged)
        assert outcome.status == "succeeded" and outcome.eligible == 0
        assert learning.get_point(connection, point.id).held
        assert len(links(connection, point.id)) == 1  # history retained, not replaced

    @pytest.mark.parametrize("corrected_first", [True, False])
    def test_new_correction_survives_a_later_model_failure_without_rewriting_content(
        self, prepared, corrected_first
    ) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        question = learning.list_questions(connection, pile_id=pile_id)[0]
        before_links = [tuple(row) for row in links(connection, point.id)]
        source_store.reopen_coverage(connection, pile_id=pile_id)
        articles = [
            Article(pmid="111", corrected=True),
            Article(pmid="222", title="Clean", abstract=_abstract("Clean")),
        ]
        if not corrected_first:
            articles.reverse()
        outcome, _, _ = run_build(
            connection, pile_id, database_path,
            ScriptedTurns(synthesis=[synthesis_reply()], evidence=[BridgeTimeout()]),
            FakeProvider(articles=articles),
        )
        assert outcome.status == "failed"
        current = learning.get_question(connection, question.id)
        assert current.reference_answer == question.reference_answer
        assert current.version == question.version
        assert learning.get_point(connection, point.id).held
        assert learning.eligible_question_ids(connection) == []
        assert [tuple(row) for row in links(connection, point.id)] == before_links
        assert source_store.pile_coverage(connection, pile_id).chars_covered == 0

    @pytest.mark.parametrize("flag", ["corrected", "retracted", "is_notice"])
    def test_disqualification_arriving_during_assessment_is_not_cleared_by_upsert(
        self, prepared, flag
    ) -> None:
        from vademecum.model import schemas

        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        record_id = point.evidence[0].record_id
        source_store.reopen_coverage(connection, pile_id=pile_id)
        before = [tuple(row) for row in links(connection, point.id)]

        class CorrectDuringAssessment(ScriptedTurns):
            async def run(self, **kwargs):
                reply = await super().run(**kwargs)
                if kwargs["output_schema"] is schemas.ASSESSMENT_SCHEMA:
                    connection.execute(
                        f"UPDATE literature_records SET {flag} = 1 WHERE id = ?", (record_id,)
                    )
                return reply

        outcome, _, _ = run_build(
            connection, pile_id, database_path,
            CorrectDuringAssessment(
                synthesis=[synthesis_reply()], evidence=[EVIDENCE_SUPPORTS],
                assessment=[ASSESSMENT_SOUND],
            ),
            FakeProvider(articles=[Article(pmid="111", title="Old", abstract=_abstract("Old"))]),
        )
        assert outcome.failure_category == "evidence_changed"
        assert connection.execute(
            f"SELECT {flag} FROM literature_records WHERE id = ?", (record_id,)
        ).fetchone()[flag] == 1
        assert [tuple(row) for row in links(connection, point.id)] == before
        assert source_store.pile_coverage(connection, pile_id).chars_covered == 0
        assert learning.eligible_question_ids(connection) == []

    @pytest.mark.parametrize("returned_corrected", [True, False])
    def test_mixed_corrected_and_clean_results_recover_on_clean_basis_only(
        self, prepared, returned_corrected
    ) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        old_record = point.evidence[0].record_id
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?", (old_record,)
        )
        learning.settle_point(connection, point.id)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()], evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        outcome, _, _ = run_build(
            connection, pile_id, database_path, turns,
            FakeProvider(articles=[
                FakeArticle(pmid="111", title="Old", abstract=_abstract("Old"),
                            corrected=returned_corrected),
                FakeArticle(pmid="222", title="Clean", abstract=_abstract("Clean")),
            ]),
        )
        assert outcome.status == "succeeded" and outcome.eligible == 1
        assert [e.pmid for e in learning.get_point(connection, point.id).evidence] == ["222"]
        assert len(turns.prompts("evidence")) == 1
        assert len(links(connection, point.id)) == 2
        assert next(row for row in links(connection, point.id)
                    if row["record_id"] == old_record)["superseded_at"] is not None

    def test_a_rerun_against_a_clean_paper_makes_the_claim_askable_again(
        self, prepared
    ) -> None:
        """The exact reproduction, end to end, in the direction that must work."""
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        old_record = point.evidence[0].record_id

        # The paper is corrected. The claim is held, correctly.
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?", (old_record,)
        )
        learning.settle_point(connection, point.id)
        assert learning.get_point(connection, point.id).held
        assert learning.eligible_question_ids(connection) == []

        # The owner reopens the material and reruns. A NEW, clean paper answers.
        source_store.reopen_coverage(connection, pile_id=pile_id)
        outcome, _, _ = build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )

        assert outcome.status == "succeeded"
        recovered = learning.get_point(connection, point.id)
        assert recovered.id == point.id, "the same claim, not a duplicate"
        assert recovered.support == learning.EVIDENCE_SUPPORTED
        assert recovered.held is False
        assert recovered.review_state == "machine_reviewed"
        assert len(learning.eligible_question_ids(connection)) == 1

        # The current basis is the new paper; the corrected one survives as audit.
        assert [reference.pmid for reference in recovered.evidence] == ["222"]
        history = links(connection, point.id)
        assert len(history) == 2
        superseded = [row for row in history if row["superseded_at"] is not None]
        assert len(superseded) == 1
        assert superseded[0]["record_id"] == old_record
        assert superseded[0]["superseded_reason"]

    def test_recovery_works_after_a_retraction_too(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        connection.execute(
            "UPDATE literature_records SET retracted = 1 WHERE id = ?",
            (point.evidence[0].record_id,),
        )
        learning.settle_point(connection, point.id)
        assert learning.eligible_question_ids(connection) == []

        source_store.reopen_coverage(connection, pile_id=pile_id)
        build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="333", title="Lactate again", abstract=_abstract("Third")),
        )
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_the_new_basis_is_numbered_and_the_old_one_is_kept(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        assert (
            connection.execute(
                "SELECT evidence_basis FROM learning_points WHERE id = ?", (point.id,)
            ).fetchone()["evidence_basis"]
            == 2
        )

        source_store.reopen_coverage(connection, pile_id=pile_id)
        build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )
        basis = connection.execute(
            "SELECT evidence_basis FROM learning_points WHERE id = ?", (point.id,)
        ).fetchone()["evidence_basis"]
        assert basis == 3
        # Nothing was deleted.
        assert len(links(connection, point.id)) == 2

    def test_a_correction_on_the_new_basis_holds_it_again(self, prepared) -> None:
        """Recovery is not immunity: the new basis is checked like any other."""
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?",
            (point.evidence[0].record_id,),
        )
        learning.settle_point(connection, point.id)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )
        assert len(learning.eligible_question_ids(connection)) == 1

        current = learning.get_point(connection, point.id)
        connection.execute(
            "UPDATE literature_records SET corrected = 1 WHERE id = ?",
            (current.evidence[0].record_id,),
        )
        learning.settle_point(connection, point.id)
        assert learning.eligible_question_ids(connection) == []
        assert learning.get_point(connection, point.id).held


class TestFailurePreservesThePriorBasis:
    def test_a_failed_rerun_leaves_the_accepted_basis_intact(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        before = [tuple(row) for row in links(connection, point.id)]
        basis_before = connection.execute(
            "SELECT evidence_basis FROM learning_points WHERE id = ?", (point.id,)
        ).fetchone()["evidence_basis"]

        source_store.reopen_coverage(connection, pile_id=pile_id)
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()], evidence=[BridgeTimeout()]
        )
        outcome, _, _ = run_build(
            connection,
            pile_id,
            database_path,
            turns,
            FakeProvider(
                articles=[
                    FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("N"))
                ]
            ),
        )

        assert outcome.status == "failed"
        assert [tuple(row) for row in links(connection, point.id)] == before
        assert (
            connection.execute(
                "SELECT evidence_basis FROM learning_points WHERE id = ?", (point.id,)
            ).fetchone()["evidence_basis"]
            == basis_before
        )
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_a_rerun_that_finds_nothing_does_not_discard_the_basis(
        self, prepared
    ) -> None:
        """No evidence is not the same as new evidence. The basis must survive."""
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        before = [tuple(row) for row in links(connection, point.id)]

        source_store.reopen_coverage(connection, pile_id=pile_id)
        turns = ScriptedTurns(synthesis=[synthesis_reply()])
        outcome, _, _ = run_build(
            connection, pile_id, database_path, turns, FakeProvider(articles=[])
        )

        assert outcome.status == "succeeded"
        assert [tuple(row) for row in links(connection, point.id)] == before
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_history_survives_across_several_bases(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        for pmid in ("222", "333", "444"):
            source_store.reopen_coverage(connection, pile_id=pile_id)
            build_with(
                connection,
                pile_id,
                database_path,
                FakeArticle(pmid=pmid, title=f"Paper {pmid}", abstract=_abstract(pmid)),
            )
        history = links(connection, point.id)
        assert len(history) == 4, "every basis is retained"
        live = [row for row in history if row["superseded_at"] is None]
        assert len(live) == 1
        assert len(learning.eligible_question_ids(connection)) == 1


class TestAuditTrailIsReadable:
    def test_a_superseded_link_records_which_run_replaced_it(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        _, run_id, _ = build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )
        live = [row for row in links(connection, point.id) if row["superseded_at"] is None]
        assert live[0]["generation_id"] == run_id

    def test_the_point_view_shows_only_the_active_basis(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        source_store.reopen_coverage(connection, pile_id=pile_id)
        build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )
        shown = learning.get_point(connection, point.id).as_dict()
        assert [entry["pmid"] for entry in shown["evidence"]] == ["222"]

    def test_a_status_change_on_a_superseded_paper_does_not_hold_the_point(
        self, prepared
    ) -> None:
        """History must not reach forward and hold a claim it no longer supports."""
        connection, pile_id, database_path = prepared
        point = accepted(connection, pile_id, database_path)
        old_record = point.evidence[0].record_id
        source_store.reopen_coverage(connection, pile_id=pile_id)
        build_with(
            connection,
            pile_id,
            database_path,
            FakeArticle(pmid="222", title="Lactate revisited", abstract=_abstract("Newer")),
        )
        assert len(learning.eligible_question_ids(connection)) == 1

        # The OLD paper is retracted long after it stopped being the basis.
        connection.execute(
            "UPDATE literature_records SET retracted = 1 WHERE id = ?", (old_record,)
        )
        flagged = learning.flag_points_for_record(
            connection, old_record, reason="Retracted."
        )
        assert flagged == [], "a superseded paper flags nothing"
        assert len(learning.eligible_question_ids(connection)) == 1
