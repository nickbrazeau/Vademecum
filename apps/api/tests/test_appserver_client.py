"""Lifecycle, correlation and supervision, against a scripted transport.

No process is started anywhere in this file. The autouse ``no_real_codex``
fixture makes that structural rather than a matter of care.
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from conftest import run
from fake_appserver import (
    AccountScript,
    Error,
    NoReply,
    Raw,
    ScriptedTransport,
    factory,
)

from vademecum.appserver import protocol
from vademecum.appserver import client as client_module
from vademecum.appserver.client import SERVER_REQUEST_POLICY, AppServerClient, Decline, Refuse
from vademecum.appserver.errors import (
    BridgeProtocolError,
    BridgeRefused,
    BridgeTimeout,
    BridgeUnavailable,
)

READ = protocol.ACCOUNT_READ
READ_PARAMS = protocol.account_read_params()


def capture_sessions(monkeypatch: pytest.MonkeyPatch) -> list[client_module._Session]:
    """Keep every session the client builds, published or not.

    The sessions that matter here are the ones ``_start`` gives up on, which by
    definition never reach ``client._session``. Holding them is the only way to
    assert that their pending map is empty and their tasks are finished rather
    than merely unreachable.
    """
    made: list[client_module._Session] = []
    original = client_module._Session

    def record(**kwargs: object) -> client_module._Session:
        session = original(**kwargs)  # type: ignore[arg-type]
        made.append(session)
        return session

    monkeypatch.setattr(client_module, "_Session", record)
    return made


def live_tasks() -> int:
    return len([task for task in asyncio.all_tasks() if task is not asyncio.current_task()])


def make_client(*transports: ScriptedTransport, **options: object) -> AppServerClient:
    return AppServerClient(
        factory(*transports),
        client_name="Vademecum",
        client_title="Vademecum",
        client_version="0.1.0",
        **options,  # type: ignore[arg-type]
    )


class TestStartup:
    def test_nothing_starts_until_the_bridge_is_used(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)
        assert transport.started is False
        assert client.running is False
        assert client.initialize_count == 0

    def test_initialize_comes_first_and_initialized_comes_next(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            await client.aclose()

        run(scenario())
        methods = [message["method"] for message in transport.sent]
        assert methods == ["initialize", "initialized", "account/read"]
        # The notification carries no id; the request that preceded it does.
        assert "id" in transport.sent[0]
        assert "id" not in transport.sent[1]

    def test_one_process_is_reused_across_calls(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            await client.call(READ, READ_PARAMS)
            await client.call(protocol.ACCOUNT_RATE_LIMITS_READ)
            await client.aclose()

        run(scenario())
        assert client.initialize_count == 1
        assert len(transport.requests("initialize")) == 1
        assert client.generation == 1

    def test_concurrent_first_calls_initialise_exactly_once(self) -> None:
        """The lock is what makes this true; without it there are two children."""
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await asyncio.gather(*(client.call(READ, READ_PARAMS) for _ in range(8)))
            await client.aclose()

        run(scenario())
        assert len(transport.requests("initialize")) == 1
        assert len(transport.notifications("initialized")) == 1
        assert len(transport.requests(READ)) == 8

    def test_a_transport_that_cannot_start_fails_closed(self) -> None:
        transport = ScriptedTransport(start_error=BridgeUnavailable("codex_not_found"))
        client = make_client(transport)

        async def scenario() -> None:
            with pytest.raises(BridgeUnavailable) as caught:
                await client.call(READ, READ_PARAMS)
            assert caught.value.category == "codex_not_found"
            assert client.running is False
            await client.aclose()

        run(scenario())

    def test_a_failed_initialize_tears_the_process_down(self) -> None:
        script = AccountScript(overrides={"initialize": Error(-32603, "no")})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport)

        async def scenario() -> None:
            with pytest.raises(BridgeRefused):
                await client.call(READ, READ_PARAMS)
            assert transport.stopped is True
            assert client.running is False
            assert client.initialize_count == 0
            await client.aclose()

        run(scenario())

    def test_a_cancelled_startup_leaves_no_process_or_task_behind(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The window between spawning the child and publishing the session.

        The caller gives up -- a closed tab, a shutdown -- while `initialize`
        is still outstanding. Nothing holds the session yet, so if `_start` does
        not tear it down here, the child and its two reader tasks belong to
        nobody and no later caller can find them in order to reap them.
        """
        script = AccountScript(overrides={"initialize": NoReply})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport, startup_timeout=30)
        sessions = capture_sessions(monkeypatch)

        async def scenario() -> int:
            starting = asyncio.ensure_future(client.call(READ, READ_PARAMS))
            for _ in range(4):
                await asyncio.sleep(0)
            assert transport.started is True
            assert len(transport.requests("initialize")) == 1

            starting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await starting

            assert client.running is False
            assert transport.stopped is True
            assert len(sessions) == 1
            session = sessions[0]
            assert session.pending == {}
            assert session.tasks == set()
            assert session.reader is not None and session.reader.done()
            assert session.stderr is not None and session.stderr.done()
            assert client.initialize_count == 0
            await client.aclose()
            return live_tasks()

        assert run(scenario()) == 0

    def test_an_unexpected_startup_failure_tears_the_process_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not every failure is a category yet. This one is turned into one.

        A transport that raises something the bridge has no name for used to
        escape `_start` with the child running and both reader tasks alive.
        """
        transport = ScriptedTransport(responder=AccountScript())
        transport.write_error = RuntimeError("something the bridge has no name for")
        client = make_client(transport)
        sessions = capture_sessions(monkeypatch)

        async def scenario() -> int:
            with pytest.raises(BridgeUnavailable) as caught:
                await client.call(READ, READ_PARAMS)
            assert caught.value.category == "startup_failed"

            assert client.running is False
            assert transport.stopped is True
            assert len(sessions) == 1
            session = sessions[0]
            assert session.pending == {}
            assert session.tasks == set()
            assert session.reader is not None and session.reader.done()
            assert session.stderr is not None and session.stderr.done()
            await client.aclose()
            return live_tasks()

        assert run(scenario()) == 0

    def test_an_unexpected_startup_failure_logs_no_exception_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        transport.write_error = RuntimeError("ZZQXMARKERZZ from the child")
        client = make_client(transport)

        async def scenario() -> None:
            with pytest.raises(BridgeUnavailable):
                await client.call(READ, READ_PARAMS)
            await client.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        assert "ZZQXMARKERZZ" not in caplog.text
        assert "appserver_initialize_failed" in caplog.text
        assert "category=startup_failed" in caplog.text

    def test_a_method_this_client_does_not_own_is_refused_locally(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            # A method that exists upstream but that this client does not send.
            # `thread/start` used to serve here and no longer can: the turn
            # surface owns it now.
            assert "thread/resume" not in protocol.CLIENT_REQUEST_METHODS
            with pytest.raises(BridgeProtocolError):
                await client.call("thread/resume", {})
            # Nothing was started: refusing locally means not opening a process
            # to send something the client has no business sending.
            assert transport.started is False
            await client.aclose()

        run(scenario())


class TestCorrelation:
    def test_replies_are_matched_by_id_not_by_order(self) -> None:
        pending: list[dict] = []

        def hold(transport: ScriptedTransport, message: dict) -> object:
            if message["method"] == READ:
                pending.append(message)
                return NoReply
            return AccountScript()._base(message["method"])

        script = AccountScript(overrides={READ: hold})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            first = asyncio.ensure_future(client.call(READ, {"refreshToken": False, "n": 1}))
            second = asyncio.ensure_future(client.call(READ, {"refreshToken": False, "n": 2}))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert len(pending) == 2
            # Answered back to front.
            transport.emit({"id": pending[1]["id"], "result": {"which": "second"}})
            transport.emit({"id": pending[0]["id"], "result": {"which": "first"}})
            assert await first == {"which": "first"}
            assert await second == {"which": "second"}
            await client.aclose()

        run(scenario())

    def test_a_string_id_does_not_answer_an_integer_request(self) -> None:
        """`"1"` is not `1`. Answering the wrong request is worse than timing out."""
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport, request_timeout=0.05)

        async def scenario() -> None:
            # `initialize` is answered normally, so the process comes up; only
            # then does the server start stringifying the ids it echoes.
            await client.ensure_started()
            assert isinstance(transport.responder, AccountScript)
            transport.responder.echo_id_as_string = True
            with pytest.raises(BridgeTimeout):
                await client.call(READ, READ_PARAMS)
            assert client.counters()["unmatched_replies"] == 1
            assert client.counters()["pending"] == 0
            await client.aclose()

        run(scenario())

    def test_a_reply_to_nothing_is_counted_and_dropped(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            transport.emit({"id": 9999, "result": {"unexpected": True}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert client.counters()["unmatched_replies"] == 1
            # And the connection still works.
            assert await client.call(READ, READ_PARAMS) is not None
            await client.aclose()

        run(scenario())

    def test_an_error_reply_becomes_a_category_without_the_servers_words(self) -> None:
        script = AccountScript(overrides={READ: Error(-32601, "MODEL SECRET DETAIL")})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport)

        async def scenario() -> None:
            with pytest.raises(BridgeRefused) as caught:
                await client.call(READ, READ_PARAMS)
            assert caught.value.code == -32601
            assert caught.value.category == "method_not_found"
            assert "SECRET" not in str(caught.value)
            await client.aclose()

        run(scenario())


class TestNotifications:
    def test_observed_notifications_reach_the_handler(self) -> None:
        seen: list[tuple[str, object]] = []
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport, on_notification=lambda m, p: seen.append((m, p)))

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"method": "account/updated", "params": {"planType": "pro"}})
            transport.emit({"method": "account/rateLimits/updated", "params": {"rateLimits": {}}})
            transport.emit({"method": "account/login/completed", "params": {"success": True}})
            transport.emit({"method": "thread/started", "params": {"threadId": "t1"}})
            # Not observed at all: a `dance/party` is counted and dropped.
            transport.emit({"method": "dance/party", "params": {}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert [name for name, _ in seen] == [
                "account/updated",
                "account/rateLimits/updated",
                "account/login/completed",
                # The turn surface observes this one now, and `on_notification`
                # sees everything the client observes.
                "thread/started",
            ]
            await client.aclose()

        run(scenario())

    def test_a_handler_that_raises_does_not_stop_the_read_loop(self) -> None:
        def explode(method: str, params: object) -> None:
            raise RuntimeError("the payload was " + repr(params))

        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport, on_notification=explode)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"method": "account/updated", "params": {"planType": "pro"}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert await client.call(READ, READ_PARAMS) is not None
            await client.aclose()

        run(scenario())

    def test_a_subscriber_is_told_which_generation_an_event_came_from(self) -> None:
        """Defect 3's mechanism, at the layer that owns it.

        Without the generation a subscriber cannot tell an event from the child
        it is talking to from one that crossed a restart.
        """
        seen: list[tuple[int, str]] = []
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(responder=AccountScript())
        client = make_client(first, second)

        async def scenario() -> None:
            await client.ensure_started()
            client.subscribe(lambda generation, method, params: seen.append((generation, method)))
            first.emit({"method": "account/updated", "params": {}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await client.restart()
            second.emit({"method": "account/updated", "params": {}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert seen == [(1, "account/updated"), (2, "account/updated")]
            await client.aclose()

        run(scenario())

    def test_a_subscriber_that_raises_does_not_stop_the_others(self) -> None:
        seen: list[str] = []
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        def explode(generation: int, method: str, params: object) -> None:
            raise RuntimeError("the payload was " + repr(params))

        async def scenario() -> None:
            await client.ensure_started()
            client.subscribe(explode)
            client.subscribe(lambda generation, method, params: seen.append(method))
            transport.emit({"method": "account/updated", "params": {"planType": "pro"}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert seen == ["account/updated"]
            await client.aclose()

        run(scenario())


class TestServerRequests:
    def test_every_server_request_is_answered_exactly_once(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            for index, method in enumerate(sorted(SERVER_REQUEST_POLICY)):
                transport.emit({"id": f"srv-{index}", "method": method, "params": {}})
            for _ in range(10):
                await asyncio.sleep(0)
            replies = transport.replies()
            assert len(replies) == len(SERVER_REQUEST_POLICY)
            assert len({reply["id"] for reply in replies}) == len(SERVER_REQUEST_POLICY)
            await client.aclose()

        run(scenario())

    def test_approvals_are_denied_and_stop_the_turn(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit(
                {"id": 1, "method": "item/commandExecution/requestApproval", "params": {}}
            )
            transport.emit({"id": 2, "method": "execCommandApproval", "params": {}})
            for _ in range(6):
                await asyncio.sleep(0)
            replies = {reply["id"]: reply for reply in transport.replies()}
            assert replies[1]["result"] == {"decision": "cancel"}
            assert replies[2]["result"] == {"decision": "abort"}
            await client.aclose()

        run(scenario())

    @pytest.mark.parametrize(
        "method",
        [
            "item/tool/call",
            "item/tool/requestUserInput",
            "item/permissions/requestApproval",
            "mcpServer/elicitation/request",
            "account/chatgptAuthTokens/refresh",
            "attestation/generate",
        ],
    )
    def test_capabilities_this_slice_has_no_business_with_are_refused(self, method: str) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"id": "x", "method": method, "params": {"anything": True}})
            for _ in range(6):
                await asyncio.sleep(0)
            reply = transport.replies()[0]
            assert reply["error"]["code"] == protocol.METHOD_NOT_FOUND
            assert "result" not in reply
            await client.aclose()

        run(scenario())

    def test_a_token_refresh_request_is_answered_with_no_token(self) -> None:
        """Codex holds the credential. This client has none to hand back."""
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"id": 5, "method": "account/chatgptAuthTokens/refresh", "params": {}})
            for _ in range(6):
                await asyncio.sleep(0)
            reply = transport.replies()[0]
            assert "accessToken" not in str(reply)
            assert reply["error"]["code"] == protocol.METHOD_NOT_FOUND
            await client.aclose()

        run(scenario())

    def test_an_unknown_server_request_is_still_answered(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"id": 42, "method": "something/invented", "params": {}})
            for _ in range(6):
                await asyncio.sleep(0)
            assert transport.replies()[0]["id"] == 42
            assert transport.replies()[0]["error"]["code"] == protocol.METHOD_NOT_FOUND
            await client.aclose()

        run(scenario())

    def test_an_id_is_echoed_with_its_type_intact(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit({"id": "abc", "method": "item/tool/call", "params": {}})
            transport.emit({"id": 17, "method": "item/tool/call", "params": {}})
            for _ in range(8):
                await asyncio.sleep(0)
            ids = [reply["id"] for reply in transport.replies()]
            assert ids == ["abc", 17]
            await client.aclose()

        run(scenario())

    def test_the_policy_table_answers_every_request_with_one_reply_shape(self) -> None:
        for method, policy in SERVER_REQUEST_POLICY.items():
            assert isinstance(policy, (Decline, Refuse)), method
            if isinstance(policy, Decline):
                assert policy.decision in {"cancel", "abort"}


class TestMalformedInput:
    @pytest.mark.parametrize(
        "line",
        [
            "this is not json",
            "[1,2,3]",
            '{"id": 1}',
            '{"id": 1, "error": {"message": "no code"}}',
            # A boolean is not a code: `isinstance(True, int)` would otherwise
            # turn this into a refusal with the code 1.
            '{"id": 1, "error": {"code": true, "message": "boom"}}',
            '{"method": ""}',
        ],
    )
    def test_a_bad_line_is_counted_and_the_connection_survives(self, line: str) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit(line)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert client.counters()["malformed"] == 1
            assert await client.call(READ, READ_PARAMS) is not None
            await client.aclose()

        run(scenario())

    def test_a_malformed_line_is_logged_as_a_category_not_as_content(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)
        marker = "ZZQXMARKERZZ"

        async def scenario() -> None:
            await client.ensure_started()
            transport.emit(f'{{"id": 1, "oops": "{marker}"}}')
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await client.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        assert marker not in caplog.text
        assert "appserver_malformed_message" in caplog.text
        assert "reply_without_result_or_error" in caplog.text

    def test_a_result_that_is_not_an_object_is_still_delivered(self) -> None:
        """Shape validation belongs to the facade, not to the framing."""
        script = AccountScript(overrides={READ: "a bare string"})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport)

        async def scenario() -> None:
            assert await client.call(READ, READ_PARAMS) == "a bare string"
            await client.aclose()

        run(scenario())

    def test_a_raw_line_with_a_duplicate_id_resolves_once(self) -> None:
        script = AccountScript(
            overrides={READ: lambda transport, message: Raw(
                '{"id": %s, "result": {"first": true}}\n' % message["id"]
            )}
        )
        transport = ScriptedTransport(responder=script)
        client = make_client(transport)

        async def scenario() -> None:
            assert await client.call(READ, READ_PARAMS) == {"first": True}
            transport.emit({"id": 2, "result": {"second": True}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert client.counters()["unmatched_replies"] == 1
            await client.aclose()

        run(scenario())


class TestTimeoutAndCancellation:
    def test_a_silent_server_times_out_and_releases_the_slot(self) -> None:
        script = AccountScript(overrides={READ: NoReply})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport, request_timeout=0.05)

        async def scenario() -> None:
            with pytest.raises(BridgeTimeout):
                await client.call(READ, READ_PARAMS)
            assert client.counters()["pending"] == 0
            await client.aclose()

        run(scenario())

    def test_a_slow_start_times_out_and_stops_the_process(self) -> None:
        script = AccountScript(overrides={"initialize": NoReply})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport, startup_timeout=0.05)

        async def scenario() -> None:
            with pytest.raises(BridgeTimeout):
                await client.call(READ, READ_PARAMS)
            assert transport.stopped is True
            await client.aclose()

        run(scenario())

    def test_a_cancelled_caller_leaves_nothing_pending(self) -> None:
        script = AccountScript(overrides={READ: NoReply})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport, request_timeout=5)

        async def scenario() -> None:
            await client.ensure_started()
            waiting = asyncio.ensure_future(client.call(READ, READ_PARAMS))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert client.counters()["pending"] == 1
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting
            assert client.counters()["pending"] == 0
            # A reply that arrives after the caller gave up is counted, not
            # applied to somebody else's request.
            transport.emit({"id": 2, "result": {"late": True}})
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert client.counters()["unmatched_replies"] == 1
            await client.aclose()

        run(scenario())


class TestProcessDeath:
    def test_a_dead_child_fails_everything_waiting(self) -> None:
        script = AccountScript(overrides={READ: NoReply})
        transport = ScriptedTransport(responder=script)
        client = make_client(transport, request_timeout=5)

        async def scenario() -> None:
            await client.ensure_started()
            waiting = asyncio.ensure_future(client.call(READ, READ_PARAMS))
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            transport.die("signal:9")
            with pytest.raises(BridgeUnavailable) as caught:
                await waiting
            assert caught.value.category == "process_exited"
            assert client.running is False
            # Reaped where it died, without waiting for another caller: a page
            # nobody has open is the normal state of this bridge.
            assert transport.stopped is True
            assert client.exit_status() == "signal:9"
            await client.aclose()

        run(scenario())

    def test_the_next_call_starts_and_initialises_a_fresh_child(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(responder=AccountScript())
        client = make_client(first, second)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            first.die()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert await client.call(READ, READ_PARAMS) is not None
            await client.aclose()

        run(scenario())
        assert client.generation == 2
        assert client.initialize_count == 2
        assert len(second.requests("initialize")) == 1
        assert len(second.notifications("initialized")) == 1
        assert first.stopped is True

    def test_a_write_to_a_broken_pipe_is_a_category(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(responder=AccountScript())
        client = make_client(first, second)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            first.write_error = BridgeUnavailable("write_failed")
            with pytest.raises(BridgeUnavailable) as caught:
                await client.call(READ, READ_PARAMS)
            assert caught.value.category == "write_failed"
            await client.aclose()

        run(scenario())


class TestRestart:
    def test_restart_replaces_the_process_and_reinitialises(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(responder=AccountScript())
        client = make_client(first, second)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            await client.restart()
            await client.call(READ, READ_PARAMS)
            await client.aclose()

        run(scenario())
        assert first.stopped is True
        assert second.stopped is True
        assert client.initialize_count == 2
        assert len(second.requests("initialize")) == 1
        assert len(second.requests(READ)) == 1

    def test_a_restart_that_cannot_start_leaves_nothing_half_alive(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(start_error=BridgeUnavailable("spawn_failed"))
        client = make_client(first, second)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            with pytest.raises(BridgeUnavailable):
                await client.restart()
            assert client.running is False
            assert first.stopped is True
            await client.aclose()

        run(scenario())

    def test_a_closed_client_refuses_rather_than_restarting_itself(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.call(READ, READ_PARAMS)
            await client.aclose()
            with pytest.raises(BridgeUnavailable) as caught:
                await client.call(READ, READ_PARAMS)
            assert caught.value.category == "closed"

        run(scenario())


class TestStderrAndTaskCleanup:
    def test_stderr_is_drained_continuously_and_only_counted(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)
        secret = b"panicked while handling ZZQXMARKERZZ\n"

        async def scenario() -> None:
            await client.ensure_started()
            for _ in range(120):
                transport.emit_stderr(secret)
            for _ in range(300):
                await asyncio.sleep(0)
            assert client.counters()["stderr_lines"] == 120
            await client.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        assert "ZZQXMARKERZZ" not in caplog.text
        assert "panicked" not in caplog.text
        # The count is reported; the content is not.
        assert "appserver_stderr bytes=" in caplog.text
        assert "lines=100" in caplog.text

    def test_closing_leaves_no_task_behind(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> int:
            await client.ensure_started()
            transport.emit({"id": "srv", "method": "item/tool/call", "params": {}})
            for _ in range(4):
                await asyncio.sleep(0)
            await client.aclose()
            await asyncio.sleep(0)
            return len([task for task in asyncio.all_tasks() if task is not asyncio.current_task()])

        assert run(scenario()) == 0
        assert transport.stopped is True

    def test_closing_twice_is_harmless(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        client = make_client(transport)

        async def scenario() -> None:
            await client.ensure_started()
            await client.aclose()
            await client.aclose()

        run(scenario())

    def test_closing_something_never_started_is_harmless(self) -> None:
        client = make_client(ScriptedTransport(responder=AccountScript()))
        run(client.aclose())
