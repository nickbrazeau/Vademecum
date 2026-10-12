"""Signing in with Face ID (ADR 0036): a passkey added from a signed-in session signs the
owner in; a challenge works once, for five minutes, for this address only; failures count
towards the same lockout as the passphrase; Sign out everywhere ends every session.

The phone is played by a software authenticator: a real P-256 key pair producing real
WebAuthn registration and assertion objects, so the server's checks are the real ones.
"""

from __future__ import annotations

import base64
import hashlib
import json
import struct
import time

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from tests.mcp_support import MCP_ORIGIN
from tests.test_mcp_desk import PASS, dist, seat  # noqa: F401  (fixtures)
from vademecum_mcp.auth import store as store_module
from vademecum_mcp.desk import COOKIE

pytestmark = pytest.mark.anyio
SAME = {"Origin": MCP_ORIGIN, "Sec-Fetch-Site": "same-origin"}
RP_ID = "127.0.0.1"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Phone:
    """A passkey authenticator in software: what Face ID does, without the face."""

    def __init__(self, *, rp_id: str = RP_ID, origin: str = MCP_ORIGIN, counts: bool = True) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = hashlib.sha256(str(time.time_ns()).encode()).digest()[:16]
        self.rp_id = rp_id
        self.origin = origin
        self.count = 0
        self.counts = counts

    def _cose(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, options: dict) -> dict:
        client = self._client_data("webauthn.create", options["challenge"])
        rp_hash = hashlib.sha256(self.rp_id.encode()).digest()
        attested = bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + self._cose()
        auth_data = rp_hash + bytes([0x01 | 0x04 | 0x40]) + struct.pack(">I", 0) + attested
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "attestationObject": b64(attestation), "transports": ["internal"]},
            "clientExtensionResults": {},
        }

    def get(self, options: dict, *, sign_count: int | None = None) -> dict:
        if self.counts:
            self.count += 1
        count = self.count if sign_count is None else sign_count
        client = self._client_data("webauthn.get", options["challenge"])
        auth_data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x01 | 0x04]) + struct.pack(">I", count)
        signature = self.key.sign(auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": b64(self.credential_id), "rawId": b64(self.credential_id), "type": "public-key",
            "response": {"clientDataJSON": b64(client), "authenticatorData": b64(auth_data), "signature": b64(signature), "userHandle": b64(b"owner")},
            "clientExtensionResults": {},
        }


async def signed_in(web) -> str:
    right = await web.post("/login", data={"passphrase": PASS}, headers=SAME)
    assert right.status_code == 303
    cookie = right.cookies.get(COOKIE)
    web.cookies.clear()  # each request below says which session it carries, if any
    return cookie


async def add_face_id(web, cookie: str, phone: Phone) -> None:
    begun = (await web.post("/passkeys/options", headers=SAME, cookies={COOKIE: cookie})).json()
    added = await web.post(
        "/passkeys/add", json={"challenge_id": begun["challenge_id"], "credential": phone.create(begun["options"]), "label": "iPhone"},
        headers=SAME, cookies={COOKIE: cookie},
    )
    assert added.status_code == 200, added.text


async def face_id(web, phone: Phone, **kwargs):
    begun = (await web.post("/login/passkey/options", headers=SAME)).json()
    return await web.post("/login/passkey", json={"challenge_id": begun["challenge_id"], "credential": phone.get(begun["options"], **kwargs)}, headers=SAME)


async def test_a_passkey_added_once_signs_the_owner_in_with_face_id(seat) -> None:  # noqa: F811
    web, store = seat
    assert "Face ID" not in (await web.get("/login")).text, "no passkey yet: the passphrase"
    phone = Phone()
    await add_face_id(web, await signed_in(web), phone)
    page = await web.get("/login")
    assert "Sign in with Face ID" in page.text and "Use passphrase instead" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]

    signed = await face_id(web, phone)
    assert signed.status_code == 200 and signed.json()["redirect"] == "/"
    cookie = signed.cookies.get(COOKIE)
    assert (await web.get("/api/today", cookies={COOKIE: cookie})).status_code == 200
    assert store.passkeys()[0]["last_used_at"] is not None


async def test_adding_a_passkey_needs_a_session(seat) -> None:  # noqa: F811
    web, _ = seat
    assert (await web.post("/passkeys/options", headers=SAME)).status_code == 401
    assert (await web.post("/passkeys/add", json={}, headers=SAME)).status_code == 401
    assert (await web.get("/passkeys")).status_code == 303


async def test_a_challenge_works_once_and_not_after_five_minutes(seat, monkeypatch) -> None:  # noqa: F811
    web, _ = seat
    phone = Phone()
    await add_face_id(web, await signed_in(web), phone)
    begun = (await web.post("/login/passkey/options", headers=SAME)).json()
    answer = phone.get(begun["options"])
    assert (await web.post("/login/passkey", json={"challenge_id": begun["challenge_id"], "credential": answer}, headers=SAME)).status_code == 200
    again = await web.post("/login/passkey", json={"challenge_id": begun["challenge_id"], "credential": answer}, headers=SAME)
    assert again.status_code == 401, "replayed"

    begun = (await web.post("/login/passkey/options", headers=SAME)).json()
    later = time.time() + store_module.PASSKEY_CHALLENGE_SECONDS + 1
    monkeypatch.setattr(store_module.time, "time", lambda: later)
    stale = await web.post("/login/passkey", json={"challenge_id": begun["challenge_id"], "credential": phone.get(begun["options"])}, headers=SAME)
    assert stale.status_code == 401, "expired"


async def test_a_passkey_for_another_site_or_unknown_is_refused(seat) -> None:  # noqa: F811
    web, _ = seat
    phone = Phone()
    await add_face_id(web, await signed_in(web), phone)
    elsewhere = Phone(origin="https://phishing.example")
    elsewhere.key, elsewhere.credential_id = phone.key, phone.credential_id
    assert (await face_id(web, elsewhere)).status_code == 401, "another origin"
    other_rp = Phone(rp_id="phishing.example")
    other_rp.key, other_rp.credential_id = phone.key, phone.credential_id
    assert (await face_id(web, other_rp)).status_code == 401, "another relying party"
    assert (await face_id(web, Phone())).status_code == 401, "a passkey this copy never saw"


async def test_a_sign_count_going_backwards_is_refused(seat) -> None:  # noqa: F811
    web, _ = seat
    phone = Phone()
    await add_face_id(web, await signed_in(web), phone)
    assert (await face_id(web, phone, sign_count=5)).status_code == 200
    assert (await face_id(web, phone, sign_count=3)).status_code == 401, "a cloned authenticator"


async def test_five_failed_passkeys_lock_sign_in_like_the_passphrase(seat) -> None:  # noqa: F811
    web, _ = seat
    phone = Phone()
    await add_face_id(web, await signed_in(web), phone)
    for _ in range(5):
        assert (await face_id(web, Phone())).status_code == 401
    locked = await face_id(web, phone)
    assert locked.status_code == 429, "locked for the window, the right passkey too"
    assert (await web.post("/login", data={"passphrase": PASS}, headers=SAME)).status_code == 429


async def test_a_removed_passkey_no_longer_signs_in(seat) -> None:  # noqa: F811
    web, store = seat
    cookie = await signed_in(web)
    phone = Phone()
    await add_face_id(web, cookie, phone)
    removed = await web.post("/passkeys/remove", data={"credential_id": b64(phone.credential_id)}, headers=SAME, cookies={COOKIE: cookie})
    assert removed.status_code == 303 and store.passkeys() == []
    assert (await face_id(web, phone)).status_code == 401


async def test_sign_out_everywhere_ends_every_session(seat) -> None:  # noqa: F811
    web, _ = seat
    first, second = await signed_in(web), await signed_in(web)
    out = await web.post("/logout/everywhere", headers=SAME, cookies={COOKIE: first})
    assert out.status_code == 303 and out.headers["location"] == "/login"
    for cookie in (first, second):
        assert (await web.get("/api/today", cookies={COOKIE: cookie})).status_code == 401


async def test_the_connection_approval_offers_face_id_and_keeps_the_passphrase(seat) -> None:  # noqa: F811
    web, store = seat
    await add_face_id(web, await signed_in(web), Phone())
    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    info = OAuthClientInformationFull(client_id="c1", client_name="ChatGPT", redirect_uris=[AnyUrl("https://chatgpt.com/cb")])
    store.save_client(info)
    params = AuthorizationParams(
        state="s", scopes=["vademecum"], code_challenge="x" * 43, redirect_uri=AnyUrl("https://chatgpt.com/cb"),
        redirect_uri_provided_explicitly=True, resource=None,
    )
    request_id = store.create_pending("c1", params)
    page = await web.get(f"/consent?request={request_id}")
    assert "Approve with Face ID" in page.text and "Use passphrase instead" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]


async def test_the_public_health_check_says_nothing_about_this_copy(seat) -> None:  # noqa: F811
    web, _ = seat
    assert (await web.get("/health")).json() == {"status": "ok"}


# --- what answers without a session (ADR 0036) -----------------------------------------

# Paths that may answer 2xx to someone not signed in, and why.
PUBLIC = {
    "/login": "the sign-in page",
    "/health": "says ok, nothing else",
    "/auth/passkey.js": "the sign-in page's script",
    "/login/passkey/options": "a one-time challenge, before anyone is known",
    "/.well-known/oauth-authorization-server": "OAuth discovery, for ChatGPT and Claude",
    "/.well-known/oauth-protected-resource/mcp": "OAuth discovery",
    "/.well-known/oauth-protected-resource": "OAuth discovery",
    "/register": "OAuth client registration",
    "/authorize": "OAuth: leads to the consent page",
    "/token": "OAuth: needs a code or refresh token",
    "/revoke": "OAuth: revokes a token it is given",
    "/consent": "asks for Face ID or the passphrase",
}


async def test_only_the_pinned_paths_answer_without_a_session(tmp_path) -> None:
    import httpx

    from vademecum_mcp.api_client import ApiClient
    from vademecum_mcp.auth.store import AccessStore
    from vademecum_mcp.config import McpSettings
    from vademecum_mcp.server import build_http_app
    from tests.mcp_support import API_ORIGIN

    settings = McpSettings(
        data_dir=tmp_path / "data", host="127.0.0.1", port=8765, mcp_port=8766,
        mcp_public_url=MCP_ORIGIN, tenancy="single", mcp_desk_dist=None,
    )
    store = AccessStore(settings.access_db_path)
    store.set_passphrase(PASS)
    api = ApiClient(API_ORIGIN, timeout=5, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"leaked": True})))
    app = build_http_app(settings, api, store)
    inner = app.routes[0].app.app  # Mount("/") -> HostGuard -> the gateway's own routes
    paths = sorted({route.path for route in inner.routes if hasattr(route, "path") and "{" not in route.path})
    assert "/passkeys" in paths and "/login" in paths, "the enumeration sees the gateway's own routes"
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=MCP_ORIGIN) as anyone:
            opened = []
            for path in paths + ["/api/today", "/api/encyclopedia", "/api/notes", "/api/sync/status", "/mcp"]:
                for method in ("GET", "POST"):
                    response = await anyone.request(method, path, headers=SAME)
                    if 200 <= response.status_code < 300 and path not in PUBLIC:
                        opened.append(f"{method} {path} {response.status_code}")
                    assert "leaked" not in response.text, f"{method} {path} reached the API without a session"
    assert opened == [], opened
