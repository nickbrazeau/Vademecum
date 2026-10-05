"""Flashcards and preferences (ADR 0024): cards written from a page and cited,
drawn with the Improvement Map's weight behind them, reviewed locally; and the
tabs the owner chooses to see, with Today and Settings always there."""

from __future__ import annotations

import random
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.encyclopedia import check_cards
from vademecum.storage import encyclopedia as pages
from vademecum.storage import flashcards as store
from vademecum.storage import preferences
from vademecum.storage.maintenance import EXPORTED_TABLES
from vademecum.storage.sync import DOMI_OWNED, SYNCED_TABLES

HANDLES = {"p1": "lp_one"}


def test_cards_are_checked_for_a_front_a_back_and_a_citation() -> None:
    drafts = check_cards(
        {
            "cards": [
                {"front": "Lactate above ___ mmol/L marks hypoperfusion in sepsis.", "back": "2 mmol/L.", "points": ["p1"]},
                {"front": "According to the text, what is the threshold?", "back": "2.", "points": ["p1"]},
                {"front": "What is the threshold?", "back": "2.", "points": ["p9"]},
                {"front": "", "back": "2.", "points": ["p1"]},
            ]
        },
        handles=HANDLES,
    )
    assert [d["hold_reason"] for d in drafts] == [
        "",
        "The front leans on 'the text' instead of standing on its own.",
        "The card cites none of the page's points, so its answer cannot be traced to a source.",
    ]


def _page(connection, topic: str, point_ids: list[str]):
    return pages.upsert_entry(
        connection, topic=topic, title=topic.title(), specialty_id=None, summary="", sections=[], point_ids=point_ids, points_hash_value=topic
    )


def _real_point(client: TestClient) -> str:
    """A learning point the gates accept: unheld, machine reviewed. Made the honest way, through a pile."""
    from test_end_to_end import LECTURE, upload

    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    assert upload(client, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
    return pile["id"]


def test_the_draw_leans_towards_the_map_and_a_review_draws_the_next(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect
        from vademecum.storage.learning import DraftCitation, DraftPoint, settle_point, upsert_point

        assert c.get("/api/flashcards/next").json()["card"] is None
        connection = connect(app.state.database_path)
        try:
            pile_id = _real_point(c)
            source = c.get(f"/api/piles/{pile_id}/sources").json()[0]
            segment = connection.execute("SELECT id, text FROM source_segments WHERE source_id = ? ORDER BY ordinal LIMIT 1", (source["id"],)).fetchone()
            ids = []
            for topic in ("sepsis", "pneumonia"):
                point_id, _ = upsert_point(
                    connection,
                    pile_id=pile_id,
                    generation_id=None,
                    draft=DraftPoint(claim=f"A claim about {topic}.", detail="", topics=(topic,), citations=(DraftCitation(source_id=source["id"], segment_id=segment["id"], locator="Paragraph 1", quote=segment["text"][:60]),)),
                )
                settled = settle_point(connection, point_id)
                assert settled.held is False, settled.hold_reason
                ids.append(point_id)
            flagged = _page(connection, "sepsis", [ids[0]])
            quiet = _page(connection, "pneumonia", [ids[1]])
            store.insert_cards(connection, entry_id=flagged.id, topic="sepsis", entry_version=1, drafts=[{"front": "Sepsis front?", "back": "Back.", "point_ids": [ids[0]]}])
            store.insert_cards(connection, entry_id=quiet.id, topic="pneumonia", entry_version=1, drafts=[{"front": "Pneumonia front?", "back": "Back.", "point_ids": [ids[1]]}])
            assert len(store.eligible_card_ids(connection)) == 2
            c.post("/api/flags", json={"text": "I keep forgetting sepsis thresholds", "topic": "sepsis"})

            draws = [store.next_card(connection, rng=random.Random(seed))["card"]["topic"] for seed in range(40)]
            assert draws.count("sepsis") > draws.count("pneumonia"), "the flagged topic comes up more"
            drawn = next(d for seed in range(40) if (d := store.next_card(connection, rng=random.Random(seed)))["card"]["topic"] == "sepsis")
            assert "You flagged this topic as a gap." in drawn["reasons"] and drawn["citations"][0]["sources"]
        finally:
            connection.close()

        first = c.get("/api/flashcards/next").json()
        assert first["card"]["front"].endswith("front?") and "back" in first["card"] and first["deck"] == 2
        reviewed = c.post("/api/flashcards/review", json={"card_id": first["card"]["id"], "rating": "good"}).json()
        assert reviewed["review"]["rating"] == "good" and reviewed["next"]["card"]["id"] != first["card"]["id"]
        overview = c.get("/api/flashcards").json()
        assert overview["eligible"] == 2 and overview["reviews_total"] == 1 and overview["improvement"]["flagged_topics"] == 1
        assert c.post("/api/flashcards/review", json={"card_id": first["card"]["id"], "rating": "meh"}).status_code == 422


def test_a_rewritten_page_holds_cards_whose_points_left(connection) -> None:
    entry = _page(connection, "t", ["lp_a", "lp_b"])
    store.insert_cards(connection, entry_id=entry.id, topic="t", entry_version=1, drafts=[
        {"front": "One?", "back": "a", "point_ids": ["lp_a"]},
        {"front": "Two?", "back": "b", "point_ids": ["lp_b"]},
        {"front": "Two?", "back": "b", "point_ids": ["lp_b"]},
    ])
    assert len(store.cards_for_entry(connection, entry.id)) == 2
    _page(connection, "t", ["lp_a"])
    assert {card.front: card.status for card in store.cards_for_entry(connection, entry.id)} == {"One?": "eligible", "Two?": "held"}


def test_preferences_keep_today_and_settings_and_follow_the_catalogue(connection) -> None:
    default = preferences.get_preferences(connection)
    assert default["visible_tabs"][0] == "today" and default["visible_tabs"][-1] == "settings"
    chosen = preferences.set_visible_tabs(connection, ["tutor", "bogus", "podcasts"])
    assert chosen["visible_tabs"] == ["today", "tutor", "podcasts", "settings"]
    assert preferences.set_visible_tabs(connection, [])["visible_tabs"] == ["today", "settings"]


def test_a_tab_added_after_the_owner_chose_is_shown(connection) -> None:
    import json

    connection.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES ('preferences', ?, '2026-10-01T00:00:00Z')",
        (json.dumps({"visible_tabs": ["today", "tutor", "settings"]}),),
    )
    connection.commit()
    assert preferences.get_preferences(connection)["visible_tabs"] == ["today", "tutor", "construction", "settings"]


def test_preferences_over_the_api(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        before = c.get("/api/preferences").json()
        assert "flashcards" in before["visible_tabs"] and any(tab["name"] == "settings" and tab["fixed"] for tab in before["tabs"])
        after = c.put("/api/preferences", json={"visible_tabs": ["encyclopedia", "flashcards"]}).json()
        assert after["visible_tabs"] == ["today", "flashcards", "encyclopedia", "settings"]
        assert c.get("/api/preferences").json()["visible_tabs"] == after["visible_tabs"]


def test_the_new_tables_sync_and_export() -> None:
    assert "flashcards" in SYNCED_TABLES and "flashcards" in DOMI_OWNED and "flashcards" in EXPORTED_TABLES
    assert "flashcard_reviews" in SYNCED_TABLES and "flashcard_reviews" not in DOMI_OWNED and "flashcard_reviews" in EXPORTED_TABLES


def test_the_owner_orders_the_tabs_between_today_and_settings(connection) -> None:
    moved = preferences.set_visible_tabs(connection, ["tutor", "map", "sources"], ["settings", "map", "today", "tutor", "bogus"])
    assert moved["order"][0] == "today" and moved["order"][-1] == "settings"
    assert moved["order"][1:3] == ["map", "tutor"], "the owner's order first, then the rest in catalogue order"
    assert moved["visible_tabs"] == ["today", "map", "tutor", "sources", "settings"]
    assert [tab["name"] for tab in moved["tabs"]] == moved["order"]
    again = preferences.set_visible_tabs(connection, ["tutor"])
    assert again["order"] == moved["order"], "choosing tabs again keeps the order"


def test_the_listening_speed_is_a_shared_preference(tmp_path: Path) -> None:
    """ADR 0027: chosen once, on any device, and kept with the other preferences (which sync)."""
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        assert c.get("/api/preferences").json()["podcast_speed"] == 1.0
        assert c.put("/api/preferences", json={"podcast_speed": 2}).json()["podcast_speed"] == 2.0
        assert c.put("/api/preferences", json={"podcast_speed": 3}).status_code == 409
        kept = c.get("/api/preferences").json()
        assert kept["podcast_speed"] == 2.0 and kept["daily_goal"] == 20, "other preferences untouched"
