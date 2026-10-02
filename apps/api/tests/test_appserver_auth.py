"""Whose plan a turn spends, checked before every turn.

Defect 9: a fake API-key or custom-provider account was accepted, and content
turns ran against it. AGENTS.md boundary 4 forbids API-key billing outright and
forbids falling back to it silently, so the credential is now re-read from the
running App Server before each turn and the model provider is pinned on the way
out *and* verified on the way back.

Every test here asserts the same two things: the category the owner is told, and
that nothing which could bill anybody was sent. "Nothing" is checked on the
transport itself -- no `thread/start`, no `turn/start` -- rather than on a
runner attribute, because the wire is what upstream acts on.

No process, no model, no network: ``conftest``'s autouse ``no_real_codex``
fixture makes that structural.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from fake_appserver import (
    ABSENT,
    AccountScript,
    Error,
    NoReply,
    ScriptedTransport,
    TurnScript,
    api_key_account,
    chatgpt_account,
    factory,
)

from vademecum.appserver import protocol
from vademecum.appserver.turns import (
    ACCOUNT_SIGNED_OUT,
    ACCOUNT_UNREADABLE,
    ACCOUNT_UNSUPPORTED,
    CredentialUnusable,
    ProviderUnsupported,
    TurnRunner,
)
from vademecum.appserver.client import AppServerClient

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
ANSWER = json.dumps({"ok": True})

# An email the fake reports and nothing downstream may ever repeat.
OWNER_EMAIL = "ZZOWNEREMAILZZ@example.test"


def bridge(
    script: TurnScript, tmp_path: Path, *, request_timeout: float = 5.0
) -> tuple[AppServerClient, TurnRunner, ScriptedTransport]:
    transport = ScriptedTransport(responder=script)
    client = AppServerClient(
        factory(transport),
        client_name="Vademecum",
        client_title="Vademecum",
        client_version="0.1.0",
        request_timeout=request_timeout,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return client, TurnRunner(client, workspace=workspace, turn_timeout=5.0), transport


def turn(script: TurnScript, tmp_path: Path, **options: Any) -> tuple[Any, ScriptedTransport]:
    """Run one turn, returning whatever it produced and the wire it used."""
    client, runner, transport = bridge(script, tmp_path, **options)

    async def scenario() -> Any:
        try:
            return await runner.run(
                instructions="be a grader",
                developer_instructions="return json",
                prompt="the question",
                output_schema=SCHEMA,
            )
        finally:
            await client.aclose()

    return run(scenario()), transport


def refused(
    exception: type[BaseException], script: TurnScript, tmp_path: Path, **options: Any
) -> tuple[BaseException, ScriptedTransport]:
    client, runner, transport = bridge(script, tmp_path, **options)

    async def scenario() -> BaseException:
        try:
            with pytest.raises(exception) as caught:
                await runner.run(
                    instructions="be a grader",
                    developer_instructions="return json",
                    prompt="the question",
                    output_schema=SCHEMA,
                )
            return caught.value
        finally:
            await client.aclose()

    return run(scenario()), transport


def assert_no_turn_ran(script: TurnScript, transport: ScriptedTransport) -> None:
    """Nothing that could start a model turn reached the wire."""
    assert script.threads == []
    assert script.turns == []
    assert transport.requests(protocol.THREAD_START) == []
    assert transport.requests(protocol.TURN_START) == []


def account_script(**options: Any) -> AccountScript:
    return AccountScript(**options)


def code_of(module: Any) -> tuple[set[str], set[str]]:
    """Every string a module's *code* contains, and every name it spells.

    Docstrings and comments are excluded: prose about what is refused is not a
    way of performing it, and a test that could not tell the two apart would
    make the module unable to explain itself.
    """
    import ast

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, holders)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    return literals, names


# --- the preflight -----------------------------------------------------------


# Every shape `account/read` can come back with that is not a ChatGPT sign-in,
# and the category each one is refused as. The first is the reviewer's
# reproduction; the rest are the ways a reply can be wrong without being an
# apiKey account.
UNUSABLE_ACCOUNTS: dict[str, tuple[Any, str]] = {
    "api_key": (api_key_account(), ACCOUNT_UNSUPPORTED),
    "amazon_bedrock": ({"account": {"type": "amazonBedrock"}}, ACCOUNT_UNSUPPORTED),
    "unknown_type": ({"account": {"type": "quantumBilling"}}, ACCOUNT_UNSUPPORTED),
    "null_account": ({"account": None, "requiresOpenaiAuth": True}, ACCOUNT_SIGNED_OUT),
    "absent_account": ({"requiresOpenaiAuth": True}, ACCOUNT_SIGNED_OUT),
    "reply_not_an_object": ("chatgpt", ACCOUNT_UNREADABLE),
    "reply_is_a_list": ([{"type": "chatgpt"}], ACCOUNT_UNREADABLE),
    "reply_is_null": (None, ACCOUNT_UNREADABLE),
    "account_not_an_object": ({"account": "chatgpt"}, ACCOUNT_UNREADABLE),
    "type_not_a_string": ({"account": {"type": 7}}, ACCOUNT_UNREADABLE),
    "type_is_empty": ({"account": {"type": ""}}, ACCOUNT_UNREADABLE),
    "no_type_at_all": ({"account": {"email": OWNER_EMAIL}}, ACCOUNT_UNREADABLE),
}


class TestNoTurnRunsOnACredentialWeDidNotApprove:
    @pytest.mark.parametrize("name", sorted(UNUSABLE_ACCOUNTS))
    def test_an_unusable_account_refuses_before_a_thread_exists(
        self, tmp_path: Path, name: str
    ) -> None:
        reply, category = UNUSABLE_ACCOUNTS[name]
        script = TurnScript(messages=(ANSWER,), account=account_script(account=reply))
        failure, transport = refused(CredentialUnusable, script, tmp_path)
        assert failure.category == category
        assert_no_turn_ran(script, transport)
        # And the MCP state was never even read: the credential is checked first.
        assert script.config_reads == 0

    def test_an_account_read_that_fails_refuses_the_turn(self, tmp_path: Path) -> None:
        script = TurnScript(
            messages=(ANSWER,),
            account=account_script(
                overrides={protocol.ACCOUNT_READ: Error(-32603, "ZZUPSTREAMZZ")}
            ),
        )
        failure, transport = refused(CredentialUnusable, script, tmp_path)
        assert failure.category == ACCOUNT_UNREADABLE
        assert_no_turn_ran(script, transport)

    def test_an_account_read_that_never_answers_refuses_the_turn(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(
            messages=(ANSWER,),
            account=account_script(overrides={protocol.ACCOUNT_READ: NoReply}),
        )
        failure, transport = refused(
            CredentialUnusable, script, tmp_path, request_timeout=0.2
        )
        assert failure.category == ACCOUNT_UNREADABLE
        assert_no_turn_ran(script, transport)

    def test_a_malformed_account_reply_refuses_the_turn(self, tmp_path: Path) -> None:
        """A reply the client cannot even decode is not a credential."""
        from fake_appserver import Raw

        script = TurnScript(
            messages=(ANSWER,),
            account=account_script(overrides={protocol.ACCOUNT_READ: Raw("{not json")}),
        )
        failure, transport = refused(
            CredentialUnusable, script, tmp_path, request_timeout=0.2
        )
        assert failure.category == ACCOUNT_UNREADABLE
        assert_no_turn_ran(script, transport)

    def test_the_signed_in_path_still_runs(self, tmp_path: Path) -> None:
        """The other half: the check must not refuse the account it exists for."""
        script = TurnScript(
            messages=(ANSWER,), account=account_script(account=chatgpt_account())
        )
        result, _ = turn(script, tmp_path)
        assert result.payload == {"ok": True}
        assert len(script.turns) == 1

    def test_the_credential_is_re_read_for_every_turn(self, tmp_path: Path) -> None:
        """Not cached, and not a snapshot: the owner can sign out between turns."""
        account = account_script(account=chatgpt_account())
        script = TurnScript(messages=(ANSWER,), account=account)
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                for _ in range(2):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
            finally:
                await client.aclose()

        run(scenario())
        assert account.reads == 2
        assert len(script.turns) == 2

    def test_an_account_that_changes_between_turns_stops_the_second_one(
        self, tmp_path: Path
    ) -> None:
        """The reproduction for "fresh": the first turn's answer proves nothing."""
        account = account_script(account=chatgpt_account())
        script = TurnScript(messages=(ANSWER,), account=account)
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                await runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
                # The owner signs out and signs in with an API key.
                account.account = api_key_account()
                with pytest.raises(CredentialUnusable) as caught:
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert caught.value.category == ACCOUNT_UNSUPPORTED
            finally:
                await client.aclose()

        run(scenario())
        # One turn ran, not two.
        assert len(script.turns) == 1
        assert account.reads == 2

    def test_reading_the_account_never_refreshes_a_token(self, tmp_path: Path) -> None:
        script = TurnScript(messages=(ANSWER,))
        _, transport = turn(script, tmp_path)
        reads = transport.requests(protocol.ACCOUNT_READ)
        assert reads
        assert all(request["params"] == {"refreshToken": False} for request in reads)

    @pytest.mark.parametrize("name", sorted(UNUSABLE_ACCOUNTS))
    def test_the_preflight_never_writes_to_the_owners_credentials(
        self, tmp_path: Path, name: str
    ) -> None:
        """Read-only: no login, no logout, no token, whatever it finds."""
        reply, _ = UNUSABLE_ACCOUNTS[name]
        script = TurnScript(messages=(ANSWER,), account=account_script(account=reply))
        _, transport = refused(CredentialUnusable, script, tmp_path)
        for method in (
            protocol.ACCOUNT_LOGIN_START,
            protocol.ACCOUNT_LOGIN_CANCEL,
            "account/logout",
        ):
            assert transport.requests(method) == []
        assert transport.requests(protocol.ACCOUNT_READ)

    def test_no_code_path_here_can_ask_for_an_api_key_login(self) -> None:
        """The refusal is not "prefer chatgpt"; the alternative is unspellable.

        Read off the syntax tree rather than the text, so a comment explaining
        what is refused does not read as a way of asking for it.
        """
        from vademecum.appserver import turns

        literals, names = code_of(turns)
        assert protocol.ACCOUNT_TYPE_API_KEY not in literals
        assert protocol.ACCOUNT_LOGIN_START not in literals
        assert protocol.ACCOUNT_LOGIN_CANCEL not in literals
        assert "account/logout" not in literals
        assert names.isdisjoint(
            {"ACCOUNT_TYPE_API_KEY", "ACCOUNT_LOGIN_START", "ACCOUNT_LOGIN_CANCEL"}
        )
        assert not any("forced_login" in name for name in names)

    def test_the_owners_email_never_reaches_a_log(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(
            messages=(ANSWER,),
            account=account_script(account=chatgpt_account(email=OWNER_EMAIL)),
        )
        turn(script, tmp_path)
        assert OWNER_EMAIL not in caplog.text
        assert "appserver_turn_account" in caplog.text
        assert "status=ok" in caplog.text

    def test_a_refusal_logs_a_category_and_nothing_upstream(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(
            messages=(ANSWER,),
            account=account_script(
                account={"account": {"type": "apiKey", "email": OWNER_EMAIL}}
            ),
        )
        failure, _ = refused(CredentialUnusable, script, tmp_path)
        assert failure.category == ACCOUNT_UNSUPPORTED
        assert OWNER_EMAIL not in caplog.text
        assert f"category={ACCOUNT_UNSUPPORTED}" in caplog.text
        # The exception carries the category and nothing else.
        assert str(failure) == ACCOUNT_UNSUPPORTED


# --- the provider ------------------------------------------------------------


class TestTheProviderIsPinned:
    """A ChatGPT login pointed at somebody else's endpoint is still not ours."""

    def test_every_thread_asks_for_the_openai_provider(self, tmp_path: Path) -> None:
        script = TurnScript(messages=(ANSWER,))
        turn(script, tmp_path)
        assert script.threads[0]["modelProvider"] == protocol.MODEL_PROVIDER_OPENAI

    def test_every_child_starts_pinned_to_the_openai_provider(self) -> None:
        """The second layer: a config.toml default cannot move the provider."""
        from vademecum.appserver import hardening

        assert 'model_provider="openai"' in hardening.STARTUP_CONFIG_FLAGS
        argv = hardening.startup_argv()
        assert argv[argv.index('model_provider="openai"') - 1] == "-c"

    @pytest.mark.parametrize(
        "provider", ["azure", "ollama", "openai-compatible", "", None, 7, ABSENT]
    )
    def test_a_thread_on_another_provider_starts_no_turn(
        self, tmp_path: Path, provider: Any
    ) -> None:
        script = TurnScript(messages=(ANSWER,), thread_model_provider=provider)
        failure, transport = refused(ProviderUnsupported, script, tmp_path)
        assert failure.category == "unsupported_provider"
        # The thread was started -- that is how the provider became known -- and
        # then nothing else was.
        assert len(script.threads) == 1
        assert script.turns == []
        assert transport.requests(protocol.TURN_START) == []

    def test_a_refused_provider_interrupts_nothing_because_nothing_started(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), thread_model_provider="azure")
        refused(ProviderUnsupported, script, tmp_path)
        assert script.interrupts == []

    def test_the_matching_provider_runs_the_turn(self, tmp_path: Path) -> None:
        script = TurnScript(messages=(ANSWER,), thread_model_provider="openai")
        result, _ = turn(script, tmp_path)
        assert result.payload == {"ok": True}

    def test_the_provider_refusal_logs_a_category_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(messages=(ANSWER,), thread_model_provider="ZZPRIVATEENDPOINTZZ")
        refused(ProviderUnsupported, script, tmp_path)
        assert "ZZPRIVATEENDPOINTZZ" not in caplog.text
        assert "category=unsupported_provider" in caplog.text
