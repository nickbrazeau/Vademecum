"""One grading turn, against a scripted App Server.

No process, no model, no network: ``conftest``'s autouse ``no_real_codex``
fixture makes that structural, and everything here drives ``fake_appserver``.

Most of this file is regression cover for eight escapes an independent reviewer
reproduced against the first version of ``turns.py``. Each is named in the
docstring of the class that covers it.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from conftest import run
from fake_appserver import (
    ScriptedTransport,
    TurnScript,
    UNKNOWN_PHASE,
    deliver,
    factory,
    native_item,
    thread_item,
    turn_error,
    turn_notification,
    user_message_item,
)

from vademecum.appserver import protocol
from vademecum.appserver.client import AppServerClient
from vademecum.appserver.errors import BridgeProtocolError, BridgeTimeout, BridgeUnavailable
from vademecum.appserver.turns import (
    MAX_AGENT_MESSAGES,
    TurnRefused,
    TurnRunner,
    TurnUnsafe,
    UnenforceableSchema,
)

# A schema entirely inside the enforced subset, so nothing here trips the
# pre-send check by accident.
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ok"],
    "properties": {"ok": {"type": "boolean"}},
}
ANSWER = json.dumps({"ok": True})


def make_client(*transports: ScriptedTransport, **options: Any) -> AppServerClient:
    return AppServerClient(
        factory(*transports),
        client_name="Vademecum",
        client_title="Vademecum",
        client_version="0.1.0",
        **options,
    )


def bridge(
    script: TurnScript,
    tmp_path: Path,
    *,
    turn_timeout: float = 5.0,
    request_timeout: float = 5.0,
    extra_transports: int = 0,
) -> tuple[AppServerClient, TurnRunner, ScriptedTransport]:
    transport = ScriptedTransport(responder=script)
    spares = [ScriptedTransport(responder=script) for _ in range(extra_transports)]
    client = make_client(transport, *spares, request_timeout=request_timeout)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return client, TurnRunner(client, workspace=workspace, turn_timeout=turn_timeout), transport


def one_turn(
    script: TurnScript,
    tmp_path: Path,
    *,
    prompt: str = "the question",
    max_output_chars: int = 200_000,
    **options: Any,
) -> Any:
    """Run a turn to whatever it ends in, and always close the client."""
    client, runner, _ = bridge(script, tmp_path, **options)

    async def scenario() -> Any:
        try:
            return await runner.run(
                instructions="be a grader",
                developer_instructions="return json",
                prompt=prompt,
                output_schema=SCHEMA,
                max_output_chars=max_output_chars,
            )
        finally:
            await client.aclose()

    return run(scenario())


async def until_started(runner: TurnRunner, limit: int = 2000) -> None:
    """Yield until the runner knows its turn id, deterministically.

    Nothing in the fake sleeps, so a fixed number of ``sleep(0)`` calls would be
    a guess about how many event-loop turns three round trips take.
    """
    for _ in range(limit):
        if runner._turn_id is not None:
            return
        await asyncio.sleep(0)
    raise AssertionError("the turn never started")


def raises(exception: type[BaseException], script: TurnScript, tmp_path: Path, **options: Any):
    with pytest.raises(exception) as caught:
        one_turn(script, tmp_path, **options)
    return caught.value


# --- the happy path, which everything else is a deviation from ---------------


class TestASuccessfulTurn:
    def test_a_final_agent_message_is_parsed_and_validated(self, tmp_path: Path) -> None:
        result = one_turn(TurnScript(messages=(ANSWER,)), tmp_path)
        assert result.payload == {"ok": True}
        assert result.turn_id == "turn-1"
        assert result.raw_chars == len(ANSWER)

    def test_the_thread_is_ephemeral_read_only_and_never_approving(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,))
        one_turn(script, tmp_path)
        params = script.threads[0]
        assert params["ephemeral"] is True
        assert params["sandbox"] == protocol.SANDBOX_READ_ONLY
        assert params["approvalPolicy"] == protocol.APPROVAL_NEVER
        turn = script.turns[0]
        assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
        assert turn["outputSchema"] == SCHEMA

    def test_a_failed_turn_becomes_a_category_and_never_the_servers_words(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(
            messages=(),
            status="failed",
            error=turn_error("usageLimitExceeded", "PROMPT ECHOED BACK: secret"),
        )
        failure = raises(TurnRefused, script, tmp_path)
        assert failure.category == "rate_limited"
        assert "secret" not in str(failure)
        assert "PROMPT" not in str(failure)

    def test_an_events_before_reply_turn_still_completes(self, tmp_path: Path) -> None:
        """The reason the runner subscribes before it sends `turn/start`."""
        result = one_turn(TurnScript(messages=(ANSWER,), events_before_reply=True), tmp_path)
        assert result.payload == {"ok": True}


# --- defect 1 ----------------------------------------------------------------


# Every ThreadItem variant in the pinned union except the three on the
# allowlist. (`userMessage` is on it, but only as a correlated echo of our own
# input -- see TestTheInputEcho, where an uncorrelated one still fails closed.)
UNSAFE_ITEM_TYPES = (
    "commandExecution",
    "fileChange",
    "mcpToolCall",
    "dynamicToolCall",
    "webSearch",
    "imageView",
    "imageGeneration",
    "hookPrompt",
    "functionCallOutput",
    "subAgentActivity",
    "collabAgentToolCall",
    "sleep",
    "plan",
    "enteredReviewMode",
    "exitedReviewMode",
    "contextCompaction",
)


class TestUnsafeItemsFailClosed:
    """Defect 1: tool and lifecycle items escaped the fail-closed check.

    The old guard only reacted to server *requests*, so a native `item/started`
    carrying a `commandExecution` streamed past and the turn was accepted.
    """

    def test_an_item_started_command_execution_fails_the_turn(self, tmp_path: Path) -> None:
        """The reviewer's exact reproduction: a native tool item, then an answer."""
        script = TurnScript(
            items=(native_item("commandExecution"), thread_item(ANSWER)),
            emit_item_completed=False,
        )
        client, runner, transport = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(TurnUnsafe):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                # And the turn was stopped upstream rather than left running.
                assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
            finally:
                await client.aclose()

        run(scenario())

    @pytest.mark.parametrize("item_type", UNSAFE_ITEM_TYPES)
    def test_every_item_type_outside_the_allowlist_fails_the_turn(
        self, tmp_path: Path, item_type: str
    ) -> None:
        script = TurnScript(items=(native_item(item_type), thread_item(ANSWER)))
        assert isinstance(raises(TurnUnsafe, script, tmp_path), TurnUnsafe)

    def test_an_item_type_this_build_has_never_heard_of_fails_the_turn(
        self, tmp_path: Path
    ) -> None:
        """The allowlist's whole point: a future Codex variant fails closed."""
        script = TurnScript(items=(native_item("quantumTeleportation"), thread_item(ANSWER)))
        raises(TurnUnsafe, script, tmp_path)

    def test_an_item_with_no_readable_type_fails_the_turn(self, tmp_path: Path) -> None:
        script = TurnScript(items=({"id": "x"},))
        raises(TurnUnsafe, script, tmp_path)

    def test_reasoning_is_allowed_and_never_becomes_the_answer(self, tmp_path: Path) -> None:
        """The second and last member of the allowlist."""
        script = TurnScript(
            items=(
                native_item("reasoning", summary=["thinking about it"]),
                thread_item(ANSWER),
            )
        )
        assert one_turn(script, tmp_path).payload == {"ok": True}

    def test_the_allowlist_is_three_names_long(self) -> None:
        assert protocol.SAFE_ITEM_TYPES == {"agentMessage", "reasoning", "userMessage"}

    def test_a_tool_request_still_fails_the_turn(self, tmp_path: Path) -> None:
        """The original guard, which the allowlist is in addition to."""
        script = TurnScript(messages=(ANSWER,), tool_event="item/tool/call")
        raises(TurnUnsafe, script, tmp_path)


# --- defect 2 ----------------------------------------------------------------


class TestOnlyTheFinalAnswerIsAnAnswer:
    """Defect 2: commentary-only JSON was accepted as the result."""

    def test_a_commentary_only_turn_produces_no_answer(self, tmp_path: Path) -> None:
        script = TurnScript(
            items=(thread_item(json.dumps({"ok": False}), phase="commentary"),)
        )
        failure = raises(BridgeProtocolError, script, tmp_path)
        assert failure.category == "no_final_message"

    def test_commentary_never_wins_over_the_final_answer(self, tmp_path: Path) -> None:
        """Even when it arrives last, which is the ordering that hid the bug."""
        script = TurnScript(
            items=(
                thread_item(ANSWER, phase="final_answer"),
                thread_item(json.dumps({"ok": False}), phase="commentary"),
            )
        )
        assert one_turn(script, tmp_path).payload == {"ok": True}

    def test_the_final_answer_phase_is_the_one_that_is_read(self, tmp_path: Path) -> None:
        script = TurnScript(items=(thread_item(ANSWER, phase="final_answer"),))
        assert one_turn(script, tmp_path).payload == {"ok": True}

    def test_an_absent_phase_is_accepted_as_the_answer(self, tmp_path: Path) -> None:
        """The pinned rule for an unknown phase, and the only one.

        MessagePhase's own description says providers do not emit it
        consistently and that `None` means "phase unknown", so refusing an
        absent phase would refuse every legacy model.
        """
        script = TurnScript(items=(thread_item(ANSWER),))
        assert "phase" not in script.emitted_items()[0]
        assert one_turn(script, tmp_path).payload == {"ok": True}

    def test_an_explicitly_null_phase_follows_the_same_rule(self, tmp_path: Path) -> None:
        script = TurnScript(items=(thread_item(ANSWER, phase=None),))
        assert script.emitted_items()[0]["phase"] is None
        assert one_turn(script, tmp_path).payload == {"ok": True}

    def test_a_phase_this_build_does_not_recognise_is_never_the_answer(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(items=(thread_item(ANSWER, phase=UNKNOWN_PHASE),))
        assert raises(BridgeProtocolError, script, tmp_path).category == "no_final_message"

    def test_a_final_answer_outranks_an_unphased_message_that_follows_it(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(
            items=(
                thread_item(ANSWER, phase="final_answer"),
                thread_item(json.dumps({"ok": False})),
            )
        )
        assert one_turn(script, tmp_path).payload == {"ok": True}


# --- defect 3 ----------------------------------------------------------------


class TestEveryEventIsMatchedThreeWays:
    """Defect 3: a `turn/completed` naming a different turn was accepted."""

    def test_a_completion_for_another_turn_does_not_complete_ours(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), completed_turn_id="turn-999")
        # Nothing completes this turn, so it ends at the deadline rather than
        # with somebody else's result.
        raises(BridgeTimeout, script, tmp_path, turn_timeout=0.2)

    def test_a_completion_for_another_thread_does_not_complete_ours(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), completed_thread_id="thread-999")
        raises(BridgeTimeout, script, tmp_path, turn_timeout=0.2)

    def test_a_foreign_completion_is_counted_rather_than_accepted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO", logger="vademecum.appserver")
        script = TurnScript(messages=(ANSWER,), completed_turn_id="turn-999")
        raises(BridgeTimeout, script, tmp_path, turn_timeout=0.2)
        assert "appserver_turn_event_ignored" in caplog.text
        assert "category=not_ours" in caplog.text

    def test_an_event_from_another_generation_is_ignored(self, tmp_path: Path) -> None:
        """A rapid restart must not make a dead turn look alive."""
        script = TurnScript(messages=(ANSWER,), silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=0.4)

        async def scenario() -> None:
            task = asyncio.get_running_loop().create_task(
                runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            )
            await until_started(runner)
            assert client.generation == 1
            # The same thread and the same turn -- from a child that replaced
            # ours. It names a turn that died with the process it ran in.
            deliver(
                client,
                2,
                turn_notification("turn/completed", "thread-1", "turn-1", "completed"),
            )
            with pytest.raises(BridgeTimeout):
                await task
            await client.aclose()

        run(scenario())

    def test_an_event_from_our_own_generation_is_accepted(self, tmp_path: Path) -> None:
        """The other half: the filter must not reject everything."""
        script = TurnScript(messages=(ANSWER,), silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=2.0)

        async def scenario() -> None:
            task = asyncio.get_running_loop().create_task(
                runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            )
            await until_started(runner)
            deliver(
                client,
                1,
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": thread_item(ANSWER, phase="final_answer"),
                    },
                },
            )
            deliver(
                client,
                1,
                turn_notification("turn/completed", "thread-1", "turn-1", "completed"),
            )
            assert (await task).payload == {"ok": True}
            await client.aclose()

        run(scenario())

    def test_an_unattributable_event_is_dropped_rather_than_guessed_at(
        self, tmp_path: Path
    ) -> None:
        """No `turn/started`, so nothing has named the turn when events arrive.

        Every one of them precedes the reply that would have named it, so none
        can be matched and none is accepted -- the turn ends at the deadline
        rather than on somebody's word for which turn this was.
        """
        script = TurnScript(
            messages=(ANSWER,), emit_turn_started=False, events_before_reply=True
        )
        raises(BridgeTimeout, script, tmp_path, turn_timeout=0.2)

    def test_an_unsafe_item_is_never_dropped_as_unattributable(
        self, tmp_path: Path
    ) -> None:
        """Screening items on the thread alone is what makes that safe.

        A `commandExecution` that arrives before anything has named the turn
        must still fail the turn closed, not be filtered out as somebody else's.
        """
        script = TurnScript(
            items=(native_item("commandExecution"), thread_item(ANSWER)),
            emit_turn_started=False,
            events_before_reply=True,
        )
        raises(TurnUnsafe, script, tmp_path, turn_timeout=1.0)


# --- defect 4 ----------------------------------------------------------------


class TestUnsuccessfulExitsInterrupt:
    """Defect 4: a `turn/start` that timed out left the turn running upstream."""

    def test_a_timed_out_start_still_interrupts_the_turn_it_started(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(turn_start_silent=True)
        client, runner, _ = bridge(script, tmp_path, request_timeout=0.2, turn_timeout=5.0)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeTimeout):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                # `turn/started` named the turn; the RPC never answered. The
                # turn is running upstream and has to be stopped anyway.
                assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
            finally:
                await client.aclose()

        run(scenario())

    def test_the_turn_deadline_interrupts(self, tmp_path: Path) -> None:
        script = TurnScript(silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=0.2)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeTimeout):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
            finally:
                await client.aclose()

        run(scenario())

    def test_an_unsafe_event_interrupts(self, tmp_path: Path) -> None:
        script = TurnScript(items=(native_item("mcpToolCall"),))
        raises(TurnUnsafe, script, tmp_path)
        assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]

    def test_an_overflowing_turn_interrupts(self, tmp_path: Path) -> None:
        script = TurnScript(messages=("x" * 50,))
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeProtocolError):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                        max_output_chars=10,
                    )
                assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
            finally:
                await client.aclose()

        run(scenario())

    def test_output_that_fails_validation_interrupts(self, tmp_path: Path) -> None:
        """Nothing is left running because the answer turned out to be wrong."""
        script = TurnScript(messages=(json.dumps({"ok": "not a boolean"}),))
        assert raises(BridgeProtocolError, script, tmp_path).category == "invalid_output"
        assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]

    def test_cancellation_interrupts(self, tmp_path: Path) -> None:
        script = TurnScript(silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=5.0)

        async def scenario() -> None:
            task = asyncio.get_running_loop().create_task(
                runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            )
            await until_started(runner)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
            await client.aclose()

        run(scenario())

    def test_a_dead_generation_is_never_resurrected_to_be_interrupted(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """`client.call` starts a child when there is not one. This must not."""
        caplog.set_level("INFO", logger="vademecum.appserver")
        script = TurnScript(die_after_turn_start=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=1.0, extra_transports=1)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeUnavailable):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert script.interrupts == []
                assert "category=generation_gone" in caplog.text
                # And no second process was started to carry the interrupt.
                assert client.generation == 1
            finally:
                await client.aclose()

        run(scenario())


# --- defect 5 ----------------------------------------------------------------


class TestPreResponseBufferingIsBounded:
    """Defect 5: events arriving before the start reply accumulated unbounded."""

    def test_a_flood_of_messages_before_the_reply_overflows_by_count(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(
            messages=(ANSWER,),
            events_before_reply=True,
            flood=MAX_AGENT_MESSAGES * 4,
            flood_text="{}",
        )
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                failure = None
                with pytest.raises(BridgeProtocolError) as caught:
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                        # Deliberately huge, so only the count cap can fire.
                        max_output_chars=10_000_000,
                    )
                failure = caught.value
                assert failure.category == "output_too_large"
                # Buffering stopped at the cap instead of running to the end of
                # the flood.
                assert runner._messages <= MAX_AGENT_MESSAGES + 1
            finally:
                await client.aclose()

        run(scenario())

    def test_a_flood_of_characters_before_the_reply_overflows_by_size(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(
            messages=(ANSWER,), events_before_reply=True, flood=8, flood_text="x" * 100
        )
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(BridgeProtocolError) as caught:
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                        max_output_chars=150,
                    )
                assert caught.value.category == "output_too_large"
                assert runner._chars <= 150 + 100
            finally:
                await client.aclose()

        run(scenario())

    def test_nothing_is_retained_after_an_outcome_is_decided(self, tmp_path: Path) -> None:
        """A failed turn stops buffering rather than accumulating to the deadline."""
        script = TurnScript(
            items=(native_item("commandExecution"),) + tuple(
                thread_item("x" * 100, item_id=f"m-{index}") for index in range(30)
            )
        )
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(TurnUnsafe):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema=SCHEMA,
                    )
                assert runner._messages == 0
                assert runner._chars == 0
            finally:
                await client.aclose()

        run(scenario())


# --- defect 8 ----------------------------------------------------------------


REJECTED_SCHEMAS: dict[str, dict[str, Any]] = {
    "pattern": {"type": "string", "pattern": "^a+$"},
    "minimum": {"type": "integer", "minimum": 1},
    "format": {"type": "string", "format": "email"},
    "oneOf": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
    "ref": {"$ref": "#/definitions/Thing"},
    "union_type": {"type": ["string", "null"]},
    "unknown_type": {"type": "integerish"},
    "permissive_additional": {"type": "object", "additionalProperties": True},
    "subschema_additional": {"type": "object", "additionalProperties": {"type": "string"}},
    "tuple_items": {"type": "array", "items": [{"type": "string"}]},
    "nested": {
        "type": "object",
        "properties": {"inner": {"type": "string", "minLength": 2}},
    },
    "in_items": {"type": "array", "items": {"type": "string", "pattern": "x"}},
    "empty_enum": {"enum": []},
    "float_bound": {"type": "string", "maxLength": 1.5},
    "required_not_strings": {"type": "object", "required": [1]},
}


class TestTheSchemaMustBeEnforceable:
    """Defect 8: the validator ignored keywords, so an unchecked constraint read
    as a checked one."""

    @pytest.mark.parametrize("name", sorted(REJECTED_SCHEMAS))
    def test_an_unenforceable_schema_is_rejected(self, name: str) -> None:
        from vademecum.appserver.turns import assert_enforceable

        with pytest.raises(UnenforceableSchema):
            assert_enforceable(REJECTED_SCHEMAS[name])

    def test_the_supported_subset_is_accepted(self) -> None:
        from vademecum.appserver.turns import assert_enforceable

        assert_enforceable(SCHEMA)
        assert_enforceable(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["a", "b"],
                "title": "Thing",
                "description": "an annotation constrains nothing",
                "properties": {
                    "a": {"type": "string", "maxLength": 10},
                    "b": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 3,
                        "items": {"type": "string", "enum": ["x", "y"]},
                    },
                },
            }
        )

    def test_every_schema_the_application_actually_sends_is_enforceable(self) -> None:
        """The check has to be a guard rail, not a wall in front of the door."""
        from vademecum.model import schemas as model_schemas
        from vademecum.appserver.turns import assert_enforceable

        for name in (
            "SYNTHESIS_SCHEMA",
            "EVIDENCE_SCHEMA",
            "ASSESSMENT_SCHEMA",
            "GRADING_SCHEMA",
        ):
            assert_enforceable(getattr(model_schemas, name))

    def test_an_unenforceable_schema_is_refused_before_anything_is_sent(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,))
        client, runner, transport = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                with pytest.raises(UnenforceableSchema):
                    await runner.run(
                        instructions="i",
                        developer_instructions="d",
                        prompt="p",
                        output_schema={"type": "string", "pattern": "^x$"},
                    )
                # Not a line on the wire, and not a process either.
                assert transport.started is False
                assert script.threads == []
                assert script.turns == []
            finally:
                await client.aclose()

        run(scenario())

    def test_every_supported_keyword_is_one_the_validator_actually_enforces(self) -> None:
        """The list is only worth having if each name on it does something."""
        from vademecum.appserver.turns import SUPPORTED_KEYWORDS, validate_against

        proofs: dict[str, tuple[dict[str, Any], Any]] = {
            "type": ({"type": "string"}, 1),
            "enum": ({"enum": ["a"]}, "b"),
            "required": ({"type": "object", "required": ["a"]}, {}),
            "properties": ({"properties": {"a": {"type": "string"}}}, {"a": 1}),
            "items": ({"items": {"type": "string"}}, [1]),
            "additionalProperties": ({"additionalProperties": False}, {"a": 1}),
            "maxLength": ({"maxLength": 1}, "ab"),
            "minItems": ({"minItems": 2}, [1]),
            "maxItems": ({"maxItems": 1}, [1, 2]),
        }
        for keyword in SUPPORTED_KEYWORDS - {"title", "description"}:
            schema, rejected = proofs[keyword]
            assert validate_against(rejected, schema) is False, keyword


# --- defect 9(a) -------------------------------------------------------------


PROMPT = "grade this: the learner said something"


class TestTheInputEcho:
    """Defect 9(a): the allowlist refused the lifecycle's echo of our own prompt.

    ``userMessage`` is what the App Server emits when it accepts a
    ``turn/start`` input, so refusing it failed normal turns closed for a reason
    that had nothing to do with safety. It is now allowed -- inert, bounded and
    correlated -- and everything that is not our own input still is not.
    """

    def test_a_normal_turn_carrying_the_echo_succeeds(self, tmp_path: Path) -> None:
        """The reviewer's reproduction: the official lifecycle, start to finish."""
        script = TurnScript(
            items=(
                user_message_item(PROMPT),
                native_item("reasoning", summary=["thinking"]),
                thread_item(ANSWER, phase="final_answer"),
            )
        )
        assert one_turn(script, tmp_path, prompt=PROMPT).payload == {"ok": True}

    def test_the_echo_is_never_the_answer(self, tmp_path: Path) -> None:
        """Even when the prompt it echoes is itself a schema-valid payload."""
        echoed = json.dumps({"ok": False})
        script = TurnScript(
            items=(user_message_item(echoed), thread_item(ANSWER, phase="final_answer"))
        )
        assert one_turn(script, tmp_path, prompt=echoed).payload == {"ok": True}

    def test_an_echo_alone_is_not_a_result(self, tmp_path: Path) -> None:
        """No agent message at all means no answer, whatever the echo said."""
        echoed = json.dumps({"ok": True})
        script = TurnScript(items=(user_message_item(echoed),))
        failure = raises(BridgeProtocolError, script, tmp_path, prompt=echoed)
        assert failure.category == "no_final_message"

    def test_the_echo_is_charged_to_the_retained_budget(self, tmp_path: Path) -> None:
        script = TurnScript(items=(user_message_item(PROMPT), thread_item(ANSWER)))
        failure = raises(
            BridgeProtocolError,
            script,
            tmp_path,
            prompt=PROMPT,
            max_output_chars=len(PROMPT) - 1,
        )
        assert failure.category == "output_too_large"

    def test_an_oversized_echo_fails_closed(self, tmp_path: Path) -> None:
        """A "user" message longer than anything this bridge sent is not ours."""
        script = TurnScript(
            items=(user_message_item(PROMPT + "x" * 10_000), thread_item(ANSWER))
        )
        raises(TurnUnsafe, script, tmp_path, prompt=PROMPT)

    @pytest.mark.parametrize(
        "item",
        [
            user_message_item("something we never sent"),
            user_message_item(PROMPT[:-1]),  # nearly, which is not the same as ours
            user_message_item(PROMPT, "and a second turn's worth"),
            user_message_item(content=[]),
            user_message_item(content=[{"type": "image", "url": "http://example.test/x"}]),
            user_message_item(content=[{"type": "skill", "name": "n", "path": "/p"}]),
            user_message_item(content="not an array"),
            native_item("userMessage"),  # no `content` member at all
        ],
    )
    def test_an_uncorrelated_echo_fails_closed(
        self, tmp_path: Path, item: dict[str, Any]
    ) -> None:
        script = TurnScript(items=(item, thread_item(ANSWER)))
        raises(TurnUnsafe, script, tmp_path, prompt=PROMPT)

    def test_an_uncorrelated_echo_interrupts_and_logs_only_a_category(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(items=(user_message_item("ZZFORGEDZZ"), thread_item(ANSWER)))
        raises(TurnUnsafe, script, tmp_path, prompt=PROMPT)
        assert script.interrupts == [{"threadId": "thread-1", "turnId": "turn-1"}]
        assert "ZZFORGEDZZ" not in caplog.text
        assert "category=uncorrelated_input_echo" in caplog.text

    def test_the_echo_on_item_started_is_screened_too(self, tmp_path: Path) -> None:
        script = TurnScript(
            items=(user_message_item("not the prompt"),), emit_item_completed=False
        )
        raises(TurnUnsafe, script, tmp_path, prompt=PROMPT)

    def test_a_flood_of_echoes_is_bounded_like_any_other_stream(
        self, tmp_path: Path
    ) -> None:
        """An empty prompt costs no characters, so the count cap is what holds."""
        script = TurnScript(
            items=tuple(
                user_message_item("", item_id=f"echo-{index}")
                for index in range(MAX_AGENT_MESSAGES * 2)
            )
            + (thread_item(ANSWER),)
        )
        failure = raises(BridgeProtocolError, script, tmp_path, prompt="")
        assert failure.category == "output_too_large"


# --- defect 9(b) -------------------------------------------------------------


class TestAStaleGenerationIsNeverAnAnswer:
    """Defect 9(b): a delayed generation-1 answer was accepted after a restart.

    The generation was only ever compared against the *event*. An event that
    genuinely carried generation 1 therefore passed, even once the client had
    moved to generation 2 -- so a turn that died with its child could still
    produce a result.
    """

    def test_an_answer_delivered_after_the_child_was_replaced_is_discarded(
        self, tmp_path: Path
    ) -> None:
        script = TurnScript(messages=(ANSWER,), silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=5.0, extra_transports=1)

        async def scenario() -> None:
            task = asyncio.get_running_loop().create_task(
                runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            )
            await until_started(runner)
            assert client.generation == 1
            # The child is replaced. Our turn died with it.
            await client.restart()
            assert client.generation == 2
            # ...and only now does generation 1's answer turn up.
            deliver(
                client,
                1,
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": thread_item(ANSWER, phase="final_answer"),
                    },
                },
            )
            deliver(
                client,
                1,
                turn_notification("turn/completed", "thread-1", "turn-1", "completed"),
            )
            # Spelled out rather than left to `pytest.raises`: the escape was
            # that this *returned*, and the payload it returned is what has to
            # be named in the failure.
            try:
                returned = await task
            except BridgeUnavailable as exc:
                assert exc.category == "generation_changed"
            else:
                raise AssertionError(
                    f"a generation-1 answer was returned: {returned.payload}"
                )
            # It was not read, either.
            assert runner._final_answer is None
            await client.aclose()

        run(scenario())

    def test_the_replacement_child_is_never_interrupted(self, tmp_path: Path) -> None:
        """The turn to stop is in a process that no longer exists."""
        script = TurnScript(messages=(ANSWER,), silent=True)
        client, runner, _ = bridge(script, tmp_path, turn_timeout=5.0, extra_transports=1)

        async def scenario() -> None:
            task = asyncio.get_running_loop().create_task(
                runner.run(
                    instructions="i",
                    developer_instructions="d",
                    prompt="p",
                    output_schema=SCHEMA,
                )
            )
            await until_started(runner)
            await client.restart()
            with pytest.raises(BridgeUnavailable):
                await task
            # Generation 2 was asked to stop nothing.
            assert script.interrupts == []
            await client.aclose()

        run(scenario())

    def test_a_validated_payload_is_still_checked_before_it_is_returned(
        self, tmp_path: Path
    ) -> None:
        """The second half of the rule: while waiting *and* before returning.

        Driven directly, because the window between an outcome being decided and
        a payload being returned is too small to hit reliably from the outside --
        and it is exactly the window a restart can land in.
        """
        from vademecum.appserver.turns import _Completed

        script = TurnScript(messages=(ANSWER,))
        client, runner, _ = bridge(script, tmp_path)

        async def scenario() -> None:
            try:
                # A turn that got as far as a valid, validated final answer...
                runner._reset(1000, "p")
                runner._thread_id = "thread-1"
                runner._turn_id = "turn-1"
                runner._final_answer = ANSWER
                runner._generation = client.generation
                assert (
                    await runner._finish(
                        _Completed(status="completed", error_category=None),
                        SCHEMA,
                        "cid",
                        0.0,
                    )
                ).payload == {"ok": True}

                # ...and the same turn, whose child has since been replaced.
                runner._final_answer = ANSWER
                runner._generation = client.generation - 1
                with pytest.raises(BridgeUnavailable) as caught:
                    await runner._finish(
                        _Completed(status="completed", error_category=None),
                        SCHEMA,
                        "cid",
                        0.0,
                    )
                assert caught.value.category == "generation_changed"
            finally:
                await client.aclose()

        run(scenario())


# --- redaction, which none of the above may weaken ---------------------------


class TestNothingUpstreamIsLogged:
    def test_no_prompt_answer_or_upstream_message_reaches_a_log(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(
            messages=("ZZLEARNERANSWERZZ",),
            status="failed",
            error=turn_error("unauthorized", "ZZUPSTREAMTEXTZZ"),
        )
        raises(TurnRefused, script, tmp_path)
        assert "ZZLEARNERANSWERZZ" not in caplog.text
        assert "ZZUPSTREAMTEXTZZ" not in caplog.text
        assert "ZZPROMPTZZ" not in caplog.text
        assert "appserver_turn" in caplog.text
        assert "category=signed_out" in caplog.text

    def test_an_unsafe_event_logs_a_category_and_a_method_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        script = TurnScript(
            items=(native_item("commandExecution", command=["rm", "-rf", "ZZSECRETZZ"]),)
        )
        raises(TurnUnsafe, script, tmp_path)
        assert "ZZSECRETZZ" not in caplog.text
        assert "appserver_turn_unsafe_event" in caplog.text
        assert "category=unsafe_item" in caplog.text
