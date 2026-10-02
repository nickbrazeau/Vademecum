"""The local product (ADR 0012): the API started beside the stdio server, and
registration with Codex and Claude Desktop without disturbing their files."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import httpx
import pytest

from vademecum_mcp import local
from vademecum_mcp.api_client import ApiClient

pytestmark = pytest.mark.anyio


class FakeChild:
    def __init__(self, *, exits: int | None = None) -> None:
        self.exits = exits
        self.terminated = False

    def poll(self) -> int | None:
        return self.exits

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float = 0) -> int:
        return 0


class Flaky:
    """An API that starts answering after a few probes."""

    def __init__(self, after: int) -> None:
        self.after = after
        self.calls = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.after:
            raise httpx.ConnectError("not yet")
        return httpx.Response(200, json={"status": "ok"})


async def test_the_api_is_started_when_nothing_answers_and_stopped_afterwards() -> None:
    flaky = Flaky(after=3)
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(flaky.handle))
    started: list[FakeChild] = []

    def start() -> FakeChild:
        child = FakeChild()
        started.append(child)
        return child

    child = await local.ensure_api(api, start=start, pause=0)
    assert child is started[0]
    assert flaky.calls == 4, "one probe before starting, then polling until it answered"
    local.stop_child(child)
    assert child.terminated


async def test_a_running_api_is_left_alone() -> None:
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(Flaky(after=0).handle))
    assert await local.ensure_api(api, start=lambda: pytest.fail("must not start"), pause=0) is None


async def test_an_api_that_dies_while_starting_is_reported() -> None:
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(Flaky(after=99).handle))
    with pytest.raises(RuntimeError, match="stopped while starting"):
        await local.ensure_api(api, start=lambda: FakeChild(exits=1), pause=0)


async def test_an_api_that_never_answers_is_stopped_and_reported() -> None:
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(Flaky(after=99).handle))
    child = FakeChild()
    with pytest.raises(RuntimeError, match="did not answer"):
        await local.ensure_api(api, start=lambda: child, attempts=3, pause=0)
    assert child.terminated


async def test_a_vanished_api_is_started_again_on_the_next_call() -> None:
    """The stdio server's client recovers once, then retries the request."""
    flaky = Flaky(after=1)
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(flaky.handle))
    recovered: list[int] = []

    async def recover() -> None:
        recovered.append(1)

    api.recover = recover
    assert (await api.get("/api/health")) == {"status": "ok"}
    assert recovered == [1]
    assert flaky.calls == 2


async def test_without_a_recovery_hook_an_unreachable_api_is_reported() -> None:
    api = ApiClient("http://127.0.0.1:8765", timeout=1, transport=httpx.MockTransport(Flaky(after=99).handle))
    from vademecum_mcp.api_client import ApiError

    with pytest.raises(ApiError) as refused:
        await api.get("/api/health")
    assert refused.value.code == "unreachable"


def test_codex_registration_replaces_ours_and_keeps_the_rest(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-5"\n\n'
        "[mcp_servers.node_repl]\n"
        'command = "node_repl"\n'
        "args = []\n\n"
        "[mcp_servers.vademecum]\n"
        "enabled = true\n"
        'url = "https://old.example/mcp"\n\n'
        "[mcp_servers.vademecum.env]\n"
        'X = "1"\n\n'
        "[desktop]\n"
        "sansFontSize = 14\n",
        encoding="utf-8",
    )
    backup = local.write_codex_config(config, command="/checkout/scripts/mcp.sh", args=["--stdio"])
    assert backup is not None and backup.exists()
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["model"] == "gpt-5"
    assert data["mcp_servers"]["node_repl"]["command"] == "node_repl"
    assert data["desktop"]["sansFontSize"] == 14
    ours = data["mcp_servers"]["vademecum"]
    assert ours == {
        "enabled": True,
        "command": "/checkout/scripts/mcp.sh",
        "args": ["--stdio"],
        "startup_timeout_sec": 60,
    }
    assert "url" not in ours and "env" not in ours, "the old tunnel entry is gone"


def test_codex_registration_creates_the_file_when_absent(tmp_path: Path) -> None:
    config = tmp_path / "codex" / "config.toml"
    assert local.write_codex_config(config, command="/c/mcp.sh", args=["--stdio"]) is None
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["vademecum"]["command"] == "/c/mcp.sh"


def test_claude_registration_merges_into_the_existing_file(tmp_path: Path) -> None:
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps({"preferences": {"sidebarMode": "chat"}, "mcpServers": {"other": {"command": "x"}}}),
        encoding="utf-8",
    )
    backup = local.write_claude_config(config, command="/c/mcp.sh", args=["--stdio"])
    assert backup is not None
    data = json.loads(config.read_text(encoding="utf-8"))
    assert data["preferences"] == {"sidebarMode": "chat"}
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["mcpServers"]["vademecum"] == {"command": "/c/mcp.sh", "args": ["--stdio"]}


def test_the_launcher_is_this_checkouts_script() -> None:
    command, args = local.launcher()
    assert command.endswith("/scripts/mcp.sh")
    assert Path(command).is_file()
    assert args == ["--stdio"]


def test_the_api_child_is_told_who_started_it(monkeypatch, tmp_path) -> None:
    """ADR 0012: the child watches this pid and stops itself when it goes."""
    import os

    from vademecum_mcp import local

    captured: dict = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["env"] = kwargs["env"]

    monkeypatch.setattr(local.subprocess, "Popen", FakePopen)
    local.start_api(tmp_path / "logs" / "api.log", env={})
    assert captured["env"]["VADEMECUM_PARENT_PID"] == str(os.getpid())
    assert captured["env"]["VADEMECUM_MODEL_PROVIDER"] == "host"
    assert captured["args"][-2:] == ["-m", "vademecum"]


def test_the_login_agent_runs_the_api_in_host_mode_and_logs_to_the_records(tmp_path: Path) -> None:
    import plistlib

    from vademecum_mcp import local

    plist = tmp_path / "LaunchAgents" / "com.vademecum.api.plist"
    local.write_login_agent(plist, python="/venv/bin/python", working_directory=tmp_path / "checkout", log_path=tmp_path / "data" / "logs" / "api.log")
    agent = plistlib.loads(plist.read_bytes())
    assert agent["Label"] == "com.vademecum.api"
    assert agent["ProgramArguments"] == ["/venv/bin/python", "-m", "vademecum"]
    assert agent["EnvironmentVariables"] == {"VADEMECUM_MODEL_PROVIDER": "host"}
    assert agent["RunAtLoad"] is True and agent["KeepAlive"] is True
    assert agent["StandardOutPath"].endswith("logs/api.log")
    assert (tmp_path / "data" / "logs").is_dir()


def test_loading_and_removing_the_login_agent_go_through_launchctl(monkeypatch, tmp_path: Path) -> None:
    from vademecum_mcp import local

    calls: list[list[str]] = []

    class Done:
        returncode = 0

    monkeypatch.setattr(local.subprocess, "run", lambda args, **kwargs: calls.append(args) or Done())
    plist = tmp_path / "com.vademecum.api.plist"
    plist.write_bytes(b"<plist/>")
    assert local.load_login_agent(plist) is True
    assert [c[1] for c in calls] == ["bootout", "bootstrap", "kickstart"]
    assert all(c[0] == "launchctl" for c in calls)
    calls.clear()
    assert local.remove_login_agent(plist) is True
    assert [c[1] for c in calls] == ["bootout"] and not plist.exists()
    assert local.remove_login_agent(plist) is False


def test_setup_sync_records_the_seat_in_the_settings_file(monkeypatch, tmp_path: Path) -> None:
    from vademecum.config import get_settings
    from vademecum_mcp.main import _setup_sync

    get_settings.cache_clear()

    settings_file = tmp_path / "settings.env"
    monkeypatch.setenv("VADEMECUM_SETTINGS_FILE", str(settings_file))
    assert _setup_sync("http://seat.example", token="x" * 20) == 2, "https only"
    assert _setup_sync("https://seat.example/", token="short") == 2
    assert _setup_sync("https://vademecum-seat.example.workers.dev/", token="a-long-random-token-value") == 0
    text = settings_file.read_text()
    assert 'VADEMECUM_SYNC_PEER_URL="https://vademecum-seat.example.workers.dev"' in text
    assert "a-long-random-token-value" in text
    assert get_settings().sync_peer_url == "https://vademecum-seat.example.workers.dev"
    assert _setup_sync(None, remove=True) == 0
    get_settings.cache_clear()
    assert get_settings().sync_peer_url == ""
    get_settings.cache_clear()
