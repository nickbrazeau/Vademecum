"""The owner's feedback of 4 October (ADR 0026): days in a row and the scorecard,
strong and weak with reasons, pages mirrored to Markdown both ways, new cases
on Today, and the Improvement Map saying where flags are filed."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.storage import activity, page_files, strengths
from vademecum.storage import encyclopedia as pages
from vademecum.storage.common import new_id


def _at(day: date) -> str:
    moment = datetime(day.year, day.month, day.day, 12, 0, tzinfo=timezone.utc).astimezone()
    local_noon = datetime(day.year, day.month, day.day, 12, 0).astimezone()
    return local_noon.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else ""


def test_days_in_a_row_count_what_happened(connection) -> None:
    today = datetime.now().astimezone().date()
    for offset in (0, 1, 2, 4, 5, 6, 7):
        connection.execute(
            "INSERT INTO review_events (id, kind, ref_id, created_at) VALUES (?, 'page', 'p', ?)",
            (new_id("rev"), _at(today - timedelta(days=offset))),
        )
    connection.commit()
    board = activity.dashboard(connection, today=today)
    assert board["days_in_a_row"] == 3 and board["longest_run"] == 4 and board["reviewed_today_already"] is True
    assert board["today"]["page"] == 1 and board["week_total"] == 6 and len(board["history"]) == 14
    yesterday = activity.dashboard(connection, today=today + timedelta(days=1))
    assert yesterday["days_in_a_row"] == 3, "a day not yet reviewed does not break the run until it is over"
    assert activity.dashboard(connection, today=today + timedelta(days=2))["days_in_a_row"] == 0


def _page_with_question(connection, topic: str):
    entry = pages.upsert_entry(connection, topic=topic, title=topic.title(), specialty_id="infectious-disease", summary="s", sections=[], point_ids=[], points_hash_value=topic)
    pages.insert_questions(connection, entry, [{"stem": f"A {topic} vignette?", "options": ["a", "b", "c", "d", "e"], "answer_index": 0, "explanation": "x", "point_ids": [], "hold_reason": ""}])
    return entry, pages.questions_for_entry(connection, entry.id)[0]


def test_strengths_say_weak_and_strong_and_why_down_to_the_question(connection) -> None:
    _, weak_q = _page_with_question(connection, "blastomycosis")
    _, strong_q = _page_with_question(connection, "candidemia")
    for _ in range(4):
        pages.record_attempt(connection, weak_q, 2)
        pages.record_attempt(connection, strong_q, 0)
    connection.execute(
        "INSERT INTO knowledge_gap_flags (id, text, topic, pile_id, status, created_at, updated_at, addressed_at)"
        " VALUES ('kgf_1', 'From a Socratic session: dimorphic fungi', 'blastomycosis', NULL, 'open', '2026-10-04T00:00:00Z', '2026-10-04T00:00:00Z', NULL)"
    )
    connection.commit()
    found = strengths.strengths(connection)
    group = next(g for g in found["specialties"] if g["id"] == "infectious-disease")
    by_topic = {t["topic"]: t for t in group["topics"]}
    assert by_topic["blastomycosis"]["label"] == "weak" and by_topic["candidemia"]["label"] == "strong"
    assert "Board questions: 0 of 4 right." in by_topic["blastomycosis"]["reasons"]
    assert by_topic["blastomycosis"]["evidence"]["missed_questions"] == ["A blastomycosis vignette?"]
    assert by_topic["blastomycosis"]["evidence"]["flags"] == ["From a Socratic session: dimorphic fungi"]
    assert group["topics"][0]["topic"] == "blastomycosis", "weakest first"

    card = activity.scorecard(connection)
    assert card["board"]["answered"] == 8 and card["board"]["correct"] == 4
    assert card["weakest_topics"][0]["topic"] == "blastomycosis" and card["strongest_topics"][0]["topic"] == "candidemia"


def test_pages_mirror_to_markdown_and_a_file_edit_comes_back(connection, tmp_path: Path) -> None:
    entry = pages.upsert_entry(
        connection, topic="lactate", title="Lactate in sepsis", specialty_id="infectious-disease", summary="Above 2 mmol/L.",
        sections=[{"heading": "Thresholds", "paragraphs": [{"text": "Lactate above 2 mmol/L is abnormal.", "point_ids": []}]}],
        point_ids=[], points_hash_value="h",
    )
    folder = tmp_path / "Vademecum"
    assert page_files.sync_folder(connection, folder) == {"written": 1, "read": 0}
    path = folder / "encyclopedia" / "Infectious Disease" / "Lactate in sepsis.md"
    text = path.read_text()
    assert text.startswith(f"---\nvademecum: {entry.id}\n") and "# Lactate in sepsis" in text and "## Thresholds" in text
    assert page_files.sync_folder(connection, folder) == {"written": 0, "read": 0}, "nothing changed, nothing written"

    path.write_text(text.replace("is abnormal.", "is abnormal; my note: recheck at 2 h."))
    assert page_files.sync_folder(connection, folder) == {"written": 0, "read": 1}
    edited = pages.get_entry(connection, entry.id)
    assert "my note: recheck at 2 h." in edited.body_md and edited.edited_at
    assert edited.as_dict()["edited"] is True

    page_files.set_edit(connection, entry.id, "")
    assert page_files.sync_folder(connection, folder)["written"] == 1
    assert "my note" not in path.read_text(), "reverting the edit puts the compiled page back in the file"


def test_the_api_edits_a_page_marks_reviews_lists_new_cases_and_says_where_flags_are_filed(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, model_provider="host")
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect
        from vademecum.storage import cases

        connection = connect(app.state.database_path)
        try:
            entry = pages.upsert_entry(connection, topic="t", title="T", specialty_id=None, summary="", sections=[], point_ids=[], points_hash_value="h")
            cases.record_items(connection, [{"series": "cps", "external_id": "1", "title": "Episode 1", "url": "https://clinicalproblemsolving.com/1/", "published_on": date.today().isoformat(), "text": "notes"}])
            case_id = connection.execute("SELECT id FROM case_entries").fetchone()["id"]
            cases.set_synthesised(connection, case_id, one_liner="A case.", points=[], think_first=["Why?"], specialty_id=None)
        finally:
            connection.close()

        edited = c.put(f"/api/encyclopedia/{entry.id}", json={"body_md": "# T\n\nMy own words."}).json()
        assert edited["edited"] is True and edited["markdown"].startswith("# T") and "My own words." in edited["body_md"]
        reverted = c.delete(f"/api/encyclopedia/{entry.id}/edit").json()
        assert reverted["edited"] is False

        board = c.post("/api/activity/page", json={"entry_id": entry.id}).json()
        assert board["today"]["page"] == 1 and board["days_in_a_row"] == 1
        assert c.get("/api/today").json()["dashboard"]["today_total"] == 1
        assert c.get("/api/tutor/scorecard").json()["dashboard"]["today_total"] == 1

        today = c.get("/api/today").json()
        assert [case["title"] for case in today["new_cases"]] == ["Episode 1"]
        assert c.post(f"/api/cases/{case_id}/acknowledge").json()["acknowledged"] is True
        assert c.get("/api/today").json()["new_cases"] == []

        mapped = c.get("/api/improvement-map").json()
        assert mapped["can_file_flags"] is False and "filed under topics by your Mac" in mapped["filing_note"]
        assert c.get("/api/improvement-map/strengths").json() == {"specialties": [], "topic_count": 0}


def test_figures_from_the_owners_sources_are_placed_by_page_and_never_drawn(connection, tmp_path: Path) -> None:
    from vademecum.storage import figures

    now = "2026-10-04T00:00:00Z"
    connection.execute("INSERT INTO piles (id, title, tier, description, created_at, updated_at) VALUES ('pil_1', 'Book', 'high', '', ?, ?)", (now, now))
    connection.execute(
        "INSERT INTO sources (id, pile_id, display_name, media_type, byte_size, sha256, stored_name, confidence, status, created_at, updated_at)"
        " VALUES ('src_1', 'pil_1', 'White Book.pdf', 'application/pdf', 1, 'd', 'd.pdf', 'high', 'extracted', ?, ?)",
        (now, now),
    )
    rows = [
        ("img_big", "page 4", 900, 600),
        ("img_icon", "page 4", 60, 60),
        ("img_banner", "page 4", 1200, 120),
        ("img_other", "page 9", 800, 600),
    ]
    for ordinal, (image_id, locator, width, height) in enumerate(rows):
        connection.execute(
            "INSERT INTO source_images (id, source_id, ordinal, unit_index, locator, origin, media_type, byte_size, width, height, sha256, stored_name, created_at)"
            " VALUES (?, 'src_1', ?, 0, ?, 'embedded', 'image/png', ?, ?, ?, ?, ?, ?)",
            (image_id, ordinal, locator, width * height, width, height, image_id, f"{image_id}.png", now),
        )
    connection.execute(
        "INSERT INTO learning_points (id, pile_id, generation_id, claim, detail, support, evidence_grade, review_state, held, hold_reason, content_hash, created_at, updated_at)"
        " VALUES ('lp_1', 'pil_1', NULL, 'c', '', 'source_supported', 'none', 'machine_reviewed', 0, '', 'h', ?, ?)",
        (now, now),
    )
    connection.execute(
        "INSERT INTO learning_point_sources (id, learning_point_id, source_id, segment_id, locator, quote, created_at)"
        " VALUES ('lps_1', 'lp_1', 'src_1', NULL, 'page 4 (characters 10-200)', 'q', ?)",
        (now,),
    )
    connection.commit()
    assert figures.unit_of("page 82 (characters 399–2767)") == "page 82"
    placed = figures.place(connection, [{"heading": "H", "paragraphs": [{"text": "t", "point_ids": ["lp_1"]}, {"text": "u", "point_ids": ["lp_1"]}]}])
    first, second = placed[0]["paragraphs"]
    assert [f["image_id"] for f in first["figures"]] == ["img_big"], "the picture on the cited page, not icons or banners, not another page"
    assert first["figures"][0]["source"] == "White Book.pdf" and first["figures"][0]["ext"] == ".png"
    assert second["figures"] == [], "a figure is placed once per page"

    entry = pages.upsert_entry(connection, topic="t", title="T", specialty_id=None, summary="", sections=[{"heading": "H", "paragraphs": [{"text": "t", "point_ids": ["lp_1"]}]}], point_ids=["lp_1"], points_hash_value="h")
    assert figures.refresh(connection) == 1 and figures.refresh(connection) == 0
    assert pages.get_entry(connection, entry.id).sections[0]["paragraphs"][0]["figures"][0]["image_id"] == "img_big"

    images = tmp_path / "images"
    images.mkdir()
    (images / "img_big.png").write_bytes(b"\x89PNG")
    page_files.sync_folder(connection, tmp_path / "Vademecum", images)
    text = next((tmp_path / "Vademecum" / "encyclopedia").rglob("T.md")).read_text()
    assert "![White Book.pdf, page 4](../_figures/img_big.png)" in text
    assert (tmp_path / "Vademecum" / "encyclopedia" / "_figures" / "img_big.png").read_bytes() == b"\x89PNG"


def test_the_owners_daily_goal_says_when_today_is_enough(connection) -> None:
    from vademecum.storage import preferences

    today = datetime.now().astimezone().date()
    for _ in range(3):
        connection.execute("INSERT INTO review_events (id, kind, ref_id, created_at) VALUES (?, 'page', 'p', ?)", (new_id("rev"), _at(today)))
    connection.commit()
    board = activity.dashboard(connection, today=today)
    assert board["daily_goal"] == 20 and board["goal_met"] is False and board["remaining_today"] == 17
    preferences.set_daily_goal(connection, 3)
    board = activity.dashboard(connection, today=today)
    assert board["goal_met"] is True and board["remaining_today"] == 0
    assert preferences.set_daily_goal(connection, 0)["daily_goal"] == 1, "bounded"
