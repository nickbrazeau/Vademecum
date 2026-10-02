"""The tool surface, and the whole flow through it: note -> build -> Tutor -> grade."""

from __future__ import annotations

import asyncio

import pytest
from tests.mcp_support import LECTURE, QUOTE, call, call_expecting_error
from fake_model import ScriptedTurns
from mcp import Client

from vademecum.appserver.errors import BridgeUnavailable

pytestmark = pytest.mark.anyio

# Every tool, pinned. Adding one means adding it here, on purpose (and to the
# ADR's table). Note what is absent: nothing signs in, deletes, exports or
# backs up.
EXPECTED_TOOLS = {
    "get_today",
    "get_improvement_map",
    "list_piles",
    "get_pile",
    "create_pile",
    "get_source",
    "read_source",
    "add_text_source",
    "set_source",
    "list_images",
    "view_image",
    "save_schematic",
    "list_schematics",
    "get_schematic",
    "update_pile",
    "remove_source",
    "sources_folder",
    "sync_sources",
    "export_workspace",
    "backup_workspace",
    "open_dashboard",
    "list_learning_points",
    "get_learning_point",
    "flag_knowledge_gap",
    "list_flags",
    "update_flag",
    "build_preview",
    "build_start",
    "build_pending",
    "build_submit",
    "build_status",
    "build_cancel",
    "get_model_status",
    "tutor_overview",
    "tutor_next_question",
    "tutor_reveal",
    "tutor_grade",
    "tutor_record_grade",
    "tutor_self_assess",
    "tutor_advance",
    "tutor_history",
    "literature_topics",
    "literature_add_topic",
    "literature_check",
    "literature_updates",
    "literature_set_update_state",
}

# The tools that transmit content beyond the Mac, and only those, say so.
TRANSMITTING = {"build_start", "tutor_grade", "literature_check"}


async def wait_for_build(client: Client, pile_id: str) -> dict:
    """The build runs as a task on the API's loop; give it a moment."""
    for _ in range(200):
        status = await call(client, "build_status", {"pile_id": pile_id})
        if not status["running"] and status["run"] and status["run"]["status"] != "running":
            return status
        await asyncio.sleep(0.02)
    raise AssertionError("the build did not finish")


async def seed_bank(client: Client) -> tuple[dict, dict]:
    pile = await call(client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    added = await call(
        client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )
    assert added["outcome"] == "stored", added
    preview = await call(client, "build_preview", {"pile_id": pile["id"]})
    assert preview["blocked_reason"] == "", preview["blocked_reason"]
    started = await call(
        client,
        "build_start",
        {
            "pile_id": pile["id"],
            "batch_id": preview["batch_id"],
            "selection_hash": preview["selection_hash"],
        },
    )
    assert started["run"]["status"] in {"running", "succeeded"}
    status = await wait_for_build(client, pile["id"])
    assert status["run"]["status"] == "succeeded", status["run"]
    return pile, preview


async def test_the_tool_surface_is_pinned(mcp_client: Client) -> None:
    tools = (await mcp_client.list_tools()).tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is not None, tool.name
        assert tool.annotations.destructive_hint is False, tool.name
        assert tool.description, tool.name
        transmits = tool.annotations.open_world_hint is True
        assert transmits == (tool.name in TRANSMITTING), tool.name
        for forbidden in ("login", "delete", "retire"):
            assert forbidden not in tool.name


async def test_the_instructions_carry_the_boundaries(api) -> None:
    from vademecum_mcp.server import INSTRUCTIONS, create_server

    server = create_server(api)
    assert server.instructions == INSTRUCTIONS
    head = INSTRUCTIONS[:512].lower()
    assert "educational" in head
    assert "identifiers" in head
    assert "never" in head and "clinical advice" in head
    assert "machine reviewed" in INSTRUCTIONS.lower()
    assert "explicit yes" in INSTRUCTIONS


async def test_note_build_tutor_grade_through_the_tools(
    mcp_client: Client, turns: ScriptedTurns, provider
) -> None:
    pile, preview = await seed_bank(mcp_client)

    # The preview said exactly what would be sent, and where.
    assert preview["excerpt_count"] == 1
    assert preview["excerpts"][0]["display_name"] == "Lecture.md"
    assert QUOTE in preview["excerpts"][0]["text"]
    assert "OpenAI" in preview["disclosure"]["destination"]
    assert len(preview["selection_hash"]) == 64

    # Only short public topic words reached the provider.
    assert provider.queries == ['"septic shock"']

    points = await call(mcp_client, "list_learning_points", {})
    assert points["count"] == 1
    point = points["items"][0]
    assert point["support"] == "evidence_supported"
    assert "not clinically validated" in point["support_meaning"]
    full = await call(mcp_client, "get_learning_point", {"point_id": point["id"]})
    assert full["citations"][0]["display_name"] == "Lecture.md"

    overview = await call(mcp_client, "tutor_overview", {})
    assert overview["eligible"] == 1
    assert "disclosure" in overview

    first = await call(mcp_client, "tutor_next_question", {})
    question = first["question"]
    assert question is not None
    assert "reference_answer" not in question, "the reference is not shown up front"
    again = await call(mcp_client, "tutor_next_question", {})
    assert again["question"]["id"] == question["id"], "idempotent until advanced"

    revealed = await call(mcp_client, "tutor_reveal", {"question_id": question["id"]})
    assert revealed["question"]["reference_answer"] == "By serial lactate measurement."

    graded = await call(
        mcp_client, "tutor_grade", {"question_id": question["id"], "answer": "Serial lactate."}
    )
    assert graded["attempt"]["outcome"] == "partially_correct"
    assert graded["attempt"]["graded_by"] == "model"
    assert "answer" not in graded["attempt"], "the typed answer is not echoed back"
    assert graded["question"]["reference_answer"]

    # Exactly what was transmitted for the grade, and nothing else.
    prompt = turns.prompts("grading")[0]
    assert "Serial lactate." in prompt
    assert question["prompt"] in prompt
    assert "Lecture.md" not in prompt

    history = await call(mcp_client, "tutor_history", {"limit": 5})
    assert history["count"] == 1
    assert history["items"][0]["asked_prompt"] == question["prompt"]

    advanced = await call(mcp_client, "tutor_advance", {"question_id": question["id"]})
    assert "cycle" in advanced

    today = await call(mcp_client, "get_today", {})
    assert today["tutor"]["answered_total"] == 1
    assert today["worth_a_look"][0]["claim"].startswith("Lactate clearance")


async def test_a_stale_preview_is_refused_in_plain_language(mcp_client: Client) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "low"})
    await call(
        mcp_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Notes", "text": LECTURE, "confidence": "low"},
    )
    preview = await call(mcp_client, "build_preview", {"pile_id": pile["id"]})
    message = await call_expecting_error(
        mcp_client,
        "build_start",
        {"pile_id": pile["id"], "batch_id": preview["batch_id"], "selection_hash": "0" * 64},
    )
    assert "Traceback" not in message
    assert "preview" in message.lower()
    status = await call(mcp_client, "build_status", {"pile_id": pile["id"]})
    assert status["run"] is None, "a refused send starts no run"


async def test_an_empty_pile_previews_as_blocked_not_as_an_error(mcp_client: Client) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Empty", "confidence": "high"})
    preview = await call(mcp_client, "build_preview", {"pile_id": pile["id"]})
    assert preview["blocked_reason"]
    assert preview["batch_id"] == ""


async def test_an_unknown_record_is_a_readable_refusal(mcp_client: Client) -> None:
    message = await call_expecting_error(mcp_client, "get_pile", {"pile_id": "nope"})
    assert "No such" in message
    assert "Traceback" not in message


async def test_flags_round_trip(mcp_client: Client) -> None:
    flag = await call(
        mcp_client,
        "flag_knowledge_gap",
        {"text": "Unsure when to escalate vasopressors", "topic": "septic shock"},
    )
    assert flag["status"] == "open"
    listed = await call(mcp_client, "list_flags", {"status": "open"})
    assert [item["id"] for item in listed["items"]] == [flag["id"]]
    updated = await call(mcp_client, "update_flag", {"flag_id": flag["id"], "status": "addressed"})
    assert updated["status"] == "addressed"
    assert updated["addressed_at"]
    gaps = await call(mcp_client, "get_improvement_map", {})
    assert gaps["topics"][0]["topic"] == "septic shock"
    assert gaps["topics"][0]["addressed_flags"] == 1


async def test_grading_unavailable_offers_self_assessment(
    mcp_client: Client, turns: ScriptedTurns
) -> None:
    await seed_bank(mcp_client)
    question = (await call(mcp_client, "tutor_next_question", {}))["question"]
    turns.grading = [BridgeUnavailable("codex_not_found")]

    result = await call(
        mcp_client, "tutor_grade", {"question_id": question["id"], "answer": "Lactate."}
    )
    assert result["graded"] is False
    assert result["self_assess_available"] is True
    assert result["unavailable"]
    assert result["question"]["reference_answer"], "the reference is revealed so they can judge"

    recorded = await call(
        mcp_client,
        "tutor_self_assess",
        {"question_id": question["id"], "answer": "Lactate.", "outcome": "partially_correct"},
    )
    assert recorded["attempt"]["graded_by"] == "self"
    assert recorded["attempt"]["outcome"] == "self_assessed"


async def test_reading_a_source_pages_through_its_segments(mcp_client: Client) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    added = await call(
        mcp_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )
    source_id = added["source"]["id"]
    detail = await call(mcp_client, "get_source", {"source_id": source_id})
    assert detail["status"] == "extracted"
    page = await call(mcp_client, "read_source", {"source_id": source_id, "limit": 1})
    assert page["count"] == 1
    assert QUOTE in page["items"][0]["text"]
    assert page["next_offset"] == 1
    excluded = await call(mcp_client, "set_source", {"source_id": source_id, "excluded": True})
    assert excluded["excluded"] is True
    preview = await call(mcp_client, "build_preview", {"pile_id": pile["id"]})
    assert preview["blocked_reason"], "an excluded source is never selected"


async def test_literature_tools(mcp_client: Client, provider) -> None:
    topics = await call(mcp_client, "literature_topics", {})
    assert topics["topics"] == []
    assert topics["settings"]["provider"].startswith("PubMed")
    assert topics["settings"]["weekly_enabled"] is False
    created = await call(
        mcp_client, "literature_add_topic", {"label": "Septic shock", "query": "septic shock lactate"}
    )
    assert created["enabled"] is True
    checked = await call(mcp_client, "literature_check", {"topic_id": created["id"]})
    assert checked["status"] not in {"failed", "off", "busy", "unavailable"}, checked
    assert "results" in checked
    assert provider.queries and all("lecture" not in q.lower() for q in provider.queries)
    updates = await call(mcp_client, "literature_updates", {})
    assert updates["count"] >= 1
    marked = await call(
        mcp_client,
        "literature_set_update_state",
        {"update_id": updates["items"][0]["id"], "state": "acknowledged"},
    )
    assert marked["state"] == "acknowledged"


async def test_model_status_carries_no_account_identifier(mcp_client: Client) -> None:
    status = await call(mcp_client, "get_model_status", {})
    assert status["state"] == "signed_out"
    assert "email" not in str(status).lower()
    assert "@" not in str(status)
