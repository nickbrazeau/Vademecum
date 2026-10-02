"""The four model routes, over the scripted transport.

No route here sends content anywhere. The suite proves what they return and,
more importantly, what they do not.
"""

from __future__ import annotations

import logging

import pytest
from conftest import LOCAL_ORIGIN
from fake_appserver import (
    AccountScript,
    Error,
    NoReply,
    ScriptedTransport,
    api_key_account,
    chatgpt_account,
    factory,
    rate_limits,
)
from fastapi.testclient import TestClient

from vademecum.app import create_app
from vademecum.appserver import protocol
from vademecum.appserver.errors import BridgeUnavailable
from vademecum.config import Settings

EMAIL = "owner@example.test"


def app_with(settings: Settings, *transports: ScriptedTransport) -> TestClient:
    return TestClient(
        create_app(settings, transport_factory=factory(*transports)),
        base_url=LOCAL_ORIGIN,
    )


class TestStatusRoute:
    def test_signed_out_is_a_plain_two_hundred(self, model_client: TestClient) -> None:
        response = model_client.get("/api/model/status")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "signed_out"
        assert body["signed_in"] is False
        assert body["plan"] is None
        assert body["rate_limits"] is None
        assert body["login_pending"] is False
        assert body["detail"]

    def test_signed_in_shows_a_plan_and_usage_but_no_identifier(
        self, settings: Settings
    ) -> None:
        script = AccountScript(
            account=chatgpt_account(plan="plus", email=EMAIL),
            limits=rate_limits(primary_used=35, secondary_used=60),
        )
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            response = client.get("/api/model/status")
        body = response.json()
        assert body["state"] == "signed_in"
        assert body["plan"] == "plus"
        assert body["rate_limits"]["primary"]["used_percent"] == 35
        assert body["rate_limits"]["primary"]["resets_at"] == "2027-01-15T08:00:00Z"
        assert body["rate_limits"]["secondary"]["used_percent"] == 60
        assert body["rate_limits"]["limited"] is False
        assert EMAIL not in response.text
        assert "@" not in response.text

    def test_a_reached_limit_is_its_own_state(self, settings: Settings) -> None:
        script = AccountScript(
            account=chatgpt_account(),
            limits=rate_limits(primary_used=100, reached="rate_limit_reached"),
        )
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            body = client.get("/api/model/status").json()
        assert body["state"] == "rate_limited"
        assert body["rate_limits"]["limited"] is True
        assert body["signed_in"] is True

    def test_an_api_key_account_reads_as_signed_out_with_a_reason(
        self, settings: Settings
    ) -> None:
        script = AccountScript(account=api_key_account())
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            body = client.get("/api/model/status").json()
        assert body["state"] == "signed_out"
        assert body["reason"] == "credential_unsupported"

    def test_an_absent_codex_is_an_unavailable_state_not_a_five_hundred(
        self, settings: Settings
    ) -> None:
        transport = ScriptedTransport(start_error=BridgeUnavailable("codex_not_found"))
        with app_with(settings, transport) as client:
            response = client.get("/api/model/status")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "unavailable"
        assert body["reason"] == "codex_not_found"
        assert "JSON-RPC" not in body["detail"]
        assert "-32" not in body["detail"]

    def test_the_snapshot_is_never_cacheable(self, model_client: TestClient) -> None:
        response = model_client.get("/api/model/status")
        assert response.headers["Cache-Control"] == "no-store"

    def test_the_process_starts_only_when_the_page_asks(self, settings: Settings) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        with app_with(settings, transport) as client:
            # Health, piles, flags: none of them is a reason to start Codex.
            client.get("/api/health")
            client.get("/api/piles")
            client.get("/api/today")
            assert transport.started is False
            client.get("/api/model/status")
            assert transport.started is True


class TestLoginRoute:
    def test_the_code_and_url_come_back_once_and_uncacheable(
        self, model_client: TestClient
    ) -> None:
        response = model_client.post("/api/model/login")
        assert response.status_code == 201
        assert response.headers["Cache-Control"] == "no-store"
        body = response.json()
        assert body["verification_url"] == "https://example.test/device"
        assert body["user_code"] == "WXYZ-1234"
        assert body["login_id"] == "login-abc123"
        assert body["status"] == "pending"

    def test_the_status_route_never_repeats_them(self, model_client: TestClient) -> None:
        model_client.post("/api/model/login")
        response = model_client.get("/api/model/status")
        assert response.json()["login_pending"] is True
        assert "WXYZ-1234" not in response.text
        assert "example.test" not in response.text
        assert "login-abc123" not in response.text

    def test_only_device_code_login_is_ever_requested(
        self, model_client: TestClient, scripted_transport: ScriptedTransport
    ) -> None:
        model_client.post("/api/model/login")
        sent = scripted_transport.requests(protocol.ACCOUNT_LOGIN_START)
        assert [message["params"] for message in sent] == [{"type": "chatgptDeviceCode"}]

    def test_a_login_answered_with_another_variant_is_a_plain_error(
        self, settings: Settings
    ) -> None:
        script = AccountScript(login_result={"type": "apiKey"})
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            response = client.post("/api/model/login")
        assert response.status_code == 502
        body = response.json()["error"]
        assert body["code"] == "login_not_supported"
        assert "API key" in body["message"]
        assert body["correlation_id"]

    def test_nothing_about_a_login_reaches_the_logs(
        self, model_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            model_client.post("/api/model/login")
            model_client.get("/api/model/status")
            model_client.post("/api/model/login/cancel")
        for secret in ("WXYZ-1234", "login-abc123", "example.test/device"):
            assert secret not in caplog.text
        assert "http_request" in caplog.text

    def test_nothing_about_a_login_reaches_the_database(
        self, model_client: TestClient, database_path
    ) -> None:
        model_client.post("/api/model/login")
        blob = database_path.read_bytes()
        for secret in (b"WXYZ-1234", b"login-abc123", b"example.test"):
            assert secret not in blob


class TestCancelRoute:
    def test_cancelling_needs_no_body_from_the_browser(
        self, model_client: TestClient, scripted_transport: ScriptedTransport
    ) -> None:
        model_client.post("/api/model/login")
        response = model_client.post("/api/model/login/cancel")
        assert response.status_code == 200
        assert response.json() == {"status": "canceled"}
        cancelled = scripted_transport.requests(protocol.ACCOUNT_LOGIN_CANCEL)
        assert cancelled[0]["params"] == {"loginId": "login-abc123"}

    def test_cancelling_nothing_says_so_rather_than_failing(
        self, model_client: TestClient
    ) -> None:
        response = model_client.post("/api/model/login/cancel")
        assert response.status_code == 200
        assert response.json() == {"status": "nothing_pending"}

    def test_a_cancelled_login_is_no_longer_pending(self, model_client: TestClient) -> None:
        model_client.post("/api/model/login")
        model_client.post("/api/model/login/cancel")
        assert model_client.get("/api/model/status").json()["login_pending"] is False


class TestRestartRoute:
    def test_restart_replaces_the_process_and_returns_fresh_state(
        self, model_client: TestClient, scripted_transport: ScriptedTransport
    ) -> None:
        model_client.get("/api/model/status")
        response = model_client.post("/api/model/restart")
        assert response.status_code == 200
        assert response.json()["state"] == "signed_out"
        assert scripted_transport.stopped is True
        second = model_client.app.state.second_transport  # type: ignore[attr-defined]
        assert len(second.requests("initialize")) == 1

    def test_restart_clears_a_pending_login(self, model_client: TestClient) -> None:
        model_client.post("/api/model/login")
        assert model_client.post("/api/model/restart").json()["login_pending"] is False

    def test_a_restart_that_cannot_start_is_an_honest_state(self, settings: Settings) -> None:
        first = ScriptedTransport(responder=AccountScript())
        second = ScriptedTransport(start_error=BridgeUnavailable("spawn_failed"))
        with app_with(settings, first, second) as client:
            client.get("/api/model/status")
            response = client.post("/api/model/restart")
        assert response.status_code == 200
        body = response.json()
        assert body["state"] == "unavailable"
        assert body["reason"] == "spawn_failed"


class TestErrorTranslation:
    def test_a_timeout_becomes_a_gateway_timeout_with_plain_language(
        self, settings: Settings
    ) -> None:
        script = AccountScript(overrides={protocol.ACCOUNT_LOGIN_START: NoReply})
        settings = settings.model_copy(update={"appserver_request_timeout": 0.05})
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            response = client.post("/api/model/login")
        assert response.status_code == 504
        body = response.json()["error"]
        assert body["code"] == "timeout"
        assert "did not answer in time" in body["message"]

    def test_a_refusal_never_echoes_the_servers_own_words(self, settings: Settings) -> None:
        script = AccountScript(
            overrides={protocol.ACCOUNT_LOGIN_START: Error(-32603, "ZZQXMARKERZZ upstream")}
        )
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            response = client.post("/api/model/login")
        assert response.status_code == 502
        assert "ZZQXMARKERZZ" not in response.text
        assert response.json()["error"]["code"] == "server_error"

    def test_a_bridge_error_response_is_never_cached(self, settings: Settings) -> None:
        script = AccountScript(overrides={protocol.ACCOUNT_LOGIN_START: Error(-32603, "no")})
        with app_with(settings, ScriptedTransport(responder=script)) as client:
            response = client.post("/api/model/login")
        assert response.headers["Cache-Control"] == "no-store"


class TestHealth:
    def test_health_reports_the_bridge_and_the_turn_capability_separately(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/health").json()
        assert body["model_bridge_configured"] is True
        # Turns exist now. The flag says the capability is wired, not that
        # anything has been sent -- transmission needs Build or Grade.
        assert body["model_calls_configured"] is True
        assert body["literature_configured"] is True


class TestShutdown:
    def test_leaving_the_application_stops_the_child(self, settings: Settings) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        with app_with(settings, transport) as client:
            client.get("/api/model/status")
            assert transport.started is True
        assert transport.stopped is True

    def test_an_application_that_never_used_the_bridge_shuts_down_cleanly(
        self, settings: Settings
    ) -> None:
        transport = ScriptedTransport(responder=AccountScript())
        with app_with(settings, transport) as client:
            client.get("/api/health")
        assert transport.started is False
