"""One page per subject (feedback of 11 October; ADR 0035): related pages are gathered into
one, everything on them moves with them, and new topics join a page instead of making one."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from vademecum.appserver.turns import TurnResult
from vademecum.model import schemas
from vademecum.model import subjects as model
from vademecum.storage import encyclopedia as pages
from vademecum.storage import learner
from vademecum.storage import subjects as store
from vademecum.storage.maintenance import EXPORTED_TABLES
from vademecum.storage.sync import DOMI_OWNED, SYNCED_TABLES

NOW = "2026-10-11T00:00:00Z"


def _pile(connection: sqlite3.Connection) -> None:
    connection.execute("INSERT INTO piles (id, title, tier, description, created_at, updated_at) VALUES ('pil_1', 'Book', 'high', '', ?, ?)", (NOW, NOW))


def _point(connection: sqlite3.Connection, point_id: str, *topics: str) -> None:
    connection.execute(
        "INSERT INTO learning_points (id, pile_id, generation_id, claim, detail, support, evidence_grade, review_state, held, hold_reason, content_hash, created_at, updated_at)"
        " VALUES (?, 'pil_1', NULL, ?, '', 'source_supported', 'none', 'machine_reviewed', 0, '', ?, ?, ?)",
        (point_id, f"claim {point_id}", point_id, NOW, NOW),
    )
    for topic in topics:
        connection.execute("INSERT INTO learning_point_topics (learning_point_id, topic) VALUES (?, ?)", (point_id, topic))


def _page(connection: sqlite3.Connection, topic: str, point_ids: list[str]) -> pages.Entry:
    found = [p for p in pages.points_for_subject(connection, topic) if p.id in point_ids]
    return pages.upsert_entry(
        connection,
        topic=topic,
        title=topic,
        specialty_id="infectious-disease",
        summary="",
        sections=[{"heading": "H", "paragraphs": [{"text": "t", "point_ids": point_ids}]}],
        point_ids=point_ids,
        points_hash_value=pages.points_hash(found),
    )


def _antifungals(connection: sqlite3.Connection) -> tuple[pages.Entry, pages.Entry, pages.Entry]:
    _pile(connection)
    _point(connection, "lp_1", "Antifungal mechanism")
    _point(connection, "lp_2", "Antifungal mechanisms")
    _point(connection, "lp_3", "Antifungal spectrum")
    _point(connection, "lp_4", "Antifungal spectrum")
    connection.commit()
    return (
        _page(connection, "Antifungal mechanism", ["lp_1"]),
        _page(connection, "Antifungal mechanisms", ["lp_2"]),
        _page(connection, "Antifungal spectrum", ["lp_3", "lp_4"]),
    )


def test_spelling_variants_share_a_key() -> None:
    assert store.spelling_key("Antifungal mechanisms") == store.spelling_key("Antifungal mechanism")
    assert store.spelling_key("Beta-blockers") == store.spelling_key("beta blocker")
    assert store.spelling_key("Bloodstream infections") == store.spelling_key("Bloodstream infection")
    assert store.spelling_key("Pseudomonas") == store.spelling_key("pseudomonas") and store.spelling_key("Sepsis") == "sepsis"
    assert store.spelling_key("Antifungal spectrum") != store.spelling_key("Antifungal mechanism")


def test_a_merge_moves_everything_and_loses_no_history(connection: sqlite3.Connection) -> None:
    one, two, spectrum = _antifungals(connection)
    question = pages.insert_questions(
        connection,
        two,
        [{"stem": "A 40-year-old ...", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": "x", "objective": "o", "point_ids": ["lp_2"], "hold_reason": ""}],
    )
    assert question["written"] == 1
    asked = pages.questions_for_entry(connection, two.id)[0]
    pages.record_attempt(connection, asked, 0)
    connection.execute(
        "INSERT INTO flashcards (id, entry_id, topic, front, back, point_ids, status, content_hash, created_at, updated_at)"
        " VALUES ('fc_1', ?, 'Antifungal mechanisms', 'f', 'b', '[\"lp_2\"]', 'eligible', 'h', ?, ?)",
        (two.id, NOW, NOW),
    )
    connection.execute("INSERT INTO flashcard_reviews (id, card_id, rating, created_at) VALUES ('fr_1', 'fc_1', 'good', ?)", (NOW,))
    connection.commit()

    assert store.merge(connection, spectrum.id, [one.id, two.id], title="Antifungal agents")["merged"] == 2

    survivor = pages.get_entry(connection, spectrum.id)
    assert survivor.title == "Antifungal agents" and survivor.topic == "Antifungal spectrum"
    assert pages.get_entry(connection, two.id).id == spectrum.id, "an old link opens the page it went into"
    assert [q.id for q in pages.questions_for_entry(connection, spectrum.id)] == [asked.id]
    assert connection.execute("SELECT question_id FROM board_attempts").fetchone()["question_id"] == asked.id, "the answer stays"
    assert connection.execute("SELECT entry_id FROM flashcards WHERE id = 'fc_1'").fetchone()["entry_id"] == spectrum.id
    assert connection.execute("SELECT card_id FROM flashcard_reviews WHERE id = 'fr_1'").fetchone()["card_id"] == "fc_1"
    assert connection.execute("SELECT COUNT(*) FROM encyclopedia_entries").fetchone()[0] == 1

    assert set(store.members(connection, "Antifungal spectrum")) == {"Antifungal spectrum", "Antifungal mechanism", "Antifungal mechanisms"}
    assert {p.id for p in pages.points_for_subject(connection, "Antifungal spectrum")} == {"lp_1", "lp_2", "lp_3", "lp_4"}
    assert pages.topics_to_compile(connection) == ["Antifungal spectrum"], "written again, once, from all its points"
    assert pages.entry_for_topic(connection, "Antifungal mechanisms").id == spectrum.id
    units = learner._gather(connection)  # noqa: SLF001
    page_unit = units[f"page:{spectrum.id}"]
    assert "antifungal mechanisms" in page_unit.names, "the learner model counts a filed topic as the page"


def test_filing_never_makes_chains(connection: sqlite3.Connection) -> None:
    from vademecum.db import transaction

    with transaction(connection) as tx:
        store.file_under(tx, "A", "B")
        store.file_under(tx, "B", "C")
        store.file_under(tx, "D", "A")
    assert store.filed(connection) == {"A": "C", "B": "C", "D": "C"}


class GroupTurns:
    """A model connection that answers the grouping turn with one group, and records prompts."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def __call__(self):
        outer = self

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                outer.prompts.append(prompt)
                assert output_schema in (schemas.GROUP_SCHEMA, schemas.PLACE_SCHEMA)
                return TurnResult(payload=outer.payload, raw_chars=10, turn_id="t", duration_ms=1.0)

        return Runner()


def test_consolidation_merges_by_spelling_then_by_the_models_groups_once(connection: sqlite3.Connection, database_path: Path) -> None:
    _antifungals(connection)
    _point(connection, "lp_5", "Sepsis")
    connection.commit()
    _page(connection, "Sepsis", ["lp_5"])
    turns = GroupTurns({"groups": [{"title": "Antifungal agents", "pages": ["g1", "g2", "g9"]}]})

    report = asyncio.run(model.consolidate(database_path, turns))
    assert report["merged"] == 2, "mechanism/mechanisms by spelling, then the spectrum page by the model"
    titles = sorted(row["title"] for row in connection.execute("SELECT title FROM encyclopedia_entries"))
    assert titles == ["Antifungal agents", "Sepsis"]
    assert "Antifungal" in turns.prompts[0] and "lp_" not in turns.prompts[0], "page titles only, nothing of the points"
    assert asyncio.run(model.consolidate(database_path, turns)) == {"skipped": True}
    assert len(turns.prompts) == 1


def test_two_pages_the_owner_edited_are_never_folded_together(connection: sqlite3.Connection, database_path: Path) -> None:
    from vademecum.storage import page_files

    one, _two, spectrum = _antifungals(connection)
    page_files.set_edit(connection, one.id, "# Mine")
    page_files.set_edit(connection, spectrum.id, "# Also mine")
    turns = GroupTurns({"groups": [{"title": "Antifungal agents", "pages": ["g1", "g2"]}]})
    asyncio.run(model.consolidate(database_path, turns))
    remaining = {row["id"] for row in connection.execute("SELECT id FROM encyclopedia_entries")}
    assert one.id in remaining and spectrum.id in remaining
    assert pages.get_entry(connection, one.id).body_md == "# Mine", "an edited page keeps its text and stays"


def test_new_topics_join_a_page_by_spelling_or_by_the_model_or_get_their_own(connection: sqlite3.Connection, database_path: Path) -> None:
    _antifungals(connection)
    _point(connection, "lp_6", "Antifungal Spectrum")  # spelling
    _point(connection, "lp_7", "Antifungal toxicity")  # the model puts it on the spectrum page
    _point(connection, "lp_8", "Sarcoidosis")  # nothing it could join
    connection.commit()
    assert "Antifungal toxicity" not in pages.topics_to_compile(connection), "not written until placed"
    spectrum_handle = None

    class PlaceTurns(GroupTurns):
        def __call__(self):
            outer = self

            class Runner:
                async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                    nonlocal spectrum_handle
                    outer.prompts.append(prompt)
                    assert output_schema is schemas.PLACE_SCHEMA
                    line = next(line for line in prompt.splitlines() if "Antifungal spectrum" in line)
                    spectrum_handle = line.split("]")[0].strip("[ ")
                    return TurnResult(payload={"placements": [{"topic": "t1", "page": spectrum_handle}]}, raw_chars=10, turn_id="t", duration_ms=1.0)

            return Runner()

    turns = PlaceTurns({})
    tally = asyncio.run(model.place_new_topics(database_path, turns))
    assert tally == {"by_spelling": 1, "by_model": 1, "own": 1, "covered": 0, "waiting": 0}
    assert store.subject_of(connection, "Antifungal Spectrum") == "Antifungal spectrum"
    assert store.subject_of(connection, "Antifungal toxicity") == "Antifungal spectrum"
    stale = pages.topics_to_compile(connection)
    assert "Sarcoidosis" in stale and "Antifungal toxicity" not in stale and "Antifungal spectrum" in stale
    assert "Sarcoidosis" not in turns.prompts[0], "a topic with no page it could join is not asked about"


def test_the_topic_table_is_the_macs_and_travels() -> None:
    assert "page_topics" in SYNCED_TABLES and "page_topics" in DOMI_OWNED and "page_topics" in EXPORTED_TABLES


def test_a_new_topic_whose_points_are_all_on_pages_gets_no_page_of_its_own(connection: sqlite3.Connection, database_path: Path) -> None:
    """A page for it would only repeat them: it waits until it brings a point of its own."""
    _antifungals(connection)
    connection.execute("INSERT INTO learning_point_topics (learning_point_id, topic) VALUES ('lp_3', 'Echinocandins')")
    connection.commit()
    tally = asyncio.run(model.place_new_topics(database_path, GroupTurns({"placements": []})))
    assert tally["covered"] == 1 and tally["own"] == 0
    assert "Echinocandins" not in pages.topics_to_compile(connection)
    _point(connection, "lp_9", "Echinocandins")
    connection.commit()
    tally = asyncio.run(model.place_new_topics(database_path, GroupTurns({"placements": []})))
    assert tally["covered"] == 0, "with a point of its own it is placed"


def test_related_new_topics_share_one_new_page(connection: sqlite3.Connection, database_path: Path) -> None:
    _pile(connection)
    _point(connection, "lp_a", "AmpC derepression")
    _point(connection, "lp_b", "AmpC hydrolysis")
    connection.commit()

    class Together(GroupTurns):
        def __call__(self):
            outer = self

            class Runner:
                async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                    outer.prompts.append(prompt)
                    # t1 is "AmpC derepression"; g1 is the other new topic, offered as a place.
                    assert "(a new topic, no page yet)" in prompt
                    return TurnResult(payload={"placements": [{"topic": "t1", "page": "g1"}, {"topic": "t2", "page": ""}]}, raw_chars=10, turn_id="t", duration_ms=1.0)

            return Runner()

    asyncio.run(model.place_new_topics(database_path, Together({})))
    assert store.subject_of(connection, "AmpC derepression") == "AmpC hydrolysis"
    assert pages.topics_to_compile(connection) == ["AmpC hydrolysis"], "one page for the two"
    assert {p.id for p in pages.points_for_subject(connection, "AmpC hydrolysis")} == {"lp_a", "lp_b"}


def test_the_same_question_on_two_pages_merges_into_one_keeping_the_answers(connection: sqlite3.Connection) -> None:
    """Live, 11 October: two fragment pages held the same question; moving both onto one page
    broke its 'no question twice' rule and rolled every merge back. One copy stays now, and
    the other's answers move to it."""
    one, two, spectrum = _antifungals(connection)
    draft = {"stem": "A 40-year-old ...", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": "x", "objective": "o", "point_ids": ["lp_1"], "hold_reason": ""}
    pages.insert_questions(connection, spectrum, [draft])
    pages.insert_questions(connection, two, [{**draft, "point_ids": ["lp_2"]}])
    duplicate = pages.questions_for_entry(connection, two.id)[0]
    pages.record_attempt(connection, duplicate, 1)
    connection.execute(
        "INSERT INTO flashcards (id, entry_id, topic, front, back, point_ids, status, content_hash, created_at, updated_at)"
        " VALUES ('fc_a', ?, 't', 'f', 'b', '[]', 'eligible', 'same', ?, ?), ('fc_b', ?, 't', 'f', 'b', '[]', 'eligible', 'same', ?, ?)",
        (spectrum.id, NOW, NOW, two.id, NOW, NOW),
    )
    connection.execute("INSERT INTO flashcard_reviews (id, card_id, rating, created_at) VALUES ('fr_b', 'fc_b', 'again', ?)", (NOW,))
    connection.commit()

    assert store.merge(connection, spectrum.id, [one.id, two.id])["merged"] == 2
    kept = pages.questions_for_entry(connection, spectrum.id)
    assert len(kept) == 1, "one copy of the question"
    assert connection.execute("SELECT question_id FROM board_attempts").fetchone()["question_id"] == kept[0].id, "the answer moved to it"
    assert connection.execute("SELECT COUNT(*) FROM flashcards").fetchone()[0] == 1
    assert connection.execute("SELECT card_id FROM flashcard_reviews WHERE id = 'fr_b'").fetchone()["card_id"] == "fc_a"


def test_grouping_is_asked_once_per_specialty_however_merges_change_its_size(connection: sqlite3.Connection, database_path: Path) -> None:
    _antifungals(connection)
    turns = GroupTurns({"groups": []})
    store.mark_consolidated(connection, {"complete": False, "done": ["infectious-disease:0:58"], "merged": 0})
    asyncio.run(model.consolidate(database_path, turns))
    assert turns.prompts == [], "a specialty asked before, at another size, is not asked again"
    assert store.consolidated(connection)["complete"] is True
