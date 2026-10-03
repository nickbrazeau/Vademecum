"""The desk, through the gateway (ADR 0011). Multi tenancy only.

The hosted web app needs a sign-in and a way to reach the API, and files can
only be uploaded from a browser. Rather than teach the React app OAuth and
hand a token to JavaScript, this process -- already the public host, already
the place that knows what a token is -- serves the desk itself:

* ``GET /login`` is the same sign-in as the consent page (handle and
  passphrase, or an invite the first time), rendered by the same code.
* A successful sign-in mints a desk token in the same access store the API
  reads, with no refresh token and no resource, and puts it in an
  ``HttpOnly``, ``SameSite=Strict`` cookie. It is a session and a credential
  at once: the API resolves it like any other token; the MCP endpoint refuses
  it, because it names no resource.
* ``/api/*`` is proxied to the API on loopback with the cookie's token in
  ``X-Vademecum-Token``, request and response bodies streamed, so an upload
  never lands in this process's memory. Mutating requests must come from this
  origin, checked against ``Origin`` and ``Sec-Fetch-Site`` on top of the
  cookie's ``SameSite``.
* The built web app is served at the root, with the shell as the fallback for
  client routes and a redirect to ``/login`` for a browser with no session.

Nothing here logs a token, a cookie, a handle or a body.
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from mcp.server import MCPServer
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from .api_client import TOKEN_HEADER, ApiClient, ApiError
from .auth import consent, pages
from .auth.store import OWNER_SUBJECT, AccessStore, InviteError
from .config import SCOPE

logger = logging.getLogger("vademecum_mcp.desk")

COOKIE = "vademecum_desk"
SYNC_HEADER = "X-Vademecum-Sync"
DESK_CLIENT = "desk"
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Headers that describe the message, not the hop. Everything else is dropped in
# both directions; in particular the API's `Host` is its own, and the browser's
# cookies never reach the API.
FORWARDED_REQUEST_HEADERS = ("content-type", "content-length", "accept")
FORWARDED_RESPONSE_HEADERS = ("content-type", "content-length", "content-disposition", "cache-control", "x-correlation-id")

# What the service worker is allowed to cache, and therefore what may be
# served with a long cache lifetime: hashed build output only.
IMMUTABLE_PREFIX = "assets/"

UNAUTHENTICATED = {
    "error": {
        "code": "unauthenticated",
        "message": "You are not signed in to Vademecum. Sign in at /login.",
        "fields": [],
    }
}
CROSS_ORIGIN = {
    "error": {
        "code": "cross_origin",
        "message": "That request did not come from Vademecum, so it was refused.",
        "fields": [],
    }
}


def register(
    mcp: MCPServer,
    store: AccessStore,
    *,
    api: ApiClient,
    public_url: str,
    session_ttl: int,
    dist: Path | None,
    tenancy: str = "multi",
) -> None:
    origin = public_url.rstrip("/").lower()
    single = tenancy == "single"
    secure = urlsplit(public_url).scheme == "https"

    def token_of(request: Request) -> str | None:
        """The desk token in the cookie, if it is live. Never logged."""
        raw = request.cookies.get(COOKIE, "")
        if not raw:
            return None
        record = store.load_token(raw, "access")
        if record is None or record.client_id != DESK_CLIENT or not record.learner_id:
            return None
        return raw

    def set_session(response: Response, token: str) -> Response:
        response.set_cookie(
            COOKIE,
            token,
            max_age=session_ttl,
            httponly=True,
            secure=secure,
            samesite="strict",
            path="/",
        )
        return response

    def login_page(error: str = "", *, status: int = 200) -> Response:
        if single:
            # One learner, the owner (ADR 0017): the same passphrase the
            # assistants are approved with.
            form = pages.owner_form(LOGIN_PATH, "")
            lead = "Your Vademecum: Today, your sources, the Tutor, the Improvement Map and the Case Series."
        else:
            form = pages.learner_forms(LOGIN_PATH, "", approve_label="Sign in", register_label="Create my workspace", deny=False)
            lead = "Your desk: upload material, manage your sources, export or delete your workspace."
        body = f"""
<h1>Sign in to Vademecum</h1>
<p>{lead} Building and grading happen in your assistant. {pages.BOUNDARY}</p>
{pages.notice(error)}
{form}
"""
        return pages.page(body, status=status, raw=True)

    # --- sign in, sign out ----------------------------------------------------

    @mcp.custom_route(LOGIN_PATH, methods=["GET", "POST"], include_in_schema=False)
    async def login(request: Request) -> Response:
        if request.method == "GET":
            if token_of(request) is not None:
                return RedirectResponse("/", status_code=303, headers=pages.SECURITY_HEADERS)
            return login_page()
        if not same_origin(request, origin):
            return login_page("That request did not come from this site.", status=403)
        raw = await request.form()
        form = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
        if single:
            remaining = store.lockout_remaining()
            if remaining:
                logger.warning("desk_locked_out")
                return login_page(consent.locked(remaining), status=429)
            if not store.verify_passphrase(form.get("passphrase", "")):
                logger.info("desk_sign_in_refused")
                return login_page(consent.WRONG)
            learner_id = OWNER_SUBJECT
        elif form.get("action") == "register":
            outcome = consent.register_learner(store, form)
            if isinstance(outcome, InviteError):
                logger.info("desk_registration_refused")
                return login_page(outcome.message)
            learner_id = outcome
            logger.info("learner_registered")
        else:
            handle = form.get("handle", "").strip().lower()
            remaining = store.lockout_remaining(f"handle:{handle}")
            if remaining:
                logger.warning("desk_locked_out")
                return login_page(consent.locked(remaining), status=429)
            found = store.authenticate(handle=handle, passphrase=form.get("passphrase", ""))
            if found is None:
                logger.info("desk_sign_in_refused")
                return login_page(consent.WRONG_LEARNER)
            learner_id = found
        issued = store.issue_tokens(
            client_id=DESK_CLIENT,
            scopes=[SCOPE],
            resource=None,
            access_ttl=session_ttl,
            refresh_ttl=session_ttl,
            learner_id=learner_id,
            with_refresh=False,
        )
        logger.info("desk_signed_in")
        return set_session(RedirectResponse("/", status_code=303, headers=pages.SECURITY_HEADERS), issued.access_token)

    @mcp.custom_route(LOGOUT_PATH, methods=["POST"], include_in_schema=False)
    async def logout(request: Request) -> Response:
        if not same_origin(request, origin):
            return JSONResponse(CROSS_ORIGIN, status_code=403, headers=pages.SECURITY_HEADERS)
        token = token_of(request)
        if token is not None:
            store.revoke_token(token)
            logger.info("desk_signed_out")
        response = RedirectResponse(LOGIN_PATH, status_code=303, headers=pages.SECURITY_HEADERS)
        response.delete_cookie(COOKIE, path="/")
        return response

    # --- sync, passed through (ADR 0015, 0017) --------------------------------------
    #
    # The home node reaches the away node's sync routes through this address.
    # No desk session: the API checks the peer token itself, and answers as if
    # the routes did not exist when it is wrong or absent.

    @mcp.custom_route("/api/sync/{path:path}", methods=["GET", "POST", "PUT"], include_in_schema=False)
    async def sync_passthrough(request: Request) -> Response:
        peer = request.headers.get(SYNC_HEADER)
        if peer is None:
            return JSONResponse({"error": {"code": "not_found", "message": "No such route."}}, status_code=404, headers={"Cache-Control": "no-store"})
        headers = {name: value for name, value in request.headers.items() if name.lower() in FORWARDED_REQUEST_HEADERS}
        headers[SYNC_HEADER] = peer
        try:
            upstream = await api.forward(
                request.method,
                f"/api/sync/{request.path_params['path']}",
                params=request.query_params,
                headers=headers,
                content=request.stream() if request.method in MUTATING else None,
            )
        except ApiError as exc:
            return JSONResponse({"error": {"code": exc.code, "message": exc.message, "fields": []}}, status_code=503, headers={"Cache-Control": "no-store"})
        passed = {name: value for name, value in upstream.headers.items() if name in FORWARDED_RESPONSE_HEADERS}
        passed.setdefault("cache-control", "no-store")
        return StreamingResponse(upstream.body, status_code=upstream.status, headers=passed, background=BackgroundTask(upstream.aclose))

    # --- the API, proxied ----------------------------------------------------------

    @mcp.custom_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    async def proxy(request: Request) -> Response:
        token = token_of(request)
        if token is None:
            return JSONResponse(UNAUTHENTICATED, status_code=401, headers={"Cache-Control": "no-store"})
        if request.method in MUTATING and not same_origin(request, origin):
            logger.info("desk_cross_origin_refused")
            return JSONResponse(CROSS_ORIGIN, status_code=403, headers={"Cache-Control": "no-store"})
        path = request.path_params["path"]
        headers = {name: value for name, value in request.headers.items() if name.lower() in FORWARDED_REQUEST_HEADERS}
        headers[TOKEN_HEADER] = token
        try:
            upstream = await api.forward(
                request.method,
                f"/api/{path}",
                params=request.query_params,
                headers=headers,
                content=request.stream() if request.method in MUTATING else None,
            )
        except ApiError as exc:
            return JSONResponse(
                {"error": {"code": exc.code, "message": exc.message, "fields": []}},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        passed = {name: value for name, value in upstream.headers.items() if name in FORWARDED_RESPONSE_HEADERS}
        passed.setdefault("cache-control", "no-store")
        return StreamingResponse(
            upstream.body,
            status_code=upstream.status,
            headers=passed,
            background=BackgroundTask(upstream.aclose),
        )

    # --- the web app ----------------------------------------------------------------

    if dist is None:
        return
    root = dist.resolve()

    @mcp.custom_route("/{path:path}", methods=["GET"], include_in_schema=False)
    async def desk(request: Request) -> Response:
        path = request.path_params["path"]
        candidate = (root / path).resolve() if path else root
        if path and root in candidate.parents and candidate.is_file():
            headers = (
                {"Cache-Control": "public, max-age=31536000, immutable"}
                if path.startswith(IMMUTABLE_PREFIX)
                else {"Cache-Control": "no-cache"}
            )
            return FileResponse(candidate, headers=headers)
        if token_of(request) is None:
            return RedirectResponse(LOGIN_PATH, status_code=303, headers=pages.SECURITY_HEADERS)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-store"})


def same_origin(request: Request, origin: str) -> bool:
    """A browser request from this site, and nowhere else.

    ``Origin`` when present must match; ``Sec-Fetch-Site`` when present must
    not say cross-site. A request with neither (an old client, a form post
    from this site in some browsers) passes on the strength of the cookie's
    ``SameSite=Strict``, which such a browser also honours.
    """
    sent = request.headers.get("origin")
    # An explicit `null` is a refusal, not an absence (ADR 0002): it is what a
    # sandboxed frame, a data: document or a no-referrer submission sends.
    if sent is not None and sent.strip().lower() != origin:
        return False
    fetched = request.headers.get("sec-fetch-site", "").lower()
    if fetched == "cross-site":
        return False
    return True
