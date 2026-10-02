"""Multi tenancy end to end (ADR 0010): invites, learner sign-in, tokens that
name a learner, the API resolving them from the shared store, and two
learners who cannot see each other through the tools.

The MCP HTTP application and the API run in-process with no socket. The MCP
calls go over the real streamable HTTP endpoint with a real token, because the
in-memory client bypasses authorization and this is the one place where the
token is the point.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from tests.mcp_support import API_ORIGIN, MCP_ORIGIN
from tests.test_mcp_auth import REDIRECT, pkce, register, start_authorization

from fake_appserver import AccountScript, ScriptedTransport, factory  # noqa: E402
from fake_model import FakeProvider  # noqa: E402
from vademecum.app import create_app  # noqa: E402
from vademecum.config import Settings as ApiSettings  # noqa: E402
from vademecum.tenancy import LEARNERS_DIRNAME, SharedStoreResolver  # noqa: E402
from vademecum_mcp.api_client import ApiClient  # noqa: E402
from vademecum_mcp.auth.store import LOCKOUT_ATTEMPTS, AccessStore, InviteError  # noqa: E402
from vademecum_mcp.config import McpSettings  # noqa: E402
from vademecum_mcp.server import build_http_app  # noqa: E402

pytestmark = pytest.mark.anyio

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


@pytest.fixture
def multi_mcp_settings(tmp_path: Path) -> McpSettings:
    return McpSettings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=8765,
        mcp_port=8766,
        mcp_public_url=MCP_ORIGIN,
        tenancy="multi",
    )


@pytest.fixture
def multi_api_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=8765,
        model_provider="host",
        tenancy="multi",
    )


@pytest.fixture
def store(multi_mcp_settings: McpSettings) -> AccessStore:
    return AccessStore(multi_mcp_settings.access_db_path)


@pytest.fixture
async def stack(multi_mcp_settings: McpSettings, multi_api_settings: ApiSettings, store: AccessStore, provider: FakeProvider):
    """The API (multi, host mode) behind the MCP HTTP app (multi), one store."""
    resolver = SharedStoreResolver(multi_api_settings.resolve_data_dir() / "mcp" / "access.sqlite3")
    api_app = create_app(
        multi_api_settings,
        transport_factory=factory(ScriptedTransport(responder=AccountScript())),
        provider_factory=lambda: provider,
        token_resolver=resolver,
    )
    async with api_app.router.lifespan_context(api_app):
        api = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=api_app))
        mcp_app = build_http_app(multi_mcp_settings, api, store)
        async with mcp_app.router.lifespan_context(mcp_app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp_app), base_url=MCP_ORIGIN) as web:
                yield web, resolver, multi_api_settings


async def sign_up(web: httpx.AsyncClient, store: AccessStore, handle: str, passphrase: str) -> dict:
    """A brand-new learner: invite, register on the consent page, get tokens."""
    client = await register(web, name=f"ChatGPT for {handle}")
    verifier, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    code = store.create_invite()
    approved = await web.post(
        "/consent",
        data={
            "request": request_id,
            "action": "register",
            "invite": code,
            "new_handle": handle,
            "new_passphrase": passphrase,
            "new_passphrase_again": passphrase,
        },
    )
    assert approved.status_code == 302, approved.text
    return await exchange(web, client, verifier, approved)


async def sign_in(web: httpx.AsyncClient, handle: str, passphrase: str) -> httpx.Response:
    client = await register(web)
    verifier, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    response = await web.post(
        "/consent",
        data={"request": request_id, "action": "approve", "handle": handle, "passphrase": passphrase},
    )
    response.extensions["exchange"] = (client, verifier)  # type: ignore[index]
    return response


async def exchange(web: httpx.AsyncClient, client: dict, verifier: str, approved: httpx.Response) -> dict:
    query = parse_qs(urlsplit(approved.headers["location"]).query)
    token = await web.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": query["code"][0],
            "redirect_uri": REDIRECT,
            "client_id": client["client_id"],
            "code_verifier": verifier,
            "resource": MCP_ORIGIN + "/mcp",
        },
    )
    assert token.status_code == 200, token.text
    return token.json()


def _payload(response: httpx.Response) -> dict:
    """The JSON-RPC message in a JSON or an SSE reply."""
    if response.headers.get("content-type", "").startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no message in reply: {response.text[:200]}")


class Assistant:
    """A minimal streamable-HTTP MCP client holding one learner's token."""

    def __init__(self, web: httpx.AsyncClient, token: str) -> None:
        self.web = web
        self.token = token
        self.session: str | None = None
        self.counter = 0

    def _headers(self) -> dict[str, str]:
        headers = dict(MCP_HEADERS, Authorization=f"Bearer {self.token}")
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        return headers

    async def rpc(self, method: str, params: dict | None = None, *, notification: bool = False) -> httpx.Response:
        message: dict = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            self.counter += 1
            message["id"] = self.counter
        return await self.web.post("/mcp", json=message, headers=self._headers())

    async def connect(self) -> None:
        response = await self.rpc(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        )
        assert response.status_code == 200, response.text
        self.session = response.headers.get("mcp-session-id")
        await self.rpc("notifications/initialized", notification=True)

    async def call(self, name: str, arguments: dict | None = None) -> dict:
        response = await self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        assert response.status_code == 200, response.text
        message = _payload(response)
        assert "result" in message, message
        result = message["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    async def call_expecting_error(self, name: str, arguments: dict | None = None) -> str:
        response = await self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        message = _payload(response)
        result = message["result"]
        assert result.get("isError"), result
        return "\n".join(block.get("text", "") for block in result.get("content", []))


# --- accounts -------------------------------------------------------------------


async def test_the_store_path_is_the_one_the_api_reads(multi_mcp_settings: McpSettings, multi_api_settings: ApiSettings) -> None:
    assert multi_mcp_settings.access_db_path == multi_api_settings.resolve_data_dir() / "mcp" / "access.sqlite3"


async def test_an_invite_becomes_a_learner_and_a_token_that_names_them(stack, store: AccessStore) -> None:
    web, resolver, _ = stack
    tokens = await sign_up(web, store, "ada", "correct horse battery staple")
    learner = resolver.resolve(tokens["access_token"])
    assert learner is not None and learner.startswith("lrn_")
    listed = store.list_learners()
    assert [entry.handle for entry in listed] == ["ada"]
    assert listed[0].id == learner
    assert store.summary()["learners"] == 1


async def test_an_invite_is_single_use_and_a_handle_is_unique(stack, store: AccessStore) -> None:
    web, _, _ = stack
    await sign_up(web, store, "ada", "correct horse battery staple")
    client = await register(web)
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    with pytest.raises(InviteError):
        store.redeem_invite(code="nope-nope-nope-nope", handle="bob", passphrase="correct horse battery staple")
    taken = await web.post(
        "/consent",
        data={
            "request": request_id,
            "action": "register",
            "invite": store.create_invite(),
            "new_handle": "ada",
            "new_passphrase": "another long passphrase",
            "new_passphrase_again": "another long passphrase",
        },
    )
    assert taken.status_code == 200
    assert "already taken" in taken.text
    weak = await web.post(
        "/consent",
        data={
            "request": request_id,
            "action": "register",
            "invite": store.create_invite(),
            "new_handle": "bob",
            "new_passphrase": "short",
            "new_passphrase_again": "short",
        },
    )
    assert "at least 12" in weak.text


async def test_a_learner_signs_in_again_and_a_wrong_passphrase_locks_only_them(stack, store: AccessStore) -> None:
    web, resolver, _ = stack
    first = await sign_up(web, store, "ada", "correct horse battery staple")
    await sign_up(web, store, "bob", "another long passphrase")

    again = await sign_in(web, "ada", "correct horse battery staple")
    assert again.status_code == 302
    client, verifier = again.extensions["exchange"]
    second = await exchange(web, client, verifier, again)
    assert resolver.resolve(second["access_token"]) == resolver.resolve(first["access_token"])

    for _ in range(LOCKOUT_ATTEMPTS):
        refused = await sign_in(web, "ada", "wrong passphrase here")
        assert refused.status_code == 200 and "do not match" in refused.text
    locked = await sign_in(web, "ada", "correct horse battery staple")
    assert locked.status_code == 429
    bob = await sign_in(web, "bob", "another long passphrase")
    assert bob.status_code == 302, "one learner's lockout is not another's"


async def test_a_reset_passphrase_signs_everything_out_and_signs_in_again(stack, store: AccessStore) -> None:
    web, resolver, _ = stack
    tokens = await sign_up(web, store, "ada", "correct horse battery staple")
    for _ in range(LOCKOUT_ATTEMPTS):
        await sign_in(web, "ada", "wrong passphrase here")
    assert store.reset_passphrase("ada", "a brand new passphrase") >= 2
    assert resolver.resolve(tokens["access_token"]) is None, "the old sessions are gone"
    stale = await sign_in(web, "ada", "correct horse battery staple")
    assert stale.status_code == 200 and "do not match" in stale.text
    fresh = await sign_in(web, "ada", "a brand new passphrase")
    assert fresh.status_code == 302, "the reset also clears the lockout"
    with pytest.raises(InviteError):
        store.reset_passphrase("nobody", "a brand new passphrase")
    with pytest.raises(InviteError):
        store.reset_passphrase("ada", "short")


async def test_disabling_a_learner_revokes_their_tokens_and_blocks_sign_in(stack, store: AccessStore) -> None:
    web, resolver, _ = stack
    tokens = await sign_up(web, store, "ada", "correct horse battery staple")
    assert store.disable_learner("ada") >= 2
    assert resolver.resolve(tokens["access_token"]) is None
    refused = await sign_in(web, "ada", "correct horse battery staple")
    assert refused.status_code == 200 and "do not match" in refused.text


async def test_the_sign_in_page_has_both_forms_and_no_owner_passphrase(stack) -> None:
    web, _, _ = stack
    client = await register(web)
    _, challenge = pkce()
    request_id = await start_authorization(web, client["client_id"], challenge)
    page = await web.get("/consent", params={"request": request_id})
    assert page.status_code == 200
    assert "First time here?" in page.text
    assert 'name="invite"' in page.text
    assert "Vademecum passphrase" not in page.text
    assert "patient identifiers" in page.text


# --- isolation through the tools ------------------------------------------------


async def test_two_learners_cannot_see_each_other_through_the_tools(stack, store: AccessStore) -> None:
    web, _, api_settings = stack
    ada = Assistant(web, (await sign_up(web, store, "ada", "correct horse battery staple"))["access_token"])
    bob = Assistant(web, (await sign_up(web, store, "bob", "another long passphrase"))["access_token"])
    await ada.connect()
    await bob.connect()

    pile = await ada.call("create_pile", {"title": "Sepsis", "confidence": "mid"})
    flag = await ada.call("flag_knowledge_gap", {"text": "Unsure about vasopressors"})
    assert (await ada.call("list_piles"))["count"] == 1

    assert (await bob.call("list_piles"))["count"] == 0
    assert (await bob.call("list_flags"))["count"] == 0
    assert (await bob.call("get_today"))["open_flag_count"] == 0
    assert "No such" in await bob.call_expecting_error("get_pile", {"pile_id": pile["id"]})
    assert "No such" in await bob.call_expecting_error("update_flag", {"flag_id": flag["id"], "status": "addressed"})

    root = api_settings.resolve_data_dir() / LEARNERS_DIRNAME
    assert len(list(root.iterdir())) == 2

    # Nothing a tool returns names the learner or a path.
    for body in (await ada.call("get_today"), await ada.call("list_piles"), pile, flag):
        text = json.dumps(body)
        assert "lrn_" not in text and "/Users/" not in text and str(root) not in text


async def test_a_call_without_a_learner_token_reaches_no_workspace(stack) -> None:
    web, _, _ = stack
    nobody = Assistant(web, "not-a-token")
    response = await nobody.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}})
    assert response.status_code == 401


async def test_single_tenancy_tokens_never_resolve_to_a_learner(mcp_settings: McpSettings, tmp_path: Path) -> None:
    """A token issued by a single-owner server has no learner and cannot open one."""
    store = AccessStore(mcp_settings.access_db_path)
    issued = store.issue_tokens(client_id="c", scopes=["vademecum"], resource=None, access_ttl=60, refresh_ttl=60)
    resolver = SharedStoreResolver(mcp_settings.access_db_path)
    assert resolver.resolve(issued.access_token) is None
