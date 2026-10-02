"""Host mode through the tools (ADR 0009): the assistant is the model.

The API runs with ``model_provider="host"`` and no turn runner of any kind. A
test plays the assistant: it reads each pending turn from a tool reply and
submits the scripted result for it, exactly as ChatGPT would.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from mcp import Client
from tests.mcp_support import (
    API_ORIGIN,
    ASSESSMENT,
    EVIDENCE,
    GRADE,
    LECTURE,
    SYNTHESIS,
    call,
    call_expecting_error,
)

from fake_appserver import AccountScript, ScriptedTransport, factory  # noqa: E402
from fake_model import FakeProvider  # noqa: E402
from vademecum.app import create_app  # noqa: E402
from vademecum.config import Settings as ApiSettings  # noqa: E402
from vademecum_mcp.api_client import ApiClient  # noqa: E402
from vademecum_mcp.server import create_server  # noqa: E402

pytestmark = pytest.mark.anyio

REPLIES = {"synthesis": SYNTHESIS, "evidence": EVIDENCE, "assessment": ASSESSMENT}


@pytest.fixture
async def host_client(tmp_path: Path, provider: FakeProvider) -> AsyncIterator[Client]:
    settings = ApiSettings(data_dir=tmp_path / "host", host="127.0.0.1", port=8765, model_provider="host")
    transports = factory(
        ScriptedTransport(responder=AccountScript()),
        ScriptedTransport(responder=AccountScript()),
    )
    app = create_app(settings, transport_factory=transports, provider_factory=lambda: provider)
    async with app.router.lifespan_context(app):
        api = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=app))
        try:
            async with Client(create_server(api)) as client:
                yield client
        finally:
            await api.aclose()


async def play_build(client: Client, pile_id: str, first: dict) -> dict:
    turn = first
    for _ in range(20):
        reply = await call(client, "build_submit", {"pile_id": pile_id, "turn_id": turn["turn_id"], "result": REPLIES[turn["kind"]]})
        assert reply["accepted"] is True
        if reply["next"] is None:
            return reply
        turn = reply["next"]
    raise AssertionError("the build never finished")


async def test_the_assistant_does_the_model_work(host_client: Client, provider: FakeProvider) -> None:
    pile = await call(host_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    await call(
        host_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )
    preview = await call(host_client, "build_preview", {"pile_id": pile["id"]})
    assert "your own ChatGPT" in preview["disclosure"]["destination"]

    started = await call(
        host_client,
        "build_start",
        {"pile_id": pile["id"], "batch_id": preview["batch_id"], "selection_hash": preview["selection_hash"]},
    )
    assert started["pending"], "host mode hands the first turn back at once"
    first = started["pending"][0]
    assert first["kind"] == "synthesis"
    assert first["how_to_submit"]
    assert first["output_schema"]["additionalProperties"] is False

    listed = await call(host_client, "build_pending", {"pile_id": pile["id"]})
    assert listed["mode"] == "host"
    assert listed["turns"][0]["turn_id"] == first["turn_id"]

    done = await play_build(host_client, pile["id"], first)
    assert done["run"]["status"] == "succeeded"
    assert provider.queries == ['"septic shock"'], "PubMed was asked by the server"

    points = await call(host_client, "list_learning_points", {})
    assert points["items"][0]["support"] == "evidence_supported"

    # Grading: the assistant grades, the server records.
    question = (await call(host_client, "tutor_next_question", {}))["question"]
    asked = await call(host_client, "tutor_grade", {"question_id": question["id"], "answer": "Serial lactate."})
    assert asked["attempt"] is None
    assert asked["pending"]["kind"] == "grading"
    assert "Serial lactate." in asked["pending"]["material"]
    recorded = await call(
        host_client, "tutor_record_grade", {"turn_id": asked["pending"]["turn_id"], "result": GRADE}
    )
    assert recorded["attempt"]["outcome"] == "partially_correct"
    assert recorded["attempt"]["graded_by"] == "model"
    history = await call(host_client, "tutor_history", {})
    assert history["count"] == 1

    status = await call(host_client, "get_model_status", {})
    assert status["state"] == "signed_out", "the Codex bridge is not what grades here"


async def test_a_bad_submission_is_a_readable_refusal_and_the_turn_stays(host_client: Client) -> None:
    pile = await call(host_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    await call(
        host_client,
        "add_text_source",
        {"pile_id": pile["id"], "title": "Lecture", "text": LECTURE, "confidence": "mid"},
    )
    preview = await call(host_client, "build_preview", {"pile_id": pile["id"]})
    started = await call(
        host_client,
        "build_start",
        {"pile_id": pile["id"], "batch_id": preview["batch_id"], "selection_hash": preview["selection_hash"]},
    )
    turn = started["pending"][0]
    message = await call_expecting_error(
        host_client,
        "build_submit",
        {"pile_id": pile["id"], "turn_id": turn["turn_id"], "result": {"points": [], "verified": True}},
    )
    assert "output_schema" in message
    assert "verified" not in message
    listed = await call(host_client, "build_pending", {"pile_id": pile["id"]})
    assert listed["turns"][0]["turn_id"] == turn["turn_id"]
    cancelled = await call(host_client, "build_cancel", {"pile_id": pile["id"]})
    assert cancelled["stopped"] is True


async def test_codex_mode_replies_carry_no_pending_turns(mcp_client: Client) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    listed = await call(mcp_client, "build_pending", {"pile_id": pile["id"]})
    assert listed["mode"] == "codex"
    assert listed["turns"] == []
    message = await call_expecting_error(
        mcp_client, "build_submit", {"pile_id": pile["id"], "turn_id": "turn_x", "result": {}}
    )
    assert "nothing to submit" in message
