"""The learner model behind the Improvement Map (ADR 0031): what is understood, what is
holding, what each topic needs next, and how that steers the flashcards and the Tutor."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.storage import encyclopedia as pages
from vademecum.storage import learner
from vademecum.storage.learner import Evidence

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- the estimates -----------------------------------------------------------------


def test_with_no_evidence_the_estimate_is_even_and_rests_on_nothing() -> None:
    understood, confidence = learner.mastery([], NOW)
    assert understood == 0.5 and confidence == 0.0
    assert learner.half_life([]) == (learner.START_HALF_LIFE, None)
    assert learner.recall_now(1.0, None, NOW) is None


def test_right_answers_raise_the_estimate_and_old_evidence_counts_for_less() -> None:
    recent = [Evidence("board", 1.0, _ago(1)) for _ in range(4)]
    old = [Evidence("board", 1.0, _ago(240)) for _ in range(4)]
    up, sure = learner.mastery(recent, NOW)
    up_old, sure_old = learner.mastery(old, NOW)
    assert up > 0.8 and sure > 0.5
    assert 0.5 < up_old < up and sure_old < sure, "four months old is a quarter of the weight per half-life"
    down, _ = learner.mastery([Evidence("board", 0.0, _ago(1)) for _ in range(4)], NOW)
    assert down < 0.2


def test_spaced_successes_last_longer_than_massed_ones() -> None:
    """The spacing effect: the same three right answers, a minute apart or days apart."""
    massed = [Evidence("card", 1.0, _ago(10) + timedelta(minutes=i)) for i in range(3)]
    spaced = [Evidence("card", 1.0, _ago(10 - 3 * i)) for i in range(3)]
    h_massed, _ = learner.half_life(massed)
    h_spaced, _ = learner.half_life(spaced)
    assert h_spaced > 2 * h_massed
    h_lapsed, _ = learner.half_life(spaced + [Evidence("card", 0.0, _ago(0.5))])
    assert h_lapsed == pytest.approx(h_spaced * learner.LAPSE_FACTOR), "a miss halves it"


def test_recall_halves_every_half_life() -> None:
    assert learner.recall_now(4.0, _ago(4), NOW) == pytest.approx(0.5)
    assert learner.recall_now(4.0, _ago(0), NOW) == pytest.approx(1.0)


# --- the units, from what the learner did -------------------------------------------------


def _page(connection, topic: str, *, specialty: str | None = None, questions: int = 1):
    entry = pages.upsert_entry(connection, topic=topic, title=topic.title(), specialty_id=specialty, summary="s", sections=[], point_ids=[], points_hash_value=topic)
    pages.insert_questions(
        connection,
        entry,
        [{"stem": f"A {topic} vignette {i}?", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": "x", "point_ids": [], "hold_reason": ""} for i in range(questions)],
    )
    return entry, pages.questions_for_entry(connection, entry.id)


def _answer(connection, question, *, correct: bool, at: datetime) -> None:
    connection.execute(
        "INSERT INTO board_attempts (id, question_id, asked_stem, chosen_index, correct, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (f"ba_{question.id}_{at.timestamp()}", question.id, question.stem, 0 if correct else 1, 1 if correct else 0, _stamp(at)),
    )
    connection.commit()


def _flag(connection, topic: str, text: str = "Not sure about this") -> None:
    count = connection.execute("SELECT COUNT(*) FROM knowledge_gap_flags").fetchone()[0]
    connection.execute(
        "INSERT INTO knowledge_gap_flags (id, text, topic, pile_id, status, created_at, updated_at, addressed_at)"
        " VALUES (?, ?, ?, NULL, 'open', ?, ?, NULL)",
        (f"kgf_{count}", text, topic, _stamp(_ago(1)), _stamp(_ago(1))),
    )
    connection.commit()


@pytest.fixture()
def eligible_everything(monkeypatch):
    """The unit tests' questions cite no machine-reviewed points, so every one counts as askable."""
    monkeypatch.setattr(pages, "eligible_board_ids", lambda c: [r["id"] for r in c.execute("SELECT id FROM board_questions")])


def test_each_page_gets_a_state_from_what_was_answered(connection, eligible_everything) -> None:
    _, (forming_q,) = _page(connection, "blastomycosis")
    _, (holding_q,) = _page(connection, "candidemia")
    _, (fading_q,) = _page(connection, "histoplasmosis")
    _page(connection, "aspergillosis")
    for day in (20, 13, 6):
        _answer(connection, forming_q, correct=False, at=_ago(day))
    for day in (9, 5, 1, 0.1):
        _answer(connection, holding_q, correct=True, at=_ago(day))
    for day in (40, 39):
        _answer(connection, fading_q, correct=True, at=_ago(day))
    found = learner.model(connection, now=NOW)
    state = {unit["topic"]: unit["state"] for unit in found["units"]}
    assert state["blastomycosis"] == "forming"
    assert state["candidemia"] == "holding"
    assert state["histoplasmosis"] == "fading"
    assert "aspergillosis" not in state, "untried pages with no signal are counted, not listed"
    assert next(s["count"] for s in found["states"] if s["state"] == "untried") == 1
    blasto = next(u for u in found["units"] if u["topic"] == "blastomycosis")
    assert "Board questions: 0 of 3 right." in blasto["evidence"] and blasto["next"]["kind"] == "socratic"
    histo = next(u for u in found["units"] if u["topic"] == "histoplasmosis")
    assert histo["next"]["kind"] == "board" and "slipping" in histo["next"]["why"]


def test_a_flag_points_the_plan_at_its_page_and_a_flag_without_one_asks_for_a_source(connection, eligible_everything) -> None:
    _page(connection, "cirrhosis", specialty="gastroenterology")
    _page(connection, "asthma", specialty="pulmonology")
    _flag(connection, "Infections in cirrhosis")
    _flag(connection, "Leucovorin rescue")
    _flag(connection, "Leucovorin dosing")
    found = learner.model(connection, now=NOW)
    first = found["plan"][0]
    assert first["topic"] == "cirrhosis" and first["open_flags"] == 1 and first["next"]["kind"] == "read"
    assert found["by_name"]["infections in cirrhosis"] == first["key"], "the map's topic node finds its page's state"
    sourceless = [step for step in found["plan"] if step["next"]["kind"] == "add_source"]
    assert len(sourceless) == 1 and sourceless[0]["title"].startswith("Leucovorin")
    assert found["plan"][-1]["topic"] == "asthma", "a page nothing points to comes last, as something to explore"


def test_the_plan_interleaves_specialties() -> None:
    def item(key: str, specialty: str, need: float) -> dict:
        return {"key": key, "specialty_id": specialty, "need": need, "next": {"kind": "board"}}

    assessed = [item("a1", "cards", 2.0), item("a2", "cards", 1.9), item("a3", "cards", 1.8), item("b1", "renal", 1.0), item("c1", "id", 0.9)]
    plan = learner.interleave(assessed)
    keys = [step["key"] for step in plan]
    assert keys[0] == "a1" and sorted(keys) == ["a1", "a2", "a3", "b1", "c1"]
    subjects = [step["specialty_id"] for step in plan]
    assert all(a != b for a, b in zip(subjects, subjects[1:])), f"no two in a row from one specialty: {keys}"


def test_where_you_need_it_most_asks_the_neediest_page_then_moves_to_another(connection, eligible_everything) -> None:
    weak, weak_qs = _page(connection, "blastomycosis", questions=2)
    other, _ = _page(connection, "candidemia")
    _flag(connection, "blastomycosis")
    _flag(connection, "blastomycosis", "and its treatment")
    assert learner.most_needed_entry(connection, kind="board", now=NOW) == weak.id
    assert learner.most_needed_entry(connection, kind="board", not_entry=weak.id, now=NOW) == other.id
    assert learner.most_needed_entry(connection, kind="cards", now=NOW) is None, "no cards written"


def test_a_fading_page_brings_its_flashcards_forward(connection) -> None:
    from vademecum.storage import flashcards

    entry, (question,) = _page(connection, "histoplasmosis")
    for day in (40, 39):
        _answer(connection, question, correct=True, at=_ago(day))
    factors = learner.factor_for_entry(connection, now=NOW)
    assert factors[entry.id][0] > 1.0 and "Fading" in (factors[entry.id][1] or "")
    card = flashcards.Flashcard(
        id="fc_1", entry_id=entry.id, topic="histoplasmosis", title="Histoplasmosis", front="f", back="b", point_ids=(),
        status="eligible", hold_reason="", entry_version=1, created_at="now",
    )
    plain, _ = flashcards.weigh(card, weights={"flagged": set(), "below": set(), "missed": set()}, last=None)
    steered, reasons = flashcards.weigh(card, weights={"flagged": set(), "below": set(), "missed": set(), "retention": factors}, last=None)
    assert steered > plain and any("Fading" in reason for reason in reasons)


def test_the_route_and_the_board_focus(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(pages, "eligible_board_ids", lambda c: [r["id"] for r in c.execute("SELECT id FROM board_questions")])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    with TestClient(create_app(settings, transport_factory=refusing_factory()), base_url=LOCAL_ORIGIN) as client:
        from vademecum.db import connect

        connection = connect(settings.database_path)
        try:
            weak, _ = _page(connection, "blastomycosis", questions=2)
            other, _ = _page(connection, "candidemia")
            _flag(connection, "blastomycosis")
        finally:
            connection.close()
        model = client.get("/api/learner").json()
        assert model["plan"][0]["entry_id"] == weak.id
        assert {"plan", "units", "states", "by_name", "by_entry"} <= set(model)
        first = client.get("/api/tutor/board/next", params={"focus": "need"}).json()
        assert first["question"]["entry_id"] == weak.id
        after = client.post("/api/tutor/board/advance", json={"question_id": first["question"]["id"], "focus": "need"}).json()
        assert after["question"]["entry_id"] == other.id, "interleaved: the next page, not the same one again"
        body = client.get("/api/learner").text.lower()
        for forbidden in ("streak", "due count", "review queue", "items due", "overdue"):
            assert forbidden not in body


def test_need_moves_across_pages_rather_than_alternating_two(connection, eligible_everything) -> None:
    """Review of ADR 0031: two pages missed over and over must not hold the turn between them."""
    a, (qa,) = _page(connection, "blastomycosis")
    b, (qb,) = _page(connection, "candidemia")
    c, _ = _page(connection, "histoplasmosis")
    for topic in ("blastomycosis", "candidemia"):
        _flag(connection, topic)
        _flag(connection, topic, "again")
    _answer(connection, qa, correct=False, at=NOW - timedelta(minutes=5))
    _answer(connection, qb, correct=False, at=NOW - timedelta(minutes=2))
    assert learner.most_needed_entry(connection, kind="board", not_entry=b.id, now=NOW) == c.id
    later = NOW + timedelta(hours=2)
    assert learner.most_needed_entry(connection, kind="board", now=later) in {a.id, b.id}, "after a rest, need leads again"


def test_a_deleted_page_is_not_suggested_back(connection, eligible_everything) -> None:
    entry, _ = _page(connection, "sarcoidosis")
    _flag(connection, "sarcoidosis")
    pages.delete_entry(connection, entry.id)
    found = learner.model(connection, now=NOW)
    assert all("sarcoidosis" not in unit["names"] for unit in found["units"])


def test_today_recall_is_a_card_from_where_the_plan_points(connection, eligible_everything, monkeypatch) -> None:
    """Feedback of 9 October: Today's one thing to recall replaces "worth a look"."""
    from vademecum.storage import flashcards

    weak, _ = _page(connection, "cirrhosis", specialty="gastroenterology")
    other, _ = _page(connection, "asthma", specialty="pulmonology")
    _flag(connection, "cirrhosis")
    for entry in (weak, other):
        flashcards.insert_cards(connection, entry_id=entry.id, topic=entry.topic, entry_version=entry.version,
                                drafts=[{"front": f"{entry.topic} front?", "back": "b", "points": [], "point_ids": [], "hold_reason": ""}])
    monkeypatch.setattr(flashcards, "eligible_card_ids", lambda c: [r["id"] for r in c.execute("SELECT id FROM flashcards")])
    found = learner.recall_prompt(connection, now=NOW)
    assert found["card"]["entry_id"] == weak.id and found["card"]["front"] == "cirrhosis front?"
    assert found["unit"]["title"] == "Cirrhosis" and found["kind"] == "recall"
    assert set(found["intervals"]) == {"again", "good"}


def test_an_answered_recall_card_gives_way_to_another(connection, eligible_everything, monkeypatch) -> None:
    """Feedback of 10 October: once answered, Today's card is replaced the next time it opens."""
    from vademecum.storage import flashcards

    weak, _ = _page(connection, "cirrhosis", specialty="gastroenterology")
    _flag(connection, "cirrhosis")
    flashcards.insert_cards(connection, entry_id=weak.id, topic=weak.topic, entry_version=weak.version,
                            drafts=[{"front": f"cirrhosis front {i}?", "back": "b", "point_ids": [], "hold_reason": ""} for i in range(2)])
    monkeypatch.setattr(flashcards, "eligible_card_ids", lambda c: [r["id"] for r in c.execute("SELECT id FROM flashcards")])
    first = learner.recall_prompt(connection)
    flashcards.record_review(connection, first["card"]["id"], "good")
    second = learner.recall_prompt(connection)
    assert second["card"] is not None and second["card"]["id"] != first["card"]["id"]
