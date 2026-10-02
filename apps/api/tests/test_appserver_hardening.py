"""The tool and MCP lockdown, at both layers.

Startup flags are asserted as strings because that is what reaches `exec`. The
per-thread config is asserted through a real `thread/start`, because the thing
that matters is what the App Server is actually told -- not what a helper
returns in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from fake_appserver import (
    Error,
    ScriptedTransport,
    TurnScript,
    config_read_result,
    factory,
)

from vademecum.appserver import hardening
from vademecum.appserver.client import AppServerClient
from vademecum.appserver.errors import BridgeUnavailable
from vademecum.appserver.transport import SubprocessTransport
from vademecum.appserver.turns import TurnRunner

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
ANSWER = json.dumps({"ok": True})

# Nothing like the owner's real servers, and nothing this codebase could have
# guessed. If a name here comes back disabled, it was discovered.
INVENTED_SERVERS = ("zz-weather-oracle", "zz-payroll", "zz-ssh-jump")


def make_client(*transports: ScriptedTransport) -> AppServerClient:
    return AppServerClient(
        factory(*transports),
        client_name="Vademecum",
        client_title="Vademecum",
        client_version="0.1.0",
        request_timeout=5.0,
    )


def bridge(script: TurnScript, tmp_path: Path) -> tuple[AppServerClient, TurnRunner]:
    transport = ScriptedTransport(responder=script)
    client = make_client(transport)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return client, TurnRunner(client, workspace=workspace, turn_timeout=5.0)


def run_turn(script: TurnScript, tmp_path: Path) -> Any:
    client, runner = bridge(script, tmp_path)

    async def scenario() -> Any:
        try:
            return await runner.run(
                instructions="i",
                developer_instructions="d",
                prompt="p",
                output_schema=SCHEMA,
            )
        finally:
            await client.aclose()

    return run(scenario())


# --- defect 7 ----------------------------------------------------------------


class TestStartupFlags:
    """Defect 7: telemetry overrides were missing from the child spawn.

    Every flag below was additionally verified by hand against the installed
    ``codex-cli 0.153.4``: the child starts with the whole set, answers
    ``initialize``, and answers ``account/read`` with the owner's ChatGPT
    account. None was rejected. That verification cannot live in a test --
    running it would spawn a real App Server, which the suite forbids.
    """

    @pytest.mark.parametrize(
        "flag",
        [
            "otel.log_user_prompt=false",
            'otel.exporter="none"',
            'otel.trace_exporter="none"',
            'otel.metrics_exporter="none"',
            "analytics.enabled=false",
        ],
    )
    def test_the_telemetry_overrides_are_passed_to_every_child(self, flag: str) -> None:
        assert flag in hardening.STARTUP_CONFIG_FLAGS

    def test_a_learner_answer_can_never_be_written_into_a_span(self) -> None:
        """The one flag that decides whether the prompt itself is exported."""
        assert "otel.log_user_prompt=false" in hardening.STARTUP_CONFIG_FLAGS
        assert "otel.log_user_prompt=true" not in hardening.STARTUP_CONFIG_FLAGS

    def test_no_exporter_is_left_at_its_default(self) -> None:
        exporters = [
            flag for flag in hardening.STARTUP_CONFIG_FLAGS if flag.startswith("otel.")
        ]
        assert sorted(exporters) == [
            'otel.exporter="none"',
            "otel.log_user_prompt=false",
            'otel.metrics_exporter="none"',
            'otel.trace_exporter="none"',
        ]

    def test_every_flag_reaches_the_child_argv_as_a_dash_c_pair(
        self, tmp_path: Path
    ) -> None:
        executable = tmp_path / "codex"
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
        argv = SubprocessTransport(executable, tmp_path).build_argv()
        assert argv[:2] == [str(executable), "app-server"]
        pairs = [argv[index + 1] for index, item in enumerate(argv) if item == "-c"]
        assert pairs == list(hardening.STARTUP_CONFIG_FLAGS)
        assert len(argv) == 2 + 2 * len(hardening.STARTUP_CONFIG_FLAGS)

    def test_the_flag_set_is_frozen_at_import(self) -> None:
        assert isinstance(hardening.STARTUP_CONFIG_FLAGS, tuple)
        with pytest.raises(TypeError):
            hardening.STARTUP_CONFIG_FLAGS[0] = "features.shell_tool=true"  # type: ignore[index]


# --- defect 6 ----------------------------------------------------------------


class TestMcpDiscovery:
    """Defect 6: discovery was cached per client, so a config change was missed."""

    def test_the_effective_config_is_read_once_per_thread(self, tmp_path: Path) -> None:
        script = TurnScript(messages=(ANSWER,), mcp_servers=INVENTED_SERVERS)
        client, runner = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                for _ in range(3):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
            finally:
                await client.aclose()

        run(scenario())
        assert script.config_reads == 3
        assert len(script.threads) == 3

    def test_a_server_added_between_two_threads_is_still_disabled(
        self, tmp_path: Path
    ) -> None:
        """The reproduction: the owner edits config.toml while the bridge runs."""
        script = TurnScript(messages=(ANSWER,), mcp_servers=("zz-first",))
        client, runner = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                await runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
                # config.toml changes under the running process.
                script.mcp_servers = ("zz-first", "zz-added-later")
                await runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            finally:
                await client.aclose()

        run(scenario())
        assert set(script.threads[0]["config"]["mcp_servers"]) == {"zz-first"}
        assert set(script.threads[1]["config"]["mcp_servers"]) == {
            "zz-first",
            "zz-added-later",
        }

    def test_every_discovered_server_is_disabled_by_name(self, tmp_path: Path) -> None:
        """`mcp_servers = {}` clears nothing: thread config is deep-merged."""
        script = TurnScript(messages=(ANSWER,), mcp_servers=INVENTED_SERVERS)
        run_turn(script, tmp_path)
        servers = script.threads[0]["config"]["mcp_servers"]
        assert set(servers) == set(INVENTED_SERVERS)
        assert all(entry == {"enabled": False} for entry in servers.values())
        assert servers != {}

    def test_the_names_are_discovered_and_never_hardcoded(self) -> None:
        """A name written into this codebase is a name that stops matching."""
        package = Path(hardening.__file__).parent
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(package.glob("*.py"))
        )
        for name in ("computer-use", "node_repl", *INVENTED_SERVERS):
            assert name not in source

    def test_web_search_is_turned_off_in_the_thread_config_too(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), mcp_servers=INVENTED_SERVERS)
        run_turn(script, tmp_path)
        assert script.threads[0]["config"]["tools"] == {"web_search": False}

    def test_an_empty_server_table_is_read_as_none_configured(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), mcp_servers=())
        run_turn(script, tmp_path)
        assert script.threads[0]["config"]["mcp_servers"] == {}

    def test_a_null_server_table_is_read_as_none_configured(self, tmp_path: Path) -> None:
        script = TurnScript(
            messages=(ANSWER,),
            config_reply={"config": {"mcp_servers": None}, "origins": {}},
        )
        run_turn(script, tmp_path)
        assert script.threads[0]["config"]["mcp_servers"] == {}

    def test_a_config_read_failure_fails_closed(self, tmp_path: Path) -> None:
        script = TurnScript(messages=(ANSWER,), config_reply=Error(-32603, "no config for you"))
        client, runner = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeUnavailable) as caught:
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert caught.value.category == "config_unreadable"
                # And no thread was started on top of an unknown MCP state.
                assert script.threads == []
            finally:
                await client.aclose()

        run(scenario())

    @pytest.mark.parametrize(
        "reply",
        [
            {"origins": {}},  # no `config` at all
            {"config": {"model": "gpt-test"}},  # `mcp_servers` has moved
            {"config": {"mcp_servers": []}},  # not a table
            "not an object",
        ],
    )
    def test_a_config_this_module_cannot_read_fails_closed(
        self, tmp_path: Path, reply: Any
    ) -> None:
        script = TurnScript(messages=(ANSWER,), config_reply=reply)
        client, runner = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeUnavailable):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert script.threads == []
            finally:
                await client.aclose()

        run(scenario())

    def test_nothing_is_cached_between_clients_either(self) -> None:
        """There is no cache left to be stale."""
        assert not hasattr(hardening, "_CACHE")
        assert not hasattr(hardening, "forget")

    def test_the_thread_config_handed_out_cannot_be_edited_by_a_caller(self) -> None:
        built = hardening.build_thread_config(INVENTED_SERVERS)
        copy = hardening.copy_thread_config(built)
        copy["mcp_servers"]["zz-payroll"]["enabled"] = True
        assert built["mcp_servers"]["zz-payroll"] == {"enabled": False}

    def test_the_fixture_still_describes_enabled_servers(self) -> None:
        """Or the hardening tests would pass without the hardening."""
        config = config_read_result(INVENTED_SERVERS)
        assert all(
            entry["enabled"] is True for entry in config["config"]["mcp_servers"].values()
        )
