"""The build pipeline: coverage, atomicity, and what may reach Tutor.

Every test here drives the real ``BuildRunner`` against a scripted turn factory
and a fake provider. No model call and no socket.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from vademecum.appserver.errors import BridgeUnavailable
from vademecum.db import connect
from vademecum.ingest.extract import Extraction, Segment
from vademecum.model.build import BuildRunner
from vademecum.storage import jobs, learning
from vademecum.storage import piles as pile_store
from vademecum.storage import sources as source_store

CLAIM = "Lactate clearance guides resuscitation in septic shock"
PASSAGE = (
    "In septic shock, serial lactate measurement guides resuscitation. "
    "Clearance of at least ten per cent within two hours is associated with "
    "improved survival in the cohorts reviewed here."
)
QUOTE = "serial lactate measurement guides resuscitation"
ABSTRACT = (
    "We studied resuscitation targets in septic shock. Serial lactate "
    "measurement guides resuscitation and predicted mortality in this cohort."
)
EVIDENCE_QUOTE = "Serial lactate measurement guides resuscitation"


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


def synthesis_reply(*, quote: str = QUOTE, question_quote: str | None = None) -> dict:
    anchor = {"excerpt_id": "E1", "quote": question_quote or quote}
    return {
        "points": [
            {
                "claim": CLAIM,
                "detail": "From the uploaded lecture.",
                "topics": ["septic shock"],
                "citations": [{"excerpt_id": "E1", "quote": quote}],
                "unclear": False,
                "questions": [
                    {
                        "prompt": "How is resuscitation guided in septic shock?",
                        "reference_answer": "By serial lactate measurement.",
                        "rubric": "Mentions serial lactate measurement.",
                        "citations": [anchor],
                    }
                ],
            }
        ],
        "search_topics": ["septic shock"],
    }


EVIDENCE_SUPPORTS = {
    "relation": "supports",
    "quote": EVIDENCE_QUOTE,
    "reasoning": "The abstract states it.",
}
ASSESSMENT_SOUND = {"verdict": "sound", "problems": [], "notes": ""}


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "vademecum.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def prepared(workspace: Path):
    from vademecum.db import apply_migrations

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
    outcome = asyncio.run(
        runner.run(pile_id=pile_id, run_id=run.id, batch_id=batch_id)
    )
    return outcome, run.id, batch_id


class TestEligibility:
    def test_source_only_material_is_kept_but_never_asked(self, prepared) -> None:
        """No literature match: a draft point, a held question, an empty Tutor."""
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(synthesis=[synthesis_reply()])
        outcome, _, _ = run_build(
            connection, pile_id, database_path, turns, FakeProvider(articles=[])
        )

        assert outcome.status == "succeeded"
        assert outcome.points == 1
        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED
        assert point.claim == CLAIM  # kept and visible, not discarded
        assert learning.eligible_question_ids(connection) == []
        question = learning.list_questions(connection, pile_id=pile_id)[0]
        assert question.status == "held"
        assert "published literature" in question.hold_reason

    def test_evidence_plus_assessment_is_what_makes_a_question_askable(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        outcome, _, _ = run_build(connection, pile_id, database_path, turns, provider)

        assert outcome.eligible == 1
        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.EVIDENCE_SUPPORTED
        # We only retrieved an abstract, so that is all we may claim to have read.
        assert point.evidence_grade == learning.GRADE_ABSTRACT
        assert len(learning.eligible_question_ids(connection)) == 1

    def test_an_unsound_assessment_holds_the_question_despite_good_evidence(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[
                {
                    "verdict": "unsound",
                    "problems": ["answer_not_in_passage"],
                    "notes": "The reference answer adds a threshold the passage omits.",
                }
            ],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        assert learning.eligible_question_ids(connection) == []
        question = learning.list_questions(connection, pile_id=pile_id)[0]
        assert question.assessment == "unsound"
        assert "threshold" in question.hold_reason

    def test_evidence_supports_needs_a_real_quote_not_an_empty_one(
        self, prepared
    ) -> None:
        """`"" in abstract` is true; an empty quote must not become support."""
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[{"relation": "supports", "quote": "", "reasoning": "trust me"}],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED
        assert point.evidence == ()

    def test_a_quote_that_is_not_in_the_returned_abstract_is_discarded(
        self, prepared
    ) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[
                {
                    "relation": "supports",
                    "quote": "This trial proved lactate clearance saves lives outright",
                    "reasoning": "invented",
                }
            ],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED

    def test_a_retracted_record_is_never_supporting_evidence(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[
                FakeArticle(
                    pmid="111", title="Lactate", abstract=ABSTRACT, retracted=True
                )
            ]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        point = learning.list_points(connection, pile_id=pile_id)[0]
        assert point.support == learning.SOURCE_SUPPORTED
        assert learning.eligible_question_ids(connection) == []

    def test_a_fabricated_source_quote_drops_the_whole_point(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply(quote="dobutamine is first line in every case")]
        )
        outcome, run_id, _ = run_build(
            connection, pile_id, database_path, turns, FakeProvider()
        )

        assert outcome.status == "failed"
        assert outcome.failure_category == "invalid_output"
        assert learning.list_points(connection, pile_id=pile_id) == []


class TestTransmissionIsBoundedByConsent:
    def test_only_the_consented_slice_is_ever_sent(self, workspace) -> None:
        """A quote from beyond the previewed range does not become a citation."""
        from vademecum.db import apply_migrations

        connection = connect(workspace)
        apply_migrations(connection)
        pile = pile_store.create_pile(connection, title="Long", tier="mid")
        head = "A " * 60 + "the opening passage that will be sent first. "
        tail = "TAIL_NEVER_PREVIEWED sentence hidden far beyond the first excerpt."
        body = head + ("filler sentence here. " * 3000) + tail  # well past one batch budget
        source_store.store_upload(
            connection,
            pile_id=pile.id,
            display_name="long.txt",
            media_type="text/plain",
            sha256="b" * 64,
            byte_size=len(body),
            confidence="mid",
            extraction=extraction(body),
        )

        batch = source_store.next_batch(connection, pile.id)
        sent = "\n".join(excerpt.text for excerpt in batch.excerpts)
        assert "TAIL_NEVER_PREVIEWED" not in sent, "the tail must not be in batch one"

        turns = ScriptedTurns(
            synthesis=[
                {
                    "points": [
                        {
                            "claim": "Something about the tail",
                            "detail": "",
                            "topics": [],
                            "citations": [
                                {
                                    "excerpt_id": "E1",
                                    "quote": "TAIL_NEVER_PREVIEWED sentence hidden far beyond",
                                }
                            ],
                            "unclear": False,
                            "questions": [],
                        }
                    ],
                    "search_topics": [],
                }
            ]
        )
        batch_id = source_store.record_batch(connection, pile.id, batch)
        run = jobs.start_run(
            connection,
            pile_id=pile.id,
            source_count=1,
            excerpt_count=len(batch.excerpts),
            excerpt_chars=batch.excerpt_chars,
        )
        runner = BuildRunner(
            database_path=workspace, turn_factory=turns, provider=FakeProvider()
        )
        outcome = asyncio.run(
            runner.run(pile_id=pile.id, run_id=run.id, batch_id=batch_id)
        )

        # The quote exists in the stored segment but not in what was sent, so it
        # is rejected -- checking against the segment would have accepted it.
        assert outcome.failure_category == "invalid_output"
        assert learning.list_points(connection, pile_id=pile.id) == []
        connection.close()

    def test_assessment_sees_only_the_excerpt_and_the_evidence(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        run_build(connection, pile_id, database_path, turns, provider)

        prompt = turns.prompts("assessment")[0]
        assert QUOTE in prompt  # the consented excerpt
        assert "PUBLISHED RECORD TEXT" in prompt  # and the evidence
        assert EVIDENCE_QUOTE in prompt

    def test_a_stale_selection_is_refused(self, prepared) -> None:
        connection, pile_id, _ = prepared
        batch = source_store.next_batch(connection, pile_id)
        batch_id = source_store.record_batch(connection, pile_id, batch)
        source = source_store.list_sources(connection, pile_id=pile_id)[0]
        source_store.set_excluded(connection, source.id, excluded=True)

        with pytest.raises(source_store.ConflictError) as caught:
            source_store.claim_batch(
                connection,
                pile_id=pile_id,
                batch_id=batch_id,
                expected_hash=batch.selection_hash,
            )
        assert caught.value.kind == "selection_changed"


class TestAtomicity:
    def test_a_failed_rerun_leaves_accepted_material_untouched(self, prepared) -> None:
        connection, pile_id, database_path = prepared
        good = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )
        run_build(connection, pile_id, database_path, good, provider)

        before = learning.list_points(connection, pile_id=pile_id)[0]
        eligible_before = learning.eligible_question_ids(connection)
        coverage_before = source_store.pile_coverage(connection, pile_id).as_dict()
        assert eligible_before

        # Add more material so a second batch exists, then fail its turn.
        source_store.store_upload(
            connection,
            pile_id=pile_id,
            display_name="second.txt",
            media_type="text/plain",
            sha256="c" * 64,
            byte_size=40,
            confidence="mid",
            extraction=extraction("Another passage about vasopressor selection here."),
        )
        failing = ScriptedTurns(synthesis=[BridgeUnavailable("process_exited")])
        outcome, _, _ = run_build(
            connection, pile_id, database_path, failing, provider
        )

        assert outcome.status == "failed"
        after = learning.get_point(connection, before.id)
        assert after.support == before.support
        assert after.claim == before.claim
        assert learning.eligible_question_ids(connection) == eligible_before
        # The failed batch advanced nothing.
        assert (
            source_store.pile_coverage(connection, pile_id).chars_covered
            == coverage_before["chars_covered"]
        )

    def test_a_failure_during_commit_rolls_the_whole_batch_back(
        self, prepared, monkeypatch
    ) -> None:
        connection, pile_id, database_path = prepared
        turns = ScriptedTurns(
            synthesis=[synthesis_reply()],
            evidence=[EVIDENCE_SUPPORTS],
            assessment=[ASSESSMENT_SOUND],
        )
        provider = FakeProvider(
            articles=[FakeArticle(pmid="111", title="Lactate", abstract=ABSTRACT)]
        )

        # Blow up after the point is written but before the batch commits.
        original = learning.release_question

        def explode(*args, **kwargs):
            raise RuntimeError("commit interrupted")

        monkeypatch.setattr(learning, "release_question", explode)
        outcome, _, batch_id = run_build(
            connection, pile_id, database_path, turns, provider
        )
        monkeypatch.setattr(learning, "release_question", original)

        assert outcome.status == "failed"
        # Nothing partial survived, and coverage did not move.
        assert learning.list_points(connection, pile_id=pile_id) == []
        assert learning.list_questions(connection, pile_id=pile_id) == []
        assert source_store.pile_coverage(connection, pile_id).chars_covered == 0
        status = connection.execute(
            "SELECT status FROM build_batches WHERE id = ?", (batch_id,)
        ).fetchone()["status"]
        assert status == "failed"

    def test_an_interrupted_run_is_reported_as_interrupted(self, prepared) -> None:
        connection, pile_id, _ = prepared
        run = jobs.start_run(
            connection, pile_id=pile_id, source_count=1, excerpt_count=1, excerpt_chars=1
        )
        assert jobs.get_run(connection, run.id).status == "running"

        assert jobs.sweep_interrupted(connection) == 1
        closed = jobs.get_run(connection, run.id)
        assert closed.status == "failed"
        assert closed.failure_category == "interrupted"
        assert "interrupted" in closed.as_dict()["failure_detail"].lower()
