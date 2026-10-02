"""The one module the scripted fake replaces, checked without a process.

Nothing here spawns anything: the autouse ``no_real_codex`` fixture makes
``create_subprocess_exec`` raise, and every test below stops short of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run

from vademecum.appserver.errors import BridgeUnavailable
from vademecum.appserver.transport import (
    INHERITED_ENVIRONMENT,
    SubprocessTransport,
    _describe,
    child_environment,
)


class TestChildEnvironment:
    def test_the_child_inherits_an_allowlist_and_nothing_else(self) -> None:
        source = {
            "HOME": "/Users/owner",
            "PATH": "/usr/bin",
            "CODEX_HOME": "/Users/owner/.codex",
            # None of these may reach the child. A variable set for the web app
            # must not be able to change how the model bridge authenticates.
            "OPENAI_KEY": "sk-should-not-travel",
            "VADEMECUM_DATA_DIR": "/Users/owner/Library/Application Support/Vademecum",
            "AWS_SECRET_ACCESS_KEY": "nope",
            "ANYTHING_ELSE": "no",
        }
        child = child_environment(source)
        assert child == {
            "HOME": "/Users/owner",
            "PATH": "/usr/bin",
            "CODEX_HOME": "/Users/owner/.codex",
        }

    def test_home_and_path_are_on_the_list_because_codex_needs_them(self) -> None:
        # HOME is what `~/.codex` resolves against, so managed ChatGPT
        # credentials are found; PATH is how helpers are located.
        assert "HOME" in INHERITED_ENVIRONMENT
        assert "PATH" in INHERITED_ENVIRONMENT

    def test_a_missing_variable_is_simply_absent(self) -> None:
        assert child_environment({}) == {}


class TestStart:
    def test_a_missing_executable_is_a_category_not_a_path(self, tmp_path: Path) -> None:
        transport = SubprocessTransport(tmp_path / "does-not-exist", tmp_path)
        with pytest.raises(BridgeUnavailable) as caught:
            run(transport.start())
        assert caught.value.category == "codex_not_found"
        # The message is the category and nothing else: an error that names a
        # filesystem path is the same leak as a log line that does.
        assert str(caught.value) == "codex_not_found"
        assert str(tmp_path) not in str(caught.value)

    def test_a_file_without_the_execute_bit_is_the_same_refusal(self, tmp_path: Path) -> None:
        candidate = tmp_path / "codex"
        candidate.write_text("#!/bin/sh\n")
        candidate.chmod(0o644)
        transport = SubprocessTransport(candidate, tmp_path)
        with pytest.raises(BridgeUnavailable) as caught:
            run(transport.start())
        assert caught.value.category == "codex_not_found"

    def test_sending_before_starting_fails_closed(self, tmp_path: Path) -> None:
        transport = SubprocessTransport(tmp_path / "codex", tmp_path)
        with pytest.raises(BridgeUnavailable) as caught:
            run(transport.send_line("{}\n"))
        assert caught.value.category == "not_started"

    def test_stopping_something_never_started_is_harmless(self, tmp_path: Path) -> None:
        transport = SubprocessTransport(tmp_path / "codex", tmp_path)
        assert run(transport.stop()) == "not_started"

    def test_the_streams_of_a_dead_transport_are_simply_empty(self, tmp_path: Path) -> None:
        transport = SubprocessTransport(tmp_path / "codex", tmp_path)

        async def drain() -> list[str]:
            return [line async for line in transport.stdout_lines()]

        assert run(drain()) == []


class TestExitStatus:
    @pytest.mark.parametrize(
        ("returncode", "described"),
        [(0, "exit:0"), (1, "exit:1"), (-9, "signal:9"), (-15, "signal:15"), (None, "running")],
    )
    def test_an_exit_is_described_as_a_short_status(
        self, returncode: int | None, described: str
    ) -> None:
        assert _describe(returncode) == described
