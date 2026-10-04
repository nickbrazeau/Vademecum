"""The encyclopedia and the board bank (ADR 0023): pages compiled from a
topic's points with every paragraph cited, board questions that cite the
page, a cycle like the Tutor's, local grading, and the page of the day."""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from test_end_to_end import ABSTRACT, ASSESSMENT, EVIDENCE, LECTURE, SYNTHESIS, upload
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.encyclopedia import check_board, check_entry
from vademecum.model.schemas import BOARD_SCHEMA, ENTRY_SCHEMA
from vademecum.storage import encyclopedia as store
from vademecum.storage.maintenance import EXPORTED_TABLES
from vademecum.storage.sync import DOMI_OWNED, SYNCED_TABLES

HANDLES = {"p1": "lp_one", "p2": "lp_two"}


def test_a_page_keeps_only_paragraphs_whose_handles_map() -> None:
    page = check_entry(
        {
            "title": "Lactate in sepsis",
            "summary": "What lactate tells you.",
            "specialty": "infectious-disease",
            "sections": [
                {"heading": "Thresholds", "paragraphs": [{"text": "Above 2 mmol/L is abnormal.", "points": ["p1", "p1", "p9"]}]},
                {"heading": "Unsourced", "paragraphs": [{"text": "Invented sentence.", "points": ["p9"]}, {"text": "", "points": ["p1"]}]},
                {"heading": "", "paragraphs": [{"text": "No heading.", "points": ["p2"]}]},
            ],
        },
        handles=HANDLES,
        specialty_ids={"infectious-disease"},
    )
    assert page is not None
    assert [s["heading"] for s in page["sections"]] == ["Thresholds"]
    assert page["sections"][0]["paragraphs"] == [{"text": "Above 2 mmol/L is abnormal.", "point_ids": ["lp_one"], "record_ids": []}]
    assert page["cited"] == ["lp_one"] and page["specialty_id"] == "infectious-disease"
    assert check_entry({"title": "x", "summary": "", "specialty": "nope", "sections": [{"heading": "h", "paragraphs": [{"text": "t", "points": ["p9"]}]}]}, handles=HANDLES, specialty_ids=set()) is None


def test_a_paragraph_may_rest_on_literature_but_a_page_never_on_literature_alone() -> None:
    records = {"r1": "rec_one"}
    page = check_entry(
        {
            "title": "T", "summary": "", "specialty": "",
            "sections": [
                {"heading": "Thresholds", "paragraphs": [{"text": "From the points.", "points": ["p1"], "records": []}]},
                {"heading": "In the literature", "paragraphs": [{"text": "A 2025 trial found...", "points": [], "records": ["r1", "r9"]}]},
            ],
        },
        handles=HANDLES, specialty_ids=set(), record_handles=records,
    )
    assert page is not None
    assert page["sections"][1]["paragraphs"][0] == {"text": "A 2025 trial found...", "point_ids": [], "record_ids": ["rec_one"]}
    assert page["cited_records"] == ["rec_one"]
    only_literature = check_entry(
        {"title": "T", "summary": "", "specialty": "", "sections": [{"heading": "In the literature", "paragraphs": [{"text": "x", "points": [], "records": ["r1"]}]}]},
        handles=HANDLES, specialty_ids=set(), record_handles=records,
    )
    assert only_literature is None, "a page rests on the owner's points first"


def test_board_drafts_are_checked_for_shape_and_held_without_a_citation() -> None:
    good = {
        "stem": "A 54-year-old man presents with fever and hypotension. Lactate is 4.2 mmol/L. Which of the following is the most appropriate next step?",
        "options": ["Give a 30 mL/kg crystalloid bolus", "Start vasopressors first", "Give antibiotics after cultures only", "Observe", "Give a diuretic"],
        "answer": "A",
        "explanation": "A is right because...; B is wrong because...",
        "objective": "Recognise the lactate threshold.",
        "points": ["p1"],
    }
    drafts = check_board(
        {
            "questions": [
                good,
                {**good, "stem": "According to the text, which lactate level matters?", "points": ["p2"]},
                {**good, "points": ["p9"]},
                {**good, "options": ["Same", "Same", "Other", "Another", "More"]},
                {**good, "options": ["A", "B", "C", "D", "None of the above"]},
                {**good, "answer": "F"},
            ]
        },
        handles=HANDLES,
    )
    assert [d["hold_reason"] for d in drafts] == [
        "",
        "The stem leans on 'the text' instead of standing on its own.",
        "The question cites none of the page's points, so its answer cannot be traced to a source.",
        "An option is 'all' or 'none of the above'.",
    ], "duplicates and a bad key are dropped; the rest are held with a reason"
    assert drafts[0]["answer_index"] == 0 and drafts[0]["point_ids"] == ["lp_one"]


class CompileTurns(ScriptedTurns):
    """The build's scripted runner, taught the page and the board exchanges."""

    def __init__(self, *, pages: list[dict], boards: list[dict], **kwargs) -> None:
        super().__init__(**kwargs)
        self.pages = list(pages)
        self.boards = list(boards)
        self.prompts: list[str] = []

    def __call__(self):
        outer = self
        base = super().__call__()

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                from vademecum.appserver.turns import TurnResult

                if output_schema is ENTRY_SCHEMA:
                    outer.prompts.append(prompt)
                    return TurnResult(payload=outer.pages.pop(0) if len(outer.pages) > 1 else outer.pages[0], raw_chars=10, turn_id="t", duration_ms=1.0)
                if output_schema is BOARD_SCHEMA:
                    outer.prompts.append(prompt)
                    return TurnResult(payload=outer.boards.pop(0) if len(outer.boards) > 1 else outer.boards[0], raw_chars=10, turn_id="t", duration_ms=1.0)
                return await base.run(
                    instructions=instructions, developer_instructions=developer_instructions, prompt=prompt, output_schema=output_schema, max_output_chars=max_output_chars
                )

        return Runner()


def _page_for(handles: list[str]) -> dict:
    return {
        "title": "Lactate in sepsis",
        "summary": "Lactate above 2 mmol/L marks hypoperfusion.",
        "specialty": "infectious-disease",
        "sections": [{"heading": "Thresholds", "paragraphs": [{"text": "A lactate above 2 mmol/L is abnormal in sepsis.", "points": handles}]}],
    }


def _board_for(handles: list[str]) -> dict:
    return {
        "questions": [
            {
                "stem": "A 60-year-old woman has a lactate of 3.1 mmol/L with suspected sepsis. Which of the following is the most accurate interpretation?",
                "options": ["Hypoperfusion is likely", "The value is normal", "It reflects liver failure only", "It excludes sepsis", "It is a laboratory error"],
                "answer": "A",
                "explanation": "A: above 2 mmol/L marks hypoperfusion. B-E contradict the page.",
                "objective": "Interpret lactate in sepsis.",
                "points": handles,
            }
        ]
    }


def _wait(client: TestClient, predicate, *, timeout: float = 15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/encyclopedia").json()
        if predicate(payload):
            return payload
        time.sleep(0.05)
    raise AssertionError("the compile did not get there in time")


def test_a_build_feeds_a_page_which_feeds_board_questions(tmp_path: Path) -> None:
    turns = CompileTurns(pages=[_page_for(["p1"])], boards=[_board_for(["p1"])], synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT])
    provider = FakeProvider(articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001
            workspace.turn_factory = turns

        pile = c.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        assert upload(c, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
        preview = c.get(f"/api/piles/{pile['id']}/build/preview").json()
        c.post(f"/api/piles/{pile['id']}/build", json={"batch_id": preview["batch_id"], "selection_hash": preview["selection_hash"]})
        for _ in range(200):
            if c.get(f"/api/piles/{pile['id']}/build/status").json()["run"]["status"] in ("succeeded", "failed"):
                break
            time.sleep(0.05)
        points = c.get("/api/points").json()
        assert points, "the build made points"

        before = c.get("/api/encyclopedia").json()
        assert before["entries"] == [] and before["counts"]["stale"] >= 1 and before["can_compile"] is True
        assert "once, to the Mac's own model connection" in before["disclosure"]
        assert {"id": "nephrology", "name": "Nephrology"} in before["specialties"], "the subjects pages are shelved under"
        assert c.get("/api/today").json()["page"] is None

        started = c.post("/api/encyclopedia/compile")
        assert started.status_code == 202, started.text
        done = _wait(c, lambda p: p["counts"]["entries"] >= 1 and p["counts"]["questions_eligible"] >= 1 and not p["running"])
        entry = done["entries"][0]
        assert entry["title"] == "Lactate in sepsis" and entry["point_count"] >= 1 and entry["question_count"] >= 1
        assert done["counts"]["stale"] == 0, "a compiled page is current for its points"

        full = c.get(f"/api/encyclopedia/{entry['id']}").json()
        assert full["sections"][0]["paragraphs"][0]["point_ids"], "every paragraph names its points"
        assert full["citations"][0]["sources"], "and the points resolve to sources"
        assert [r["pmid"] for r in full["literature"]] == ["30012345"], "one public search per page: its records are the review"
        assert full["literature"][0]["cited"] is False and full["literature_checked_at"]
        assert provider.queries[-1] == entry["topic"], "the search is the topic's own words, nothing of the page"
        assert "RECENT LITERATURE" in turns.prompts[0] and "[r1] Lactate targets" in turns.prompts[0]
        assert "LEARNING POINTS, each with its id" in turns.prompts[0]
        assert "THE PAGE:" in turns.prompts[1] and "OTHER CONTEXT" in turns.prompts[1]

        today = c.get("/api/today").json()
        assert today["page"]["id"] == entry["id"] and today["page"]["citations"]
        assert today["encyclopedia"]["entries"] == 1
        another = c.get("/api/encyclopedia/page", params={"random": "true"}).json()
        assert another["page"]["id"] == entry["id"], "one page: another page is the same page"

        nxt = c.get("/api/tutor/board/next").json()
        question = nxt["question"]
        assert question is not None and len(question["options"]) == 5 and "answer_index" not in question and "explanation" not in question
        assert nxt["cycle"]["total"] == 1 and nxt["cycle"]["remaining"] == 1
        assert c.get("/api/tutor/board/next").json()["question"]["id"] == question["id"], "idempotent until advance"

        wrong = c.post("/api/tutor/board/answer", json={"question_id": question["id"], "choice": 1}).json()
        assert wrong["attempt"]["correct"] is False and wrong["question"]["answer_letter"] == "A"
        assert "hypoperfusion" in wrong["question"]["explanation"] and wrong["citations"][0]["sources"]
        right = c.post("/api/tutor/board/answer", json={"question_id": question["id"], "choice": 0}).json()
        assert right["attempt"]["correct"] is True
        overview = c.get("/api/tutor/board").json()
        assert overview["answered_total"] == 2 and overview["answered_correct"] == 1 and overview["eligible"] == 1

        advanced = c.post("/api/tutor/board/advance", json={"question_id": question["id"]}).json()
        assert advanced["cycle"]["cycle_number"] == 2, "one question: advancing starts the next pass"
        history = c.get("/api/tutor/board/history").json()
        assert len(history) == 2 and history[0]["topic"]

        # Holding a cited point takes the question with it, at once.
        c.post(f"/api/piles/{pile['id']}/recheck")
        again = c.get("/api/encyclopedia").json()
        assert again["counts"]["entries"] == 1


def _dissection(client: TestClient):
    return client.get("/api/encyclopedia/dissection").json()


def test_the_dissection_agent_works_a_pile_through_to_complete_and_survives_a_failure(tmp_path: Path) -> None:
    turns = CompileTurns(pages=[_page_for(["p1"])], boards=[_board_for(["p1"])], synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT])
    provider = FakeProvider(articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001
            workspace.turn_factory = turns
        dissector = app.state.dissector
        dissector._idle_seconds = 0.2  # noqa: SLF001 - the seams for a test that cannot wait ten minutes
        dissector._backoff_base = 0.1  # noqa: SLF001
        dissector._poll_seconds = 0.05  # noqa: SLF001

        before = _dissection(c)
        assert before["status"] == "idle" and before["can_run"] is True and "standing consent" in before["disclosure"]
        assert c.post("/api/encyclopedia/dissection", json={"pile_id": "pil_nope"}).status_code == 404

        pile = c.post("/api/piles", json={"title": "The book", "tier": "high"}).json()
        assert upload(c, pile["id"], "chapter.txt", LECTURE.encode()).status_code == 201
        started = c.post("/api/encyclopedia/dissection", json={"pile_id": "all"})
        assert started.status_code == 202, started.text
        assert started.json()["status"] == "running" and started.json()["consent_at"]
        assert started.json()["pile_title"] == "every pile"

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = _dissection(c)
            if state.get("phase") == "complete":
                break
            time.sleep(0.05)
        assert state["phase"] == "complete", state
        assert state["batches_done"] >= 1 and state["points_built"] >= 1 and state["pages_compiled"] >= 1
        assert state["coverage"]["complete"] is True, "the pile was built to the end"
        assert state["encyclopedia"]["entries"] >= 1 and state["encyclopedia"]["stale"] == 0
        assert c.get("/api/tutor/board/next").json()["question"] is not None

        # A file added later is taken up without being asked: the agent is still watching.
        assert upload(c, pile["id"], "chapter-two.txt", (LECTURE + "\n\nNoradrenaline is titrated to a mean arterial pressure of 65 mm Hg.").encode()).status_code == 201
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = _dissection(c)
            if state.get("batches_done", 0) >= 2 and state.get("phase") == "complete":
                break
            time.sleep(0.05)
        assert state["batches_done"] >= 2, state

        stopped = c.post("/api/encyclopedia/dissection/stop").json()
        assert stopped["status"] == "stopped" and stopped["running"] is False


def test_the_page_of_the_day_is_stable_within_a_day(connection) -> None:
    for index in range(3):
        store.upsert_entry(
            connection, topic=f"topic {index}", title=f"Page {index}", specialty_id=None, summary="s", sections=[], point_ids=[], points_hash_value="h"
        )
    first = store.page_of_the_day(connection, on_day=date(2026, 10, 3))
    assert first is not None and first.id == store.page_of_the_day(connection, on_day=date(2026, 10, 3)).id
    other = store.random_page(connection, not_id=first.id)
    assert other is not None and other.id != first.id


def test_the_page_of_the_day_is_a_fuller_page_when_there_is_one(connection) -> None:
    for index in range(6):
        store.upsert_entry(connection, topic=f"stub {index}", title=f"Stub {index}", specialty_id=None, summary="s", sections=[], point_ids=["lp_a"], points_hash_value="h")
    full = store.upsert_entry(connection, topic="sepsis", title="Sepsis", specialty_id=None, summary="s", sections=[], point_ids=["lp_a", "lp_b"], points_hash_value="h")
    for day in range(1, 8):
        assert store.page_of_the_day(connection, on_day=date(2026, 10, day)).id == full.id
    # "Another page" has no other fuller page to offer, so it offers a different page rather than the same one.
    assert store.random_page(connection, not_id=full.id).id != full.id
    assert store.random_page(connection).id == full.id


def test_a_page_never_says_it_is_not_an_endorsement() -> None:
    """The citation says whose statement it is; a clause saying so again is dropped, and only that clause."""
    page = check_entry(
        {
            "title": "Thrombolysis",
            "summary": "Thrombolysis is described for arrest from pulmonary embolism. It is a description of that protocol, not an endorsement.",
            "specialty": "",
            "sections": [
                {
                    "heading": "Protocol",
                    "paragraphs": [
                        {"text": "CPR continues for at least 15 minutes after lysis; this is a protocol description, not an endorsement.", "points": ["p1"]},
                        {"text": "This is a description of the source's protocol, not an endorsement.", "points": ["p1"]},
                        {"text": "Regulatory approval is not an endorsement of off-label use.", "points": ["p2"]},
                    ],
                }
            ],
        },
        handles=HANDLES,
        specialty_ids=set(),
    )
    assert page is not None
    assert page["summary"] == "Thrombolysis is described for arrest from pulmonary embolism."
    assert [p["text"] for p in page["sections"][0]["paragraphs"]] == [
        "CPR continues for at least 15 minutes after lysis.",
        "Regulatory approval is not an endorsement of off-label use.",
    ]
    drafts = check_board(
        {"questions": [{"stem": "Which agent is preferred?", "options": ["a", "b", "c", "d", "e"], "answer": "A", "explanation": "A matches. This describes the source protocol, not an endorsement of its clinical use.", "points": ["p1"]}]},
        handles=HANDLES,
    )
    assert drafts[0]["explanation"] == "A matches."


def test_prose_kept_before_the_rule_is_tidied_once(connection) -> None:
    said = "This is a description of the source's protocol, not an endorsement."
    entry = store.upsert_entry(
        connection,
        topic="t",
        title="T",
        specialty_id=None,
        summary=f"Tenecteplase is preferred. {said}",
        sections=[{"heading": "H", "paragraphs": [{"text": f"Alteplase is the alternative. {said}", "point_ids": ["lp_a"]}, {"text": said, "point_ids": ["lp_a"]}]}],
        point_ids=["lp_a"],
        points_hash_value="1",
    )
    store.insert_questions(
        connection, entry, [{"stem": "Stem?", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": f"A matches. {said}", "point_ids": ["lp_a"], "hold_reason": ""}]
    )
    assert store.drop_stored_disclaimers(connection) == 3
    assert store.drop_stored_disclaimers(connection) == 0, "nothing left to change"
    tidied = store.get_entry(connection, entry.id)
    assert tidied.summary == "Tenecteplase is preferred." and tidied.version == entry.version
    assert [p["text"] for p in tidied.sections[0]["paragraphs"]] == ["Alteplase is the alternative."]
    assert store.questions_for_entry(connection, entry.id)[0].explanation == "A matches."


def test_a_rewritten_page_holds_questions_whose_points_left(connection) -> None:
    entry = store.upsert_entry(connection, topic="t", title="T", specialty_id=None, summary="", sections=[], point_ids=["lp_a", "lp_b"], points_hash_value="1")
    store.insert_questions(
        connection,
        entry,
        [
            {"stem": "Stem one?", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": "x", "point_ids": ["lp_a"], "hold_reason": ""},
            {"stem": "Stem two?", "options": ["a", "b", "c", "d", "e"], "answer_index": 1, "explanation": "x", "point_ids": ["lp_b"], "hold_reason": ""},
            {"stem": "Stem two?", "options": ["a", "b", "c", "d", "e"], "answer_index": 1, "explanation": "x", "point_ids": ["lp_b"], "hold_reason": ""},
        ],
    )
    assert len(store.questions_for_entry(connection, entry.id)) == 2, "the same stem is not written twice"
    rewritten = store.upsert_entry(connection, topic="t", title="T", specialty_id=None, summary="", sections=[], point_ids=["lp_a"], points_hash_value="2")
    assert rewritten.version == 2
    statuses = {q.stem: q.status for q in store.questions_for_entry(connection, entry.id)}
    assert statuses == {"Stem one?": "eligible", "Stem two?": "held"}


def test_the_new_tables_sync_and_export() -> None:
    for table in ("encyclopedia_entries", "board_questions", "board_cycle_entries", "board_attempts"):
        assert table in SYNCED_TABLES and table in EXPORTED_TABLES
    assert {"encyclopedia_entries", "board_questions"} <= DOMI_OWNED
    assert "board_attempts" not in DOMI_OWNED, "answers are given on either node"
