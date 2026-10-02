"""The account facade: what it shows, what it refuses, and what it drops."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
from conftest import run
from fake_appserver import (
    DEVICE_LOGIN_RESULT,
    AccountScript,
    Error,
    NoReply,
    ScriptedTransport,
    api_key_account,
    chatgpt_account,
    factory,
    rate_limits,
)

from vademecum.appserver import protocol
from vademecum.appserver.account import (
    ModelBridge,
    merge_snapshot,
    sanitise_rate_limits,
)
from vademecum.appserver.errors import BridgeTimeout, BridgeUnavailable, LoginNotSupported

EMAIL = "owner@example.test"


def numbered_logins():
    """A server that hands out a new device code each time, as a real one does."""
    counter = {"n": 0}

    def reply(transport: ScriptedTransport, message: dict) -> dict:
        counter["n"] += 1
        return dict(DEVICE_LOGIN_RESULT, loginId=f"login-{counter['n']}")

    return reply


def make_bridge(*transports: ScriptedTransport, **options: object) -> ModelBridge:
    return ModelBridge(
        codex_path=Path("/nonexistent/codex"),
        working_directory=Path("/tmp"),
        client_version="0.1.0",
        transport_factory=factory(*transports),
        **options,  # type: ignore[arg-type]
    )


class TestStatus:
    def test_signed_out_when_there_is_no_account(self) -> None:
        bridge = make_bridge(ScriptedTransport(responder=AccountScript()))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "signed_out"
        assert status.signed_in is False
        assert status.plan is None
        assert status.rate_limits is None
        assert "ChatGPT" in status.detail
        assert "API key" in status.detail
        assert "build learning material" in status.detail
        assert "future" not in status.detail

    def test_signed_in_reports_a_plan_and_never_the_email(self) -> None:
        script = AccountScript(account=chatgpt_account(plan="pro", email=EMAIL))
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "signed_in"
        assert status.signed_in is True
        assert status.plan == "pro"
        assert EMAIL not in repr(status)
        assert "Build and Grade" in status.detail
        assert "status check sends no study content" in status.detail
        assert "this version has none" not in status.detail

    def test_an_api_key_account_is_reported_as_not_usable(self) -> None:
        """AGENTS.md boundary 4: no silent fallback to API-key billing."""
        script = AccountScript(account=api_key_account())
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "signed_out"
        assert status.signed_in is False
        assert status.reason == "credential_unsupported"
        assert "no API key is used" in status.detail

    def test_a_signed_out_account_is_not_asked_for_rate_limits(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.status()
            await bridge.aclose()

        run(scenario())
        assert transport.requests(protocol.ACCOUNT_RATE_LIMITS_READ) == []

    def test_rate_limit_windows_are_shown_without_balances_or_ids(self) -> None:
        script = AccountScript(
            account=chatgpt_account(), limits=rate_limits(primary_used=40, secondary_used=70)
        )
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "signed_in"
        assert status.rate_limits is not None
        assert status.rate_limits.primary is not None
        assert status.rate_limits.primary.used_percent == 40
        assert status.rate_limits.primary.window_minutes == 300
        assert status.rate_limits.primary.resets_at == "2027-01-15T08:00:00Z"
        assert status.rate_limits.secondary is not None
        assert status.rate_limits.secondary.used_percent == 70
        rendered = repr(status)
        for absent in ("12.34", "codex", "Codex weekly", "credits"):
            assert absent not in rendered

    def test_a_reached_limit_becomes_the_rate_limited_state(self) -> None:
        script = AccountScript(
            account=chatgpt_account(),
            limits=rate_limits(primary_used=100, reached="rate_limit_reached"),
        )
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "rate_limited"
        assert status.signed_in is True
        assert status.rate_limits is not None
        assert status.rate_limits.limited is True
        assert status.rate_limits.limit_reason == "rate_limit_reached"

    def test_missing_rate_limits_still_leave_a_signed_in_state(self) -> None:
        script = AccountScript(
            account=chatgpt_account(),
            overrides={protocol.ACCOUNT_RATE_LIMITS_READ: Error(-32603, "unavailable")},
        )
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "signed_in"
        assert status.rate_limits is None

    def test_a_bridge_that_cannot_start_is_a_state_not_an_exception(self) -> None:
        bridge = make_bridge(
            ScriptedTransport(start_error=BridgeUnavailable("codex_not_found")),
        )

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "unavailable"
        assert status.reason == "codex_not_found"
        assert "Codex is not installed" in status.detail

    def test_a_timeout_is_an_unavailable_state_and_is_not_retried(self) -> None:
        """A repeated request may have been acted on; a read that hangs is not safe to repeat."""
        script = AccountScript(overrides={protocol.ACCOUNT_READ: NoReply})
        transport = ScriptedTransport(responder=script)
        bridge = ModelBridge(
            codex_path=Path("/nonexistent/codex"),
            working_directory=Path("/tmp"),
            client_version="0.1.0",
            request_timeout=0.05,
            transport_factory=factory(transport),
        )

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "unavailable"
        assert status.reason == "timeout"
        assert len(transport.requests(protocol.ACCOUNT_READ)) == 1

    def test_a_nonsense_reply_is_a_protocol_state(self) -> None:
        script = AccountScript(overrides={protocol.ACCOUNT_READ: "not an object"})
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario():
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "unavailable"
        assert status.reason == "protocol"


class TestRetry:
    def test_a_dead_process_is_retried_once_and_only_once(self) -> None:
        """The retry does not restart anything: the lazy start replaces the child."""
        first = ScriptedTransport(responder=AccountScript(account=chatgpt_account()))
        second = ScriptedTransport(responder=AccountScript(account=chatgpt_account()))
        bridge = make_bridge(first, second)

        async def scenario():
            await bridge.status()  # start on the first transport
            first.write_error = BridgeUnavailable("write_failed")
            first.die()
            await asyncio.sleep(0)
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state in {"signed_in", "rate_limited"}
        assert len(second.requests("initialize")) == 1

    def test_a_second_failure_gives_up_rather_than_looping(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(start_error=BridgeUnavailable("spawn_failed"))
        third = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(first, second, third)

        async def scenario():
            await bridge.status()
            first.die()
            await asyncio.sleep(0)
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.state == "unavailable"
        assert status.reason == "spawn_failed"
        # The third transport was never reached: one retry, not a loop.
        assert third.started is False


class TestDeviceLogin:
    def test_sign_in_asks_only_for_chatgpt_device_code(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario():
            login = await bridge.start_device_login()
            await bridge.aclose()
            return login

        login = run(scenario())
        assert login.verification_url == "https://example.test/device"
        assert login.user_code == "WXYZ-1234"
        assert login.login_id == "login-abc123"
        sent = transport.requests(protocol.ACCOUNT_LOGIN_START)
        assert len(sent) == 1
        assert sent[0]["params"] == {"type": "chatgptDeviceCode"}

    def test_a_login_answered_with_another_variant_is_refused(self) -> None:
        script = AccountScript(login_result={"type": "apiKey"})
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario() -> None:
            with pytest.raises(LoginNotSupported):
                await bridge.start_device_login()
            assert bridge.login_pending is False
            await bridge.aclose()

        run(scenario())

    def test_the_code_and_url_are_never_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        bridge = make_bridge(ScriptedTransport(responder=AccountScript()))

        async def scenario() -> None:
            await bridge.start_device_login()
            await bridge.status()
            await bridge.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        for secret in ("WXYZ-1234", "login-abc123", "example.test/device", EMAIL):
            assert secret not in caplog.text
        assert "model_login_started" in caplog.text

    def test_a_pending_login_shows_in_status_without_the_code(self) -> None:
        bridge = make_bridge(ScriptedTransport(responder=AccountScript()))

        async def scenario():
            await bridge.start_device_login()
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.login_pending is True
        rendered = repr(status)
        assert "WXYZ-1234" not in rendered
        assert "login-abc123" not in rendered

    def test_cancelling_sends_the_login_id_the_browser_never_had_to_keep(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario():
            await bridge.start_device_login()
            outcome = await bridge.cancel_login()
            await bridge.aclose()
            return outcome

        assert run(scenario()) == "canceled"
        cancel = transport.requests(protocol.ACCOUNT_LOGIN_CANCEL)
        assert cancel[0]["params"] == {"loginId": "login-abc123"}
        assert bridge.login_pending is False

    def test_cancelling_with_nothing_pending_says_so(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario():
            outcome = await bridge.cancel_login()
            await bridge.aclose()
            return outcome

        assert run(scenario()) == "nothing_pending"
        # Nothing was started to answer a question that has a local answer.
        assert transport.started is False

    def test_a_failed_cancel_still_forgets_the_pending_login(self) -> None:
        script = AccountScript(
            overrides={protocol.ACCOUNT_LOGIN_CANCEL: Error(-32603, "boom")}
        )
        bridge = make_bridge(ScriptedTransport(responder=script))

        async def scenario() -> None:
            await bridge.start_device_login()
            with pytest.raises(Exception):
                await bridge.cancel_login()
            assert bridge.login_pending is False
            await bridge.aclose()

        run(scenario())

    def test_a_completed_login_notification_clears_the_pending_state(self) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.start_device_login()
            assert bridge.login_pending is True
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {"success": True, "loginId": "login-abc123"},
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert bridge.login_pending is False
            await bridge.aclose()

        run(scenario())

    def test_a_failed_login_notification_keeps_no_error_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.start_device_login()
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {
                        "success": False,
                        "loginId": "login-abc123",
                        "error": "ZZQXMARKERZZ upstream detail",
                    },
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await bridge.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        assert "ZZQXMARKERZZ" not in caplog.text
        assert bridge.login_pending is False

    def test_a_completion_for_a_different_login_leaves_this_one_pending(self) -> None:
        """Correlation, not position: an id that is not ours clears nothing."""
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.start_device_login()
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {"success": True, "loginId": "login-somebody-else"},
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert bridge.login_pending is True
            await bridge.aclose()

        run(scenario())

    def test_a_stale_completion_does_not_clear_a_newer_sign_in(self) -> None:
        """The failure this exists to prevent.

        A sign-in is cancelled and another started. The first one completing
        afterwards must not dismiss the code the owner is currently looking at,
        or they are left with a live code and an interface that says there is
        nothing to finish.
        """
        transport = ScriptedTransport(responder=AccountScript(login_result=numbered_logins()))
        bridge = make_bridge(transport)

        async def scenario() -> None:
            first = await bridge.start_device_login()
            await bridge.cancel_login()
            second = await bridge.start_device_login()
            assert first.login_id != second.login_id
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {"success": True, "loginId": first.login_id},
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert bridge.login_pending is True
            # And the one that does name it still clears it.
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {"success": True, "loginId": second.login_id},
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert bridge.login_pending is False
            await bridge.aclose()

        run(scenario())

    def test_a_completion_that_overtakes_the_start_reply_is_not_lost(self) -> None:
        """The ordering race: completed arrives before start has returned.

        The read loop sees the notification while ``start_device_login`` is
        still awaiting its reply, so there is no pending id to match. Without
        the remembered completion, ``login_pending`` would stay true for a
        sign-in that has already finished, and the only way out would be to
        cancel something that no longer exists.
        """

        def complete_before_replying(
            transport: ScriptedTransport, message: dict
        ) -> dict:
            transport.emit(
                {
                    "method": "account/login/completed",
                    "params": {"success": True, "loginId": "login-abc123"},
                }
            )
            return dict(DEVICE_LOGIN_RESULT)

        transport = ScriptedTransport(
            responder=AccountScript(login_result=complete_before_replying)
        )
        bridge = make_bridge(transport)

        async def scenario():
            login = await bridge.start_device_login()
            assert login.user_code == "WXYZ-1234"
            assert bridge.login_pending is False
            status = await bridge.status()
            await bridge.aclose()
            return status

        assert run(scenario()).login_pending is False

    def test_a_completion_that_names_nothing_leaves_the_sign_in_pending(self) -> None:
        """`loginId` is optional in the schema, so it can be absent.

        Guessing which sign-in an uncorrelated completion meant is the same bug
        as clearing on a stale one. The pending sign-in stays pending, and the
        interface can still cancel it.
        """
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.start_device_login()
            transport.emit(
                {"method": "account/login/completed", "params": {"success": True}}
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert bridge.login_pending is True
            await bridge.aclose()

        run(scenario())

    def test_no_login_id_reaches_the_logs_on_any_of_those_paths(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(transport)

        async def scenario() -> None:
            await bridge.start_device_login()
            for login_id in ("login-somebody-else", "login-abc123"):
                transport.emit(
                    {
                        "method": "account/login/completed",
                        "params": {"success": True, "loginId": login_id},
                    }
                )
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            await bridge.aclose()

        with caplog.at_level(logging.DEBUG):
            run(scenario())
        for secret in ("login-abc123", "login-somebody-else", "WXYZ-1234", "example.test/device"):
            assert secret not in caplog.text
        assert "model_login_completed status=unmatched" in caplog.text
        assert "model_login_completed status=ok" in caplog.text


class TestNotifications:
    def test_a_rolling_rate_limit_update_is_merged_not_replaced(self) -> None:
        script = AccountScript(account=chatgpt_account(), limits=rate_limits(primary_used=10))
        transport = ScriptedTransport(responder=script)
        bridge = make_bridge(transport)

        async def scenario():
            await bridge.status()
            transport.emit(
                {
                    "method": "account/rateLimits/updated",
                    # Sparse: only `primary` moves, and `secondary` is absent.
                    "params": {"rateLimits": {"primary": {"usedPercent": 88}}},
                }
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            script.overrides = {protocol.ACCOUNT_RATE_LIMITS_READ: Error(-32603, "gone")}
            status = await bridge.status()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert status.rate_limits is not None
        assert status.rate_limits.primary is not None
        assert status.rate_limits.primary.used_percent == 88
        # The window that the sparse update did not mention survived it.
        assert status.rate_limits.secondary is not None

    def test_an_account_update_supplies_a_plan_the_read_did_not(self) -> None:
        account = {
            "account": {"type": "chatgpt", "email": EMAIL, "planType": None},
            "requiresOpenaiAuth": True,
        }
        transport = ScriptedTransport(responder=AccountScript(account=account))
        bridge = make_bridge(transport)

        async def scenario():
            before = await bridge.status()
            transport.emit(
                {"method": "account/updated", "params": {"planType": "pro", "authMode": "chatgpt"}}
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            after = await bridge.status()
            await bridge.aclose()
            return before, after

        before, after = run(scenario())
        assert before.plan is None
        assert after.plan == "pro"
        assert EMAIL not in repr(after)


class TestRestart:
    def test_restart_drops_cached_state_and_reports_the_new_process(self) -> None:
        first = ScriptedTransport(responder=AccountScript(account=chatgpt_account()))
        second = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(first, second)

        async def scenario():
            await bridge.status()
            status = await bridge.restart()
            await bridge.aclose()
            return status

        status = run(scenario())
        assert first.stopped is True
        assert status.state == "signed_out"
        assert status.rate_limits is None
        assert status.plan is None

    def test_restart_cancels_a_pending_login(self) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(responder=AccountScript())
        bridge = make_bridge(first, second)

        async def scenario():
            await bridge.start_device_login()
            status = await bridge.restart()
            await bridge.aclose()
            return status

        assert run(scenario()).login_pending is False


class TestSanitisers:
    def test_a_snapshot_keeps_only_windows_and_whether_a_limit_was_reached(self) -> None:
        limits = sanitise_rate_limits(rate_limits(primary_used=5))
        assert limits is not None
        assert limits.limited is False
        assert limits.limit_reason is None
        assert not hasattr(limits, "credits")

    def test_a_spend_control_flag_counts_as_limited(self) -> None:
        snapshot = rate_limits(primary_used=5)
        snapshot["spendControlReached"] = True
        limits = sanitise_rate_limits(snapshot)
        assert limits is not None and limits.limited is True

    def test_a_full_window_counts_as_limited_even_without_a_reason(self) -> None:
        limits = sanitise_rate_limits(rate_limits(primary_used=100))
        assert limits is not None and limits.limited is True

    def test_a_used_percentage_is_clamped(self) -> None:
        limits = sanitise_rate_limits({"primary": {"usedPercent": 240}})
        assert limits is not None and limits.primary is not None
        assert limits.primary.used_percent == 100

    def test_an_implausible_reset_time_is_dropped_rather_than_rendered(self) -> None:
        """`resetsAt` is an int64 with no stated unit; a wrong guess is not shown."""
        limits = sanitise_rate_limits({"primary": {"usedPercent": 5, "resetsAt": 900}})
        assert limits is not None and limits.primary is not None
        assert limits.primary.resets_at is None

    def test_nothing_useful_becomes_nothing_shown(self) -> None:
        assert sanitise_rate_limits({}) is None
        assert sanitise_rate_limits(None) is None
        assert sanitise_rate_limits("nonsense") is None

    def test_a_null_in_a_sparse_update_does_not_clear_a_known_value(self) -> None:
        merged = merge_snapshot({"primary": {"usedPercent": 10}}, {"primary": None, "secondary": 1})
        assert merged == {"primary": {"usedPercent": 10}, "secondary": 1}


def test_a_timeout_surfaces_as_a_bridge_timeout_for_a_login() -> None:
    """Sign-in raises rather than becoming a state: the button needs an outcome."""
    script = AccountScript(overrides={protocol.ACCOUNT_LOGIN_START: NoReply})
    bridge = ModelBridge(
        codex_path=Path("/nonexistent/codex"),
        working_directory=Path("/tmp"),
        client_version="0.1.0",
        request_timeout=0.05,
        transport_factory=factory(ScriptedTransport(responder=script)),
    )

    async def scenario() -> None:
        with pytest.raises(BridgeTimeout):
            await bridge.start_device_login()
        await bridge.aclose()

    run(scenario())
