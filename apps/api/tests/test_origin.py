"""Same-origin enforcement on every mutating route.

Binding loopback is not the same as being reachable only by Vademecum. A page
on any origin the browser has open can POST a simple form -- including
``multipart/form-data`` -- to 127.0.0.1 without CORS being consulted, and a
hostname the attacker controls can be rebound to 127.0.0.1 so the request
arrives carrying a Host the owner never typed.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.api.origin import allowed_authorities
from vademecum.app import create_app
from vademecum.config import Settings

FOREIGN = "https://evil.example"


@pytest.fixture()
def app_client(settings: Settings):
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        yield client


class TestCrossOriginWrites:
    def test_a_cross_origin_post_is_refused(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Origin": FOREIGN},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "cross_origin"
        assert app_client.get("/api/piles").json() == [], "nothing was written"

    def test_a_cross_origin_referer_is_refused(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Referer": f"{FOREIGN}/attack.html"},
        )
        assert response.status_code == 403

    def test_a_cross_origin_multipart_upload_is_refused(
        self, app_client: TestClient
    ) -> None:
        """The shape that CORS does not preflight, so CORS cannot stop it."""
        pile = app_client.post("/api/piles", json={"title": "P", "tier": "mid"}).json()
        response = app_client.post(
            f"/api/piles/{pile['id']}/sources",
            files={"files": ("note.txt", b"some uploaded text here", "text/plain")},
            data={"confidence": "mid"},
            headers={"Origin": FOREIGN},
        )
        assert response.status_code == 403
        assert app_client.get(f"/api/piles/{pile['id']}/sources").json() == []

    @pytest.mark.parametrize("method", ["POST", "PATCH", "DELETE"])
    def test_every_mutating_verb_is_covered(
        self, app_client: TestClient, method: str
    ) -> None:
        response = app_client.request(
            method, "/api/piles/whatever", headers={"Origin": FOREIGN}, json={}
        )
        assert response.status_code == 403

    def test_reads_from_a_foreign_origin_are_allowed_when_the_host_is_ours(
        self, app_client: TestClient
    ) -> None:
        """A cross-origin GET reads nothing back: there is no CORS header.

        The browser will not hand the response to the attacking page, and the
        Host is still ours. Rebinding -- the attack that does reach reads -- is
        covered by TestHostIsCheckedOnEveryMethod.
        """
        response = app_client.get("/api/health", headers={"Origin": FOREIGN})
        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers


class TestOriginNull:
    def test_an_explicit_null_origin_is_refused(self, app_client: TestClient) -> None:
        """`Origin: null` is a sandboxed/opaque context, not an absent header."""
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Origin": "null"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "cross_origin"
        assert app_client.get("/api/piles").json() == []

    def test_a_null_origin_does_not_fall_through_to_referer(
        self, app_client: TestClient
    ) -> None:
        """A present Origin decides. A friendly Referer must not rescue it."""
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Origin": "null", "Referer": f"{LOCAL_ORIGIN}/sources"},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "origin",
        ["", "   ", "http://", "://nonsense", "javascript:alert(1)", "file://", "%%%"],
    )
    def test_a_malformed_origin_is_refused_not_a_server_error(
        self, app_client: TestClient, origin: str
    ) -> None:
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Origin": origin},
        )
        assert response.status_code == 403, origin

    def test_a_referer_is_used_only_when_origin_is_absent(
        self, app_client: TestClient
    ) -> None:
        ours = app_client.post(
            "/api/piles",
            json={"title": "Ours", "tier": "mid"},
            headers={"Referer": f"{LOCAL_ORIGIN}/sources"},
        )
        assert ours.status_code == 201


class TestHostIsCheckedOnEveryMethod:
    """A rebound name reaches reads too, so the Host check cannot be write-only."""

    @pytest.mark.parametrize(
        "path", ["/api/health", "/api/today", "/api/piles", "/api/export", "/api/runs"]
    )
    def test_a_foreign_host_get_is_refused(
        self, app_client: TestClient, path: str
    ) -> None:
        response = app_client.get(path, headers={"Host": "rebind.attacker.example"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "foreign_host"

    def test_the_handler_never_runs_for_a_foreign_host(
        self, app_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Refused before the route, not after: nothing is read from the database."""
        from vademecum.api import deps

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("the handler ran for a refused request")

        monkeypatch.setattr(deps, "get_connection", explode)
        response = app_client.get("/api/piles", headers={"Host": "attacker.example"})
        assert response.status_code == 403

    def test_the_handler_never_runs_for_a_cross_origin_write(
        self, app_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from vademecum.api import routes_piles

        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("the handler ran for a refused request")

        monkeypatch.setattr(routes_piles.store, "create_pile", explode)
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Origin": FOREIGN},
        )
        assert response.status_code == 403

    @pytest.mark.parametrize("host", ["attacker.example", "evil.test:8765", "%%%", ""])
    def test_a_malformed_or_foreign_host_is_refused_not_a_server_error(
        self, app_client: TestClient, host: str
    ) -> None:
        response = app_client.get("/api/health", headers={"Host": host})
        assert response.status_code == 403, host


class TestDnsRebinding:
    def test_a_foreign_host_header_is_refused(self, app_client: TestClient) -> None:
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Host": "attacker.example"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "foreign_host"

    def test_a_rebound_name_pointing_here_is_still_refused(
        self, app_client: TestClient
    ) -> None:
        """The DNS-rebinding shape: resolves to 127.0.0.1, is not our authority."""
        response = app_client.post(
            "/api/piles",
            json={"title": "Sepsis", "tier": "mid"},
            headers={"Host": "rebind.attacker.example:8765"},
        )
        assert response.status_code == 403


class TestAllowedOrigins:
    def test_the_api_and_web_ports_are_both_accepted(self) -> None:
        allowed = allowed_authorities(8765, 5173)
        for authority in (
            "127.0.0.1:8765",
            "127.0.0.1:5173",
            "localhost:8765",
            "localhost:5173",
            "[::1]:8765",
        ):
            assert authority in allowed
        assert "evil.example" not in allowed
        assert "127.0.0.1:9999" not in allowed

    def test_the_configured_web_port_is_what_is_allowed(self) -> None:
        allowed = allowed_authorities(8765, 5174)
        assert "127.0.0.1:5174" in allowed
        assert "127.0.0.1:5173" not in allowed

    def test_a_same_origin_write_from_the_web_port_succeeds(
        self, settings: Settings
    ) -> None:
        app = create_app(settings, transport_factory=refusing_factory())
        with TestClient(app, base_url=LOCAL_ORIGIN) as client:
            response = client.post(
                "/api/piles",
                json={"title": "Sepsis", "tier": "mid"},
                headers={"Origin": f"http://127.0.0.1:{settings.web_port}"},
            )
        assert response.status_code == 201

    def test_a_request_with_no_origin_header_is_allowed(
        self, app_client: TestClient
    ) -> None:
        """curl and the dev script send none. The Host check still applies."""
        response = app_client.post("/api/piles", json={"title": "S", "tier": "mid"})
        assert response.status_code == 201

    def test_testserver_is_not_an_allowed_authority(self) -> None:
        """The suite uses a real loopback origin, not a test-only exemption."""
        allowed = allowed_authorities(8765, 5173)
        assert "testserver" not in allowed
        assert not any("testserver" in authority for authority in allowed)


class TestNoCorsAnywhere:
    def test_nothing_sends_an_allow_origin_header(self, app_client: TestClient) -> None:
        for path in ("/api/health", "/api/today", "/api/piles"):
            headers = app_client.get(path, headers={"Origin": FOREIGN}).headers
            assert "access-control-allow-origin" not in headers

    def test_no_cors_middleware_is_installed(self) -> None:
        from vademecum.config import find_repo_root

        root = find_repo_root()
        assert root is not None
        source = (root / "apps" / "api" / "src" / "vademecum").rglob("*.py")
        for path in source:
            text = path.read_text(encoding="utf-8")
            assert "CORSMiddleware" not in text, path.name
