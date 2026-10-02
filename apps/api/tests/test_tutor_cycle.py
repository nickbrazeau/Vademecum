"""Durable five-question passes, without model or network access."""

import random

import pytest

from vademecum.db import connect
from vademecum.storage import learning, piles, sources, tutor
from test_build_atomicity import PASSAGE, accept_a_bank, extraction


def add_question(connection, template, number):
    anchors = tuple(
        learning.DraftCitation(a.source_id, a.segment_id, a.locator, a.quote)
        for a in template.anchors
    )
    identifier, _ = learning.upsert_question(
        connection, pile_id=template.pile_id, point_id=template.learning_point_id,
        generation_id=template.generation_id,
        draft=learning.DraftQuestion(
            prompt=f"Synthetic cycle question {number}?",
            reference_answer=template.reference_answer, rubric=template.rubric,
            anchors=anchors,
        ),
    )
    learning.record_assessment(connection, identifier, assessment="sound")
    assert learning.release_question(connection, identifier).status == "eligible"
    return identifier


@pytest.fixture()
def bank(connection, database_path):
    pile = piles.create_pile(connection, title="Synthetic cycle", tier="mid")
    sources.store_upload(
        connection, pile_id=pile.id, display_name="fixture.txt", media_type="text/plain",
        sha256="a" * 64, byte_size=len(PASSAGE), confidence="mid",
        extraction=extraction(PASSAGE),
    )
    accept_a_bank(connection, pile.id, database_path)
    template = learning.list_questions(connection, pile_id=pile.id)[0]
    identifiers = [template.id]
    identifiers.extend(add_question(connection, template, i) for i in range(1, 5))
    return connection, database_path, template, set(identifiers)


def test_five_questions_do_not_repeat_across_three_complete_passes(bank):
    connection, _, _, identifiers = bank
    rng = random.Random(25)
    current = tutor.next_question(connection, rng=rng)
    previous_last = None
    for expected_cycle in range(1, 4):
        seen = []
        assert current.question.id != previous_last
        for position in range(5):
            assert current.cycle.cycle_number == expected_cycle
            assert current.cycle.position == position
            assert current.cycle.total == 5
            assert current.cycle.remaining == 5 - position
            assert current.question.id not in seen
            # Refreshing, opening the reference, or querying current state is
            # not an advance and must not burn an unseen question.
            refreshed = tutor.next_question(connection, rng=rng)
            assert refreshed.question.id == current.question.id
            assert refreshed.cycle == current.cycle
            seen.append(current.question.id)
            current = tutor.advance_question(connection, current.question.id, rng=rng)
        assert set(seen) == identifiers
        previous_last = seen[-1]


def test_current_question_and_position_survive_a_new_connection(bank):
    connection, path, _, _ = bank
    first = tutor.next_question(connection, rng=random.Random(7))
    current = tutor.advance_question(connection, first.question.id)
    reopened = connect(path)
    try:
        restored = tutor.next_question(reopened)
        assert restored.question.id == current.question.id
        assert restored.cycle == current.cycle
    finally:
        reopened.close()


def test_held_upcoming_question_is_skipped_without_repeating_others(bank):
    connection, _, _, identifiers = bank
    current = tutor.next_question(connection, rng=random.Random(3))
    held_id = connection.execute(
        "SELECT question_id FROM tutor_cycle_entries WHERE cycle_number = 1 AND position = 1"
    ).fetchone()["question_id"]
    learning.record_assessment(connection, held_id, assessment="unsound")
    learning.release_question(connection, held_id)
    seen = []
    while current.cycle.cycle_number == 1:
        assert current.question.id != held_id
        assert current.question.id not in seen
        seen.append(current.question.id)
        current = tutor.advance_question(connection, current.question.id)
    assert set(seen) == identifiers - {held_id}
    assert current.cycle.total == 4
    assert current.question.id != seen[-1]


def test_out_of_order_and_retried_advance_do_not_skip_questions(bank):
    connection, _, _, _ = bank
    current = tutor.next_question(connection)
    future = connection.execute(
        "SELECT question_id FROM tutor_cycle_entries WHERE cycle_number = 1 AND position = 3"
    ).fetchone()["question_id"]
    unchanged = tutor.advance_question(connection, future)
    assert unchanged.question.id == current.question.id
    assert unchanged.cycle.position == 0
    advanced = tutor.advance_question(connection, current.question.id)
    retried = tutor.advance_question(connection, current.question.id)
    assert retried.question.id == advanced.question.id
    assert retried.cycle.position == 1


def test_boundary_redraw_cannot_immediately_repeat_when_other_questions_exist(bank):
    connection, _, _, _ = bank
    current = tutor.next_question(connection)
    for _ in range(4):
        current = tutor.advance_question(connection, current.question.id)
    last = current.question.id

    class LastFirst(random.Random):
        def shuffle(self, items):
            items.remove(last)
            items.insert(0, last)

    next_pass = tutor.advance_question(connection, last, rng=LastFirst(1))
    assert next_pass.cycle.cycle_number == 2
    assert next_pass.question.id != last
    assert next_pass.cycle.total == 5


def test_new_questions_join_the_next_pass_not_the_current_one(bank):
    connection, _, template, identifiers = bank
    current = tutor.next_question(connection)
    added = add_question(connection, template, 99)
    seen = []
    while current.cycle.cycle_number == 1:
        seen.append(current.question.id)
        current = tutor.advance_question(connection, current.question.id)
    assert set(seen) == identifiers and added not in seen
    assert current.cycle.total == 6
    assert connection.execute(
        "SELECT 1 FROM tutor_cycle_entries WHERE cycle_number = 2 AND question_id = ?",
        (added,),
    ).fetchone() is not None


def test_recording_an_answer_keeps_current_question_until_explicit_advance(bank):
    connection, _, _, _ = bank
    current = tutor.next_question(connection)
    tutor.record_attempt(
        connection, question_id=current.question.id, outcome="correct",
        graded_by="model", answer="Synthetic answer",
    )
    restored = tutor.next_question(connection)
    assert restored.question.id == current.question.id
    assert restored.cycle.position == 0
    assert restored.history_count == 1
