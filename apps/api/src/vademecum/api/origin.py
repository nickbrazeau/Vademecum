"""Same-origin enforcement.

The API answers only loopback, but "only loopback" is not "only Vademecum". A
page on any origin the browser has open can POST a simple form -- including
``multipart/form-data``, which is never preflighted -- to 127.0.0.1. And a
hostname the attacker controls can be rebound to 127.0.0.1, so a request arrives
here carrying a ``Host`` the owner never typed.

Two checks:

* ``Host`` is validated on **every** request, including GETs. A read is not
  harmless when it returns the owner's library: an export listing or a Today
  payload fetched through a rebound name is a disclosure. Rebinding is the one
  attack that reaches reads, so the Host check cannot be limited to writes.
* ``Origin`` is validated on mutating requests. An explicit ``null`` origin
  (a sandboxed iframe, a ``data:`` document, some redirect chains) is a
  *refusal*, not an absence -- treating it as absent is how a cross-origin write
  gets waved through. ``Referer`` is consulted only when ``Origin`` is absent
  entirely.

Parsing failures fail closed with a 403, never a 500. There is no CORS
middleware anywhere in this application, so a cross-origin *reader* gets
nothing; this module is about the cross-origin *writer* and the rebound *reader*.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import FastAPI, status
from fastapi.responses import JSONResponse
from starlette.requests import Request
from starlette.responses import Response

MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

REFUSAL = (
    "That request did not come from Vademecum running on this Mac, so it was "
    "refused before anything was read, written or sent."
)

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def allowed_authorities(port: int, web_port: int) -> frozenset[str]:
    """Every host:port this process legitimately answers to.

    Both the API port and the configured web port, because the browser sends the
    origin of the *page*, and in development that page is served by Vite.
    """
    ports = {int(port), int(web_port)}
    authorities = {f"{host}:{value}" for host in LOOPBACK_HOSTS for value in ports}
    if 80 in ports:
        # A default-port authority may arrive with the port elided.
        authorities.update(LOOPBACK_HOSTS)
    return frozenset(authorities)


def authority_of(value: str) -> str | None:
    """The ``host:port`` of *value*, or None if it is not usable.

    Returns None -- meaning "refuse" -- for anything unparseable, any non-HTTP
    scheme, and the literal ``null`` origin. Never raises: a malformed header is
    a refusal, not a 500.
    """
    candidate = (value or "").strip()
    if not candidate or candidate.lower() == "null":
        return None
    try:
        parts = urlsplit(candidate if "//" in candidate else f"//{candidate}")
        if parts.scheme and parts.scheme not in {"http", "https"}:
            return None
        netloc = parts.netloc.lower()
    except ValueError:
        return None
    return netloc or None


def install(app: FastAPI, *, authorities: frozenset[str]) -> None:
    @app.middleware("http")
    async def same_origin_only(request: Request, call_next) -> Response:
        # Host first, and on every method: this is the rebinding check, and a
        # rebound GET reads the owner's library just as well as a rebound POST.
        host = request.headers.get("host")
        if host is not None:
            if authority_of(host) not in authorities:
                return _refuse("foreign_host")

        if request.method not in MUTATING:
            return await call_next(request)

        origin = request.headers.get("origin")
        if origin is not None:
            # Present-but-null is a refusal. Falling through to Referer here is
            # exactly how a sandboxed-iframe write gets accepted.
            if authority_of(origin) not in authorities:
                return _refuse("cross_origin")
        else:
            referer = request.headers.get("referer")
            if referer is not None and authority_of(referer) not in authorities:
                return _refuse("cross_origin")

        return await call_next(request)


def _refuse(code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": {"code": code, "message": REFUSAL, "fields": []}},
        headers={"Cache-Control": "no-store"},
    )
