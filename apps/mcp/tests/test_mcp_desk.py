"""The desk through the gateway (ADR 0011): sign-in cookie, proxied API with
streamed uploads, same-origin checks, the web app at the root, and the
boundary between a desk session and an MCP token.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from tests.mcp_support import API_ORIGIN, LECTURE, MCP_ORIGIN

from fake_appserver import AccountScript, ScriptedTransport, factory  # noqa: E402
from fake_model import FakeProvider  # noqa: E402
from vademecum.app import create_app  # noqa: E402
from vademecum.config import Settings as ApiSettings  # noqa: E402
from vademecum.tenancy import SharedStoreResolver  # noqa: E402
from vademecum_mcp.api_client import ApiClient  # noqa: E402
from vademecum_mcp.auth.store import AccessStore  # noqa: E402
from vademecum_mcp.config import McpSettings  # noqa: E402
from vademecum_mcp.desk import COOKIE  # noqa: E402
from vademecum_mcp.server import build_http_app  # noqa: E402

pytestmark = pytest.mark.anyio

PASS = "correct horse battery staple"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A stand-in for apps/web/dist: a shell and one hashed asset."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Vademecum desk</title><div id=root></div>", encoding="utf-8")
    (root / "assets" / "main-abc123.js").write_text("console.log('desk')", encoding="utf-8")
    (root / "sw.js").write_text("// worker", encoding="utf-8")
    return root


@pytest.fixture
async def gateway(tmp_path: Path, dist: Path, provider: FakeProvider):
    mcp_settings = McpSettings(
        data_dir=tmp_path / "data", host="127.0.0.1", port=8765, mcp_port=8766,
        mcp_public_url=MCP_ORIGIN, tenancy="multi", mcp_desk_dist=dist, mcp_desk_session_ttl=3600,
    )
    api_settings = ApiSettings(
        data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", tenancy="multi"
    )
    store = AccessStore(mcp_settings.access_db_path)
    api_app = create_app(
        api_settings,
        transport_factory=factory(ScriptedTransport(responder=AccountScript())),
        provider_factory=lambda: provider,
        token_resolver=SharedStoreResolver(mcp_settings.access_db_path),
    )
    async with api_app.router.lifespan_context(api_app):
        api = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=api_app))
        app = build_http_app(mcp_settings, api, store)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=MCP_ORIGIN) as web:
                yield web, store


async def sign_up(web: httpx.AsyncClient, store: AccessStore, handle: str) -> httpx.AsyncClient:
    """A fresh browser that has registered and holds a session cookie."""
    browser = httpx.AsyncClient(transport=web._transport, base_url=MCP_ORIGIN)  # noqa: SLF001
    response = await browser.post(
        "/login",
        data={
            "action": "register",
            "invite": store.create_invite(),
            "new_handle": handle,
            "new_passphrase": PASS,
            "new_passphrase_again": PASS,
        },
        headers={"Origin": MCP_ORIGIN},
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/"
    assert COOKIE in browser.cookies
    return browser


# --- sign-in --------------------------------------------------------------------


async def test_the_login_page_is_the_same_forms_and_sets_a_hardened_cookie(gateway) -> None:
    web, store = gateway
    page = await web.get("/login")
    assert page.status_code == 200
    assert "Sign in to Vademecum" in page.text
    assert "First time here?" in page.text
    assert "patient identifiers" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "form-action 'self';" in page.headers["content-security-policy"], "the desk only redirects to itself"
    # Not `no-referrer`: Chrome would then send `Origin: null` with the form
    # and the gateway would refuse the learner's own sign-in.
    assert page.headers["referrer-policy"] == "same-origin"

    # What a browser actually sends for a same-site form post: its origin and
    # Sec-Fetch-Site. Both must pass; a `null` origin must not.
    browser_like = {"Origin": MCP_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    fine = await web.post("/login", data={"action": "approve", "handle": "x", "passphrase": "y"}, headers=browser_like)
    assert fine.status_code == 200 and "did not come from this site" not in fine.text
    null_origin = await web.post("/login", data={"action": "approve", "handle": "x", "passphrase": "y"}, headers={"Origin": "null"})
    assert null_origin.status_code == 403

    response = await web.post(
        "/login",
        data={
            "action": "register",
            "invite": store.create_invite(),
            "new_handle": "ada",
            "new_passphrase": PASS,
            "new_passphrase_again": PASS,
        },
        headers={"Origin": MCP_ORIGIN},
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/" in cookie
    assert "secure" not in cookie, "loopback http in tests; https sets Secure"


async def test_a_wrong_sign_in_is_refused_and_a_signed_in_browser_skips_the_page(gateway) -> None:
    web, store = gateway
    browser = await sign_up(web, store, "ada")
    wrong = await web.post("/login", data={"action": "approve", "handle": "ada", "passphrase": "nope nope nope"}, headers={"Origin": MCP_ORIGIN})
    assert wrong.status_code == 200 and "do not match" in wrong.text
    assert COOKIE not in wrong.headers.get("set-cookie", "")
    right = await web.post("/login", data={"action": "approve", "handle": "ada", "passphrase": PASS}, headers={"Origin": MCP_ORIGIN})
    assert right.status_code == 303
    assert (await browser.get("/login")).status_code == 303


async def test_the_desk_is_served_at_the_root_only_to_a_session(gateway) -> None:
    web, store = gateway
    anonymous = await web.get("/")
    assert anonymous.status_code == 303 and anonymous.headers["location"] == "/login"
    browser = await sign_up(web, store, "ada")
    shell = await browser.get("/")
    assert shell.status_code == 200 and "Vademecum desk" in shell.text
    assert shell.headers["cache-control"] == "no-store"
    route = await browser.get("/tutor")
    assert route.status_code == 200 and "Vademecum desk" in route.text
    asset = await web.get("/assets/main-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"]
    worker = await web.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["cache-control"] == "no-cache"
    escape = await browser.get("/../../etc/passwd")
    assert escape.status_code in {200, 303, 404} and "root:" not in escape.text


# --- the proxied API -------------------------------------------------------------


async def test_the_api_is_reached_through_the_session_and_isolated_per_learner(gateway) -> None:
    web, store = gateway
    assert (await web.get("/api/piles")).status_code == 401
    assert (await web.get("/api/piles")).json()["error"]["code"] == "unauthenticated"

    ada = await sign_up(web, store, "ada")
    bob = await sign_up(web, store, "bob")
    created = await ada.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}, headers={"Origin": MCP_ORIGIN})
    assert created.status_code == 201, created.text
    pile = created.json()
    assert [entry["id"] for entry in (await ada.get("/api/piles")).json()] == [pile["id"]]
    assert (await bob.get("/api/piles")).json() == []
    assert (await bob.get(f"/api/piles/{pile['id']}")).status_code == 404
    health = await ada.get("/api/health")
    assert health.json()["tenancy"] == "multi"
    assert health.headers["cache-control"] == "no-store"


async def test_an_upload_streams_through_to_the_learners_workspace(gateway) -> None:
    web, store = gateway
    ada = await sign_up(web, store, "ada")
    pile = (await ada.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}, headers={"Origin": MCP_ORIGIN})).json()
    big = (LECTURE * 400).encode()
    uploaded = await ada.post(
        f"/api/piles/{pile['id']}/sources",
        files=[("files", ("lecture.txt", big, "text/plain"))],
        data={"confidence": "mid"},
        headers={"Origin": MCP_ORIGIN},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["accepted"] == 1
    assert body["results"][0]["source"]["byte_size"] == len(big)
    preview = await ada.get(f"/api/piles/{pile['id']}/build/preview")
    assert preview.status_code == 200
    assert "your own ChatGPT" in preview.json()["disclosure"]["destination"]


async def test_mutations_from_another_origin_are_refused(gateway) -> None:
    web, store = gateway
    ada = await sign_up(web, store, "ada")
    foreign = await ada.post("/api/piles", json={"title": "X", "tier": "low"}, headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "cross_origin"
    fetched = await ada.post("/api/flags", json={"text": "x"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert fetched.status_code == 403
    assert (await ada.get("/api/piles", headers={"Origin": "https://evil.example"})).status_code == 200, "reads are protected by the cookie's SameSite, not by Origin"
    assert (await ada.post("/logout", headers={"Origin": "https://evil.example"})).status_code == 403


async def test_logout_revokes_the_session(gateway) -> None:
    web, store = gateway
    ada = await sign_up(web, store, "ada")
    assert (await ada.get("/api/piles")).status_code == 200
    out = await ada.post("/logout", headers={"Origin": MCP_ORIGIN})
    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert (await ada.get("/api/piles")).status_code == 401


async def test_a_desk_session_is_not_an_mcp_token(gateway) -> None:
    """The cookie's token names no resource, so the MCP endpoint refuses it."""
    web, store = gateway
    ada = await sign_up(web, store, "ada")
    token = ada.cookies[COOKIE]
    response = await web.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
        headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


async def test_deleting_the_workspace_from_the_desk_ends_the_session(gateway) -> None:
    web, store = gateway
    ada = await sign_up(web, store, "ada")
    await ada.post("/api/flags", json={"text": "gone soon"}, headers={"Origin": MCP_ORIGIN})
    deleted = await ada.request("DELETE", "/api/workspace", json={"confirm": "delete everything"}, headers={"Origin": MCP_ORIGIN})
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["connections_revoked"] >= 1
    assert (await ada.get("/api/piles")).status_code == 401


async def test_single_tenancy_without_a_built_web_app_shows_the_notice(mcp_settings, api, tmp_path: Path) -> None:
    """The desk's sign-in exists for one owner too (ADR 0017), but with no build
    the root is the notice and the API is not reachable without a session."""
    settings = mcp_settings.model_copy(update={"mcp_desk_dist": tmp_path / "nowhere"})
    store = AccessStore(settings.access_db_path)
    store.set_passphrase(PASS)
    app = build_http_app(settings, api, store)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=MCP_ORIGIN) as web:
            assert (await web.get("/login")).status_code == 200
            root = await web.get("/")
            assert root.status_code == 200 and "MCP endpoint" in root.text
            assert (await web.get("/api/piles")).status_code == 401


# --- one learner, the owner: the sea's gateway (ADR 0017) -----------------------------


@pytest.fixture
async def seat(tmp_path: Path, dist: Path, provider: FakeProvider):
    mcp_settings = McpSettings(
        data_dir=tmp_path / "data", host="127.0.0.1", port=8765, mcp_port=8766,
        mcp_public_url=MCP_ORIGIN, tenancy="single", mcp_desk_dist=dist, mcp_desk_session_ttl=3600,
    )
    api_settings = ApiSettings(
        data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host",
        sync_role="sea", sync_accept_token="the-peer-token-the-mac-presents",
    )
    store = AccessStore(mcp_settings.access_db_path)
    store.set_passphrase(PASS)
    api_app = create_app(
        api_settings,
        transport_factory=factory(ScriptedTransport(responder=AccountScript())),
        provider_factory=lambda: provider,
    )
    async with api_app.router.lifespan_context(api_app):
        api = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=api_app))
        app = build_http_app(mcp_settings, api, store)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=MCP_ORIGIN) as web:
                yield web, store


async def test_the_owner_signs_in_with_the_passphrase_alone(seat) -> None:
    web, _ = seat
    page = await web.get("/login")
    assert page.status_code == 200
    assert "Vademecum passphrase" in page.text and "handle" not in page.text.lower()

    browser_like = {"Origin": MCP_ORIGIN, "Sec-Fetch-Site": "same-origin"}
    wrong = await web.post("/login", data={"passphrase": "not it"}, headers=browser_like)
    assert wrong.status_code == 200 and "not right" in wrong.text
    assert (await web.get("/")).status_code == 303, "no session, no desk"

    right = await web.post("/login", data={"passphrase": PASS}, headers=browser_like)
    assert right.status_code == 303 and "vademecum_desk" in right.headers.get("set-cookie", "")
    cookie = right.cookies.get("vademecum_desk")
    desk = await web.get("/", cookies={"vademecum_desk": cookie})
    assert desk.status_code == 200 and "Vademecum desk" in desk.text
    today = await web.get("/api/today", cookies={"vademecum_desk": cookie})
    assert today.status_code == 200 and "worth_a_look" in today.json()


async def test_sync_passes_through_the_gateway_on_the_peer_token_alone(seat) -> None:
    web, _ = seat
    assert (await web.get("/api/sync/status")).status_code == 404
    assert (await web.get("/api/sync/status", headers={"X-Vademecum-Sync": "wrong"})).status_code == 404
    status = await web.get("/api/sync/status", headers={"X-Vademecum-Sync": "the-peer-token-the-mac-presents"})
    assert status.status_code == 200
    assert status.json()["role"] == "sea" and status.json()["node_id"].startswith("node_")
    changes = await web.get("/api/sync/changes?since=0", headers={"X-Vademecum-Sync": "the-peer-token-the-mac-presents"})
    assert changes.status_code == 200 and changes.json()["done"] is True
