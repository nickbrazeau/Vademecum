"""Request logging (ADR 0002, rule 5).

Diagnostics record event names, timings, status codes and a generated
correlation id. Free text -- flag text, item bodies, note content -- is never
logged at any level.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-Id"

logger = logging.getLogger("vademecum.http")


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


async def log_requests(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Log the shape of a request, never its content.

    Note what is absent: the query string, the request body, and the response
    body. ``request.url.path`` carries only opaque record ids.
    """
    correlation_id = new_correlation_id()
    request.state.correlation_id = correlation_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000
        # The exception *type*, not its message and not a traceback: an
        # exception raised while handling a flag can carry the flag's text.
        logger.error(
            "http_request_failed cid=%s method=%s path=%s duration_ms=%.1f error=%s",
            correlation_id,
            request.method,
            request.url.path,
            duration_ms,
            type(exc).__name__,
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "http_request cid=%s method=%s path=%s status=%d duration_ms=%.1f",
        correlation_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers[CORRELATION_HEADER] = correlation_id
    return response
