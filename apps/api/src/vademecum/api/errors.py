"""Error translation at the HTTP boundary.

Responses carry a code, a plain-language message and the correlation id that
matches the log line. They never echo the submitted value back -- a validation
error on a clinical free-text field would otherwise reflect that text into
error surfaces and, eventually, into somebody's log.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..appserver.account import DETAIL
from ..appserver.errors import BridgeError
from ..model.host import SubmissionRefused
from ..storage.common import NotFoundError
from ..storage.schematics import InvalidSchematic
from ..storage.sources import ConflictError
from ..tenancy import Unauthenticated

# How a bridge failure category becomes an HTTP status. The distinction that
# matters to a caller is "the connection is not there" (503) versus "it
# answered with something unusable" (502) versus "it never answered" (504).
BRIDGE_STATUS: dict[str, int] = {
    "timeout": status.HTTP_504_GATEWAY_TIMEOUT,
    "protocol": status.HTTP_502_BAD_GATEWAY,
    "method_not_found": status.HTTP_502_BAD_GATEWAY,
    "server_error": status.HTTP_502_BAD_GATEWAY,
    "refused": status.HTTP_502_BAD_GATEWAY,
    "login_not_supported": status.HTTP_502_BAD_GATEWAY,
}


def _correlation_id(request: Request) -> str | None:
    return getattr(request.state, "correlation_id", None)


def _body(code: str, message: str, request: Request, fields: list[dict[str, str]] | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": _correlation_id(request),
            "fields": fields or [],
        }
    }


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"][1:]) or "body",
                # error["msg"] is pydantic's rule description ("String should
                # have at least 1 character"), not the submitted value.
                "problem": error["msg"],
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("invalid_request", "That request could not be accepted.", request, fields),
        )

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=_body("not_found", f"No such {exc.kind}.", request),
        )

    @app.exception_handler(ConflictError)
    async def _conflict(request: Request, exc: ConflictError) -> JSONResponse:
        """Something the owner has to resolve, not something that went wrong.

        A pile that still holds uploads, a source a claim cites, a build already
        running, a preview that no longer matches the material. Each carries a
        code the interface branches on and a sentence saying what to do.
        """
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_body(exc.kind, exc.message, request),
        )

    @app.exception_handler(Unauthenticated)
    async def _unauthenticated(request: Request, exc: Unauthenticated) -> JSONResponse:
        """Multi tenancy and no usable token: nothing was read (ADR 0010)."""
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=_body(
                "unauthenticated",
                "This request carried no valid Vademecum token. Connect through your "
                "assistant and try again.",
                request,
            ),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(InvalidSchematic)
    async def _schematic(request: Request, exc: InvalidSchematic) -> JSONResponse:
        """An SVG that will not be kept. The reason, never the SVG."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_body("invalid_schematic", exc.message, request),
        )

    @app.exception_handler(SubmissionRefused)
    async def _submission(request: Request, exc: SubmissionRefused) -> JSONResponse:
        """A host-turn result that cannot be accepted (ADR 0009).

        A structural mismatch is a 422 like any other invalid request; a turn
        that is unknown, spent or expired is a 409, because the caller has to
        do something else rather than fix the body. The message names the
        problem and echoes nothing that was submitted.
        """
        status_code = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if exc.code == "invalid_submission"
            else status.HTTP_409_CONFLICT
        )
        return JSONResponse(
            status_code=status_code,
            content=_body(exc.code, exc.message, request),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(BridgeError)
    async def _bridge(request: Request, exc: BridgeError) -> JSONResponse:
        """Translate an App Server failure into plain language.

        The category is the whole of what crosses this line. ``exc`` may have
        been raised while handling a device-code reply or an account payload,
        so neither its message nor its ``data`` is touched here -- the copy
        comes from a local table keyed by category.
        """
        return JSONResponse(
            status_code=BRIDGE_STATUS.get(exc.category, status.HTTP_503_SERVICE_UNAVAILABLE),
            content=_body(exc.category, DETAIL.get(exc.category, DETAIL["unavailable"]), request),
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_body("error", str(exc.detail), request),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_body(
                "internal_error",
                "Something went wrong on this machine. The correlation id matches the local log line.",
                request,
            ),
        )
