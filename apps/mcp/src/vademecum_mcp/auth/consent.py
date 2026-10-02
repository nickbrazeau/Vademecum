"""The OAuth consent page: approve a connection from an assistant.

A client's ``/authorize`` request lands here by redirect with an unguessable,
single-use request id. The page names the client and where it will be sent
back to, says plainly what a connection can do, and asks for a credential:

* **single tenancy** -- the owner's passphrase, set once on the Mac;
* **multi tenancy** -- a learner's handle and passphrase, or, the first time,
  an invite code with a handle and passphrase of their choosing. The learner
  the page signs in is carried on the authorization code and, from there, on
  every token, which is how the API knows whose workspace to open (ADR 0010).

Approving mints an authorization code and redirects; declining redirects with
``access_denied``. No cookie, no session, no script, no external asset.

What never appears in a log: a passphrase, an invite code, the request id,
the code. A handle is logged nowhere either; a sign-in is an event name.
"""

from __future__ import annotations

import html
import logging
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.auth.provider import construct_redirect_uri
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from ..config import CONSENT_PATH
from . import pages
from .store import AccessStore, InviteError

logger = logging.getLogger("vademecum_mcp.consent")

NO_PASSPHRASE = (
    "No passphrase has been set on the Mac yet, so nothing can be approved. On "
    "the Mac, run: ./scripts/mcp.sh passphrase"
)
EXPIRED = (
    "This sign-in request has expired or was already used. Start again from the "
    "assistant."
)
WRONG = "That passphrase is not right."
WRONG_LEARNER = "That handle and passphrase do not match."
MISMATCH = "The two passphrases did not match."


def locked(remaining: int) -> str:
    return f"Too many wrong attempts. Try again in {max(1, remaining // 60)} minute(s)."


def register_learner(store: AccessStore, form: dict[str, str]) -> str | InviteError:
    """Redeem an invite from a submitted form. The learner id, or the refusal."""
    first = form.get("new_passphrase", "")
    if first != form.get("new_passphrase_again", ""):
        return InviteError(MISMATCH)
    try:
        return store.redeem_invite(
            code=form.get("invite", ""), handle=form.get("new_handle", ""), passphrase=first
        )
    except InviteError as exc:
        return exc


def register(mcp: MCPServer, store: AccessStore, *, tenancy: str = "single") -> None:
    multi = tenancy == "multi"

    @mcp.custom_route(CONSENT_PATH, methods=["GET", "POST"], include_in_schema=False)
    async def consent(request: Request) -> Response:
        if request.method == "GET":
            request_id = request.query_params.get("request", "")
            pending = store.get_pending(request_id)
            if pending is None:
                return pages.page(EXPIRED, status=400)
            return _form(store, request_id, pending.client_id, pending.params.redirect_uri, multi=multi)

        raw = await request.form()
        form = {key: str(value) for key, value in raw.items() if isinstance(value, str)}
        request_id = form.get("request", "")
        action = form.get("action", "approve")
        pending = store.get_pending(request_id)
        if pending is None:
            return pages.page(EXPIRED, status=400)
        params = pending.params

        def again(error: str, *, status: int = 200) -> Response:
            return _form(store, request_id, pending.client_id, params.redirect_uri, multi=multi, error=error, status=status)

        if action == "deny":
            store.consume_pending(request_id)
            logger.info("consent_denied")
            return _redirect(
                construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)
            )

        learner_id: str | None = None
        if not multi:
            if not store.has_passphrase():
                return pages.page(NO_PASSPHRASE, status=403)
            remaining = store.lockout_remaining()
            if remaining:
                logger.warning("consent_locked_out")
                return pages.page(locked(remaining), status=429)
            if not store.verify_passphrase(form.get("passphrase", "")):
                logger.info("consent_refused")
                return again(WRONG)
        elif action == "register":
            outcome = register_learner(store, form)
            if isinstance(outcome, InviteError):
                logger.info("consent_registration_refused")
                return again(outcome.message)
            learner_id = outcome
            logger.info("learner_registered")
        else:
            handle = form.get("handle", "").strip().lower()
            remaining = store.lockout_remaining(f"handle:{handle}")
            if remaining:
                logger.warning("consent_locked_out")
                return pages.page(locked(remaining), status=429)
            learner_id = store.authenticate(handle=handle, passphrase=form.get("passphrase", ""))
            if learner_id is None:
                logger.info("consent_refused")
                return again(WRONG_LEARNER)

        consumed = store.consume_pending(request_id)
        if consumed is None:
            return pages.page(EXPIRED, status=400)
        code = store.create_code(consumed.client_id, consumed.params, learner_id=learner_id)
        logger.info("consent_approved")
        return _redirect(
            construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        )


def _redirect(url: str) -> Response:
    return RedirectResponse(url=url, status_code=302, headers=pages.CONSENT_HEADERS)


def _form(
    store: AccessStore,
    request_id: str,
    client_id: str,
    redirect_uri: object,
    *,
    multi: bool,
    error: str = "",
    status: int = 200,
) -> Response:
    client = store.get_client(client_id)
    name = html.escape((client.client_name if client and client.client_name else "").strip() or "An assistant")
    where = html.escape(urlsplit(str(redirect_uri)).netloc or "its own site")
    hidden_fields = pages.hidden("request", request_id)
    if multi:
        credentials = pages.learner_forms(
            CONSENT_PATH,
            hidden_fields,
            approve_label="Sign in and approve",
            register_label="Create my workspace and approve",
            deny=True,
        )
    else:
        credentials = pages.owner_form(CONSENT_PATH, hidden_fields)
    body = f"""
<h1>Connect {name} to Vademecum?</h1>
<p>You are approving a connection from <strong>{name}</strong>. Afterwards it is sent back to
<strong>{where}</strong>.</p>
<p>A connected assistant can read your study material and learning points, record knowledge-gap
flags, and start builds and grading on your behalf. Anything it reads becomes part of that
assistant's conversation with its provider. {pages.BOUNDARY}</p>
{pages.notice(error)}
{credentials}
"""
    return pages.page(body, status=status, raw=True, headers=pages.CONSENT_HEADERS)
