"""Claude through the Claude Code CLI (ADR 0019): one turn per call, no tools,
validated output, and a sign-in state read from the CLI's own report."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vademecum.appserver.errors import BridgeProtocolError, BridgeUnavailable, LoginNotSupported
from vademecum.model import claude_cli
from vademecum.model.schemas import GRADING_SCHEMA


class FakeProcess:
    def __init__(self, out: bytes, code: int = 0) -> None:
        self._out = out
        self.returncode = code

    async def communicate(self):
        return self._out, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return self.returncode


def scripted(monkeypatch, replies: list[tuple[list[str], bytes]]):
    """Each call pops a reply; the args are recorded for the test."""
    calls: list[list[str]] = []

    async def fake_exec(*args, **kwargs):
        calls.append(list(args))
        _, out = replies.pop(0)
        return FakeProcess(out)

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    return calls


GRADE = {"outcome": "correct", "feedback": "Right.", "strengths": "", "missing_or_unsafe": "", "improved_answer": "", "uncertainty": ""}


def test_a_turn_runs_the_cli_once_with_no_tools_and_keeps_the_validated_output(monkeypatch, tmp_path: Path) -> None:
    cli = tmp_path / "claude"
    cli.write_text("#!/bin/sh\n")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    reply = json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": json.dumps(GRADE), "structured_output": GRADE, "session_id": "s1", "duration_ms": 1200})
    calls = scripted(monkeypatch, [([], reply.encode())])
    runner = claude_cli.ClaudeCliRunner(cli, workspace=workspace)
    result = asyncio.run(runner.run(instructions="Be brief.", developer_instructions="Grade.", prompt="Q", output_schema=GRADING_SCHEMA))
    assert result.payload == GRADE and result.turn_id == "s1"
    args = calls[0]
    assert args[0] == str(cli) and args[1:3] == ["-p", "Q"]
    assert "--json-schema" in args and "--max-turns" in args and args[args.index("--max-turns") + 1] == "1"
    assert "--disallowedTools" in args and "Bash" in args[args.index("--disallowedTools") + 1]
    assert args[args.index("--system-prompt") + 1] == "Be brief.\n\nGrade."


def test_a_cli_that_is_not_signed_in_is_a_login_refusal_and_not_a_retry(monkeypatch, tmp_path: Path) -> None:
    cli = tmp_path / "claude"
    cli.write_text("")
    (tmp_path / "ws").mkdir()
    reply = json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login", "structured_output": None})
    scripted(monkeypatch, [([], reply.encode())])
    runner = claude_cli.ClaudeCliRunner(cli, workspace=tmp_path / "ws")
    with pytest.raises(LoginNotSupported):
        asyncio.run(runner.run(instructions="", developer_instructions="", prompt="Q", output_schema=GRADING_SCHEMA))


def test_output_that_does_not_match_the_schema_is_refused(monkeypatch, tmp_path: Path) -> None:
    cli = tmp_path / "claude"
    cli.write_text("")
    (tmp_path / "ws").mkdir()
    bad = {"outcome": "brilliant", "feedback": "x"}
    reply = json.dumps({"type": "result", "is_error": False, "result": json.dumps(bad), "structured_output": bad})
    scripted(monkeypatch, [([], reply.encode())])
    runner = claude_cli.ClaudeCliRunner(cli, workspace=tmp_path / "ws")
    with pytest.raises(BridgeProtocolError):
        asyncio.run(runner.run(instructions="", developer_instructions="", prompt="Q", output_schema=GRADING_SCHEMA))


def test_a_missing_cli_is_unavailable_with_a_reason(tmp_path: Path) -> None:
    (tmp_path / "ws").mkdir()
    runner = claude_cli.ClaudeCliRunner(tmp_path / "nowhere", workspace=tmp_path / "ws")
    with pytest.raises(BridgeUnavailable):
        asyncio.run(runner.run(instructions="", developer_instructions="", prompt="Q", output_schema=GRADING_SCHEMA))
    bridge = claude_cli.ClaudeCliBridge(tmp_path / "nowhere", working_directory=tmp_path)
    status = asyncio.run(bridge.status())
    assert status.state == "unavailable" and status.reason == "claude_not_found"


def test_the_bridge_reads_sign_in_from_the_cli_and_never_signs_in_itself(monkeypatch, tmp_path: Path) -> None:
    cli = tmp_path / "claude"
    cli.write_text("")
    calls = scripted(monkeypatch, [([], b'{"loggedIn": true, "authMethod": "claude.ai"}'), ([], b'{"loggedIn": false}')])
    bridge = claude_cli.ClaudeCliBridge(cli, working_directory=tmp_path)
    first = asyncio.run(bridge.status())
    assert first.state == "signed_in" and first.signed_in is True and first.plan is None
    assert calls[0][1:] == ["auth", "status"]
    second = asyncio.run(bridge.status())
    assert second.state == "signed_out" and "terminal" in second.detail
    with pytest.raises(LoginNotSupported):
        asyncio.run(bridge.start_device_login())


def test_the_app_wires_claude_mode_end_to_end(monkeypatch, tmp_path: Path) -> None:
    """Health says claude, the Model page reads the CLI, and a build runs through it."""
    from fastapi.testclient import TestClient

    from conftest import LOCAL_ORIGIN, refusing_factory
    from fake_model import FakeArticle, FakeProvider
    from test_end_to_end import ABSTRACT, ASSESSMENT, EVIDENCE, LECTURE, SYNTHESIS, build, upload
    from vademecum.app import create_app
    from vademecum.config import Settings

    cli = tmp_path / "claude"
    cli.write_text("")
    replies = [
        ([], b'{"loggedIn": true}'),
        ([], json.dumps({"is_error": False, "structured_output": SYNTHESIS}).encode()),
        ([], json.dumps({"is_error": False, "structured_output": EVIDENCE}).encode()),
        ([], json.dumps({"is_error": False, "structured_output": ASSESSMENT}).encode()),
    ]
    scripted(monkeypatch, replies)
    provider = FakeProvider(articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="claude", claude_path=cli, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        health = client.get("/api/health").json()
        assert health["model_mode"] == "claude" and health["model_calls_configured"] is True
        assert client.get("/api/model/status").json()["state"] == "signed_in"
        pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
        assert upload(client, pile["id"], "lecture.txt", LECTURE.encode()).status_code == 201
        preview = client.get(f"/api/piles/{pile['id']}/build/preview").json()
        assert "Claude" in preview["disclosure"]["destination"] and "No API key" in preview["disclosure"]["destination"]
        build(client, pile["id"])
        assert client.get(f"/api/piles/{pile['id']}/build/status").json()["run"]["status"] == "succeeded"
        assert len(client.get("/api/points").json()) == 1
        assert client.get("/api/build/schedule").json()["can_run"] is True
