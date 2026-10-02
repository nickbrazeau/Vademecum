"""OAuth 2.1 over the HTTP application: discovery, registration, consent,
tokens, refresh rotation, and the bearer gate on /mcp.

Driven over httpx's ASGI transport against the real Starlette app, so the
SDK's handlers, the provider and the consent page are all exercised together.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from tests.mcp_support import MCP_ORIGIN, PASSPHRASE

from vademecum_mcp.auth.store import LOCKOUT_ATTEMPTS, AccessStore
from vademecum_mcp.server import build_http_app

pytestmark = pytest.mark.anyio

REDIRECT = "https://chatgpt.com/connector/oauth/abc123"
RESOURCE = MCP_ORIGIN + "/mcp"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


@pytest.fixture
def store(mcp_settings) -> AccessStore:
    store = AccessStore(mcp_settings.access_db_path)
    store.set_passphrase(PASSPHRASE)
    return store


@pytest.fixture
async def http_app(mcp_settings, api, store):
    app = build_http_app(mcp_settings, api, store)
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def web(http_app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_app), base_url=MCP_ORIGIN
    ) as client:
        yield client


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return verifier, challenge


async def register(web: httpx.AsyncClient, *, name: str = "ChatGPT") -> dict:
    response = await web.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [REDIRECT],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def start_authorization(
    web: httpx.AsyncClient, client_id: str, challenge: str, *, resource: str | None = RESOURCE
) -> str:
    """Run /authorize and return the pending consent request id."""
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "xyz",
        "scope": "vademecum",
    }
    if resource is not None:
        params["resource"] = resource
    response = await web.get("/authorize", params=params)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(MCP_ORIGIN + "/consent?request="), location
    return parse_qs(urlsplit(location).query)["request"][0]


async def approve(web: httpx.AsyncClient, request_id: str, passphrase: str = PASSPHRASE) -> httpx.Response:
    return await web.post(
        "/consent", data={"request": request_id, "passphrase": passphrase, "action": "approve"}
    )


async def obtain_tokens(web: httpx.AsyncClient) -> tuple[dict, dict]:
    client = await register(web)
    verifier, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    approved = await approve(web, request_id)
    assert approved.status_code == 302, approved.text
    assert "https:" in approved.headers["content-security-policy"], "the redirect itself must be allowed"
    query = parse_qs(urlsplit(approved.headers["location"]).query)
    assert query["state"] == ["xyz"]
    token = await web.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client["client_id"],
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )
    assert token.status_code == 200, token.text
    return client, token.json()


async def initialize(web: httpx.AsyncClient, token: str | None, **headers: str) -> httpx.Response:
    extra = dict(MCP_HEADERS, **headers)
    if token is not None:
        extra["Authorization"] = f"Bearer {token}"
    return await web.post("/mcp", json=INITIALIZE, headers=extra)


# --- discovery ----------------------------------------------------------------


async def test_protected_resource_metadata_points_at_this_server(web: httpx.AsyncClient) -> None:
    response = await web.get("/.well-known/oauth-protected-resource/mcp")
    assert response.status_code == 200
    body = response.json()
    assert body["resource"].rstrip("/") == RESOURCE
    assert [url.rstrip("/") for url in body["authorization_servers"]] == [MCP_ORIGIN]
    assert body["bearer_methods_supported"] == ["header"]
    assert body["scopes_supported"] == ["vademecum"]


async def test_authorization_server_metadata(web: httpx.AsyncClient) -> None:
    response = await web.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    body = response.json()
    assert body["issuer"].rstrip("/") == MCP_ORIGIN
    assert body["authorization_endpoint"].endswith("/authorize")
    assert body["token_endpoint"].endswith("/token")
    assert body["registration_endpoint"].endswith("/register")
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert "authorization_code" in body["grant_types_supported"]
    assert "refresh_token" in body["grant_types_supported"]


async def test_mcp_without_a_token_is_401_with_discovery_pointer(web: httpx.AsyncClient) -> None:
    response = await initialize(web, None)
    assert response.status_code == 401
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert "/.well-known/oauth-protected-resource/mcp" in challenge


# --- the flow -----------------------------------------------------------------


async def test_the_whole_flow_issues_a_usable_token(web: httpx.AsyncClient) -> None:
    client, tokens = await obtain_tokens(web)
    assert tokens["token_type"] == "Bearer"
    assert tokens["scope"] == "vademecum"
    assert tokens["expires_in"] == 3600
    assert tokens["refresh_token"]
    response = await initialize(web, tokens["access_token"])
    assert response.status_code == 200, response.text


async def test_the_consent_page_names_the_client_and_sets_safe_headers(web: httpx.AsyncClient) -> None:
    client = await register(web, name="Claude <script>")
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    page = await web.get("/consent", params={"request": request_id})
    assert page.status_code == 200
    assert "Claude &lt;script&gt;" in page.text
    assert "<script>" not in page.text
    assert "chatgpt.com" in page.text
    assert page.headers["cache-control"] == "no-store"
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    # Chrome applies form-action to the redirect after a submission, and the
    # approval IS a redirect to the client: HTTPS anywhere or loopback HTTP,
    # the same rule registration enforces. A stricter policy silently drops
    # the code on the floor.
    assert "form-action 'self' https: http://127.0.0.1:* http://localhost:*" in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["referrer-policy"] == "same-origin"
    assert 'type="password"' in page.text


async def test_a_wrong_passphrase_is_refused_and_then_locked_out(web: httpx.AsyncClient) -> None:
    client = await register(web)
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    for _ in range(LOCKOUT_ATTEMPTS):
        refused = await approve(web, request_id, "wrong passphrase")
        assert refused.status_code == 200
        assert "not right" in refused.text
        assert "code=" not in refused.headers.get("location", "")
    locked = await approve(web, request_id, PASSPHRASE)
    assert locked.status_code == 429
    assert "Too many" in locked.text


async def test_declining_sends_access_denied_and_spends_the_request(web: httpx.AsyncClient) -> None:
    client = await register(web)
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    declined = await web.post("/consent", data={"request": request_id, "action": "deny"})
    assert declined.status_code == 302
    query = parse_qs(urlsplit(declined.headers["location"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["xyz"]
    again = await approve(web, request_id)
    assert again.status_code == 400
    assert "expired" in again.text


async def test_an_unknown_consent_request_is_refused(web: httpx.AsyncClient) -> None:
    response = await web.get("/consent", params={"request": "nope"})
    assert response.status_code == 400
    response = await approve(web, "nope")
    assert response.status_code == 400


async def test_without_a_passphrase_nothing_can_be_approved(mcp_settings, api) -> None:
    store = AccessStore(mcp_settings.access_db_path)
    app = build_http_app(mcp_settings, api, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=MCP_ORIGIN
        ) as web:
            client = await register(web)
            _, challenge = pkce()
            request_id = await start_authorization(web, client["client_id"], challenge)
            refused = await approve(web, request_id, "anything at all")
            assert refused.status_code == 403
            assert "passphrase" in refused.text.lower()


async def test_a_wrong_verifier_gets_no_token(web: httpx.AsyncClient) -> None:
    client = await register(web)
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    approved = await approve(web, request_id)
    code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
    token = await web.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": client["client_id"],
            "code_verifier": "not-the-verifier",
            "resource": RESOURCE,
        },
    )
    assert token.status_code == 400
    assert token.json()["error"] == "invalid_grant"


async def test_a_code_is_single_use(web: httpx.AsyncClient) -> None:
    client = await register(web)
    verifier, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    approved = await approve(web, request_id)
    code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": client["client_id"],
        "code_verifier": verifier,
        "resource": RESOURCE,
    }
    first = await web.post("/token", data=form)
    assert first.status_code == 200
    second = await web.post("/token", data=form)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"


async def test_a_token_for_another_resource_is_refused_at_authorize(web: httpx.AsyncClient) -> None:
    client = await register(web)
    _, challenge = pkce()
    response = await web.get(
        "/authorize",
        params={
            "client_id": client["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "resource": "https://elsewhere.example/mcp",
        },
    )
    assert response.status_code == 302
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["error"] == ["invalid_target"]


async def test_refresh_rotates_and_reuse_signs_the_family_out(web: httpx.AsyncClient) -> None:
    client, tokens = await obtain_tokens(web)
    form = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": client["client_id"],
    }
    rotated = await web.post("/token", data=form)
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()
    assert fresh["access_token"] != tokens["access_token"]
    assert fresh["refresh_token"] != tokens["refresh_token"]
    assert (await initialize(web, fresh["access_token"])).status_code == 200

    # The old refresh token is presented again: a leak. Everything in the
    # family is revoked, the fresh access token included.
    replay = await web.post("/token", data=form)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert (await initialize(web, fresh["access_token"])).status_code == 401
    assert (await initialize(web, tokens["access_token"])).status_code == 401


async def test_revocation_endpoint_signs_a_connection_out(web: httpx.AsyncClient) -> None:
    client, tokens = await obtain_tokens(web)
    # The SDK's revocation request insists on the client_secret key being
    # present, even for a public client that has none; an empty value is what
    # such a client sends.
    revoked = await web.post(
        "/revoke",
        data={"token": tokens["refresh_token"], "client_id": client["client_id"], "client_secret": ""},
    )
    assert revoked.status_code == 200, revoked.text
    assert (await initialize(web, tokens["access_token"])).status_code == 401


async def test_revoke_all_on_the_mac_signs_everyone_out(web: httpx.AsyncClient, store: AccessStore) -> None:
    _, tokens = await obtain_tokens(web)
    assert (await initialize(web, tokens["access_token"])).status_code == 200
    result = store.revoke_all()
    assert result["tokens_revoked"] >= 2
    assert result["clients_removed"] == 1
    assert (await initialize(web, tokens["access_token"])).status_code == 401
    assert store.has_passphrase(), "revoking connections keeps the passphrase"


async def test_a_foreign_host_header_is_refused_everywhere(web: httpx.AsyncClient) -> None:
    """Rebinding a name to loopback reaches every route, so every route checks."""
    _, tokens = await obtain_tokens(web)
    response = await initialize(web, tokens["access_token"], Host="evil.example")
    assert response.status_code == 421
    for path in ("/health", "/", "/.well-known/oauth-authorization-server", "/consent"):
        response = await web.get(path, headers={"Host": "evil.example"})
        assert response.status_code == 421, path
    response = await web.post("/register", json={"redirect_uris": [REDIRECT]}, headers={"Host": "evil.example:443"})
    assert response.status_code == 421
    # The names this server is actually reached by pass.
    for host in ("127.0.0.1:8766", "localhost:8766"):
        assert (await web.get("/health", headers={"Host": host})).status_code == 200, host


async def test_a_bad_redirect_uri_cannot_be_registered(web: httpx.AsyncClient) -> None:
    response = await web.post(
        "/register",
        json={
            "client_name": "Phish",
            "redirect_uris": ["http://phish.example/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


async def test_secrets_never_reach_the_logs(
    web: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client, tokens = await obtain_tokens(web)
        await initialize(web, tokens["access_token"])
        _, challenge = pkce()
        request_id = await start_authorization(web, client["client_id"], challenge)
        await approve(web, request_id, "wrong passphrase")
    text = caplog.text
    assert PASSPHRASE not in text
    assert "wrong passphrase" not in text
    assert tokens["access_token"] not in text
    assert tokens["refresh_token"] not in text
    assert request_id not in text
    assert "consent_approved" in text


async def test_the_store_file_is_private(store: AccessStore) -> None:
    assert (store.path.stat().st_mode & 0o777) == 0o600
