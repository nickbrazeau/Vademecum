"""The only network client in Vademecum's own code.

``http.client`` from the standard library, on purpose. A repo test forbids
``requests``, ``httpx``, ``urllib.request`` and ``aiohttp`` throughout the
backend so that egress cannot appear quietly in a module nobody was reading;
this file is the one deliberate exception, and it is meant to stay small enough
that a reviewer can hold all of it at once.

What it can reach is a frozen allowlist of three hosts: PubMed's E-utilities,
and the two podcast sites the Case Series hub reads (ADR 0022). There is no
setting that adds to it, no scheme parameter, no proxy, no redirect following
and no verb other than GET. What it sends is a path, urlencoded query
parameters supplied by the caller, and a User-Agent. The callers are
:mod:`.pubmed`, whose queries are validated public topic strings, and
:mod:`.cases`, whose requests are fixed; no passage, note, learning point or
answer has a route into this module.

Failures are reported as one of :data:`CATEGORIES` and nothing else. Provider
prose is never raised, logged or stored -- a category is safe to put in a UI, an
upstream error body is not.
"""

from __future__ import annotations

import http.client
import ssl
import threading
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from .. import __version__

# The whole of the allowlist. Adding to it is a source change and a code review.
# The two sites are the Case Series hub's (ADR 0022): their public WordPress
# JSON endpoint, one fixed request each, nothing of the owner's in it.
ALLOWED_HOSTS = frozenset({"eutils.ncbi.nlm.nih.gov", "clinicalproblemsolving.com", "thecurbsiders.com"})

HTTPS_PORT = 443

# A PubMed efetch page of 100 abstracts is a few hundred KiB. Eight MiB is far
# above anything legitimate and far below anything that would hurt to discard.
MAX_BODY_BYTES = 8 * 1024 * 1024

# NCBI asks unregistered callers for no more than three requests a second,
# and callers with its courtesy key for no more than ten.
MIN_REQUEST_INTERVAL = 1.0 / 3.0
MIN_REQUEST_INTERVAL_WITH_KEY = 1.0 / 10.0

# One attempt plus at most two retries.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Transport failure categories. Closed set; each is safe to show and to store.
CATEGORIES = frozenset(
    {
        "timeout",
        "connection",
        "http_error",
        "redirect_refused",
        "too_large",
        "blocked_host",
    }
)

USER_AGENT_BASE = f"Vademecum/{__version__}"


class ProviderError(RuntimeError):
    """A provider call failed. The category is the entire message.

    ``status`` exists only so the retry rule can tell 429 from 404. It is never
    part of the message and never reaches a log line or a stored row.
    """

    def __init__(self, category: str, *, status: int | None = None) -> None:
        super().__init__(category)
        self.category = category
        self.status = status


@runtime_checkable
class Fetcher(Protocol):
    """One GET against an allowlisted host. Tests substitute a fake for this."""

    def get(self, path: str, params: dict[str, str]) -> bytes: ...


class _Response(Protocol):
    status: int

    def read(self, amount: int) -> bytes: ...

    def getheader(self, name: str, default: str | None = ...) -> str | None: ...


class _Connection(Protocol):
    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> _Response: ...

    def close(self) -> None: ...


def _open_connection(host: str, timeout: float) -> _Connection:
    # create_default_context(): certificate verification and hostname checking
    # on, which is the default and is spelled out here so a future edit that
    # turns either off has to say so.
    from ..tls import client_context

    context = client_context()
    return http.client.HTTPSConnection(host, HTTPS_PORT, timeout=timeout, context=context)


class HttpsFetcher:
    """GET from one allowlisted host over TLS, throttled and bounded.

    The rate limiter is per instance: the watcher holds one fetcher and runs
    topics one at a time, so one limiter covers everything this process sends.
    """

    def __init__(
        self,
        host: str,
        *,
        timeout: float,
        contact_email: str = "",
        ncbi_key: str = "",
        min_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        connection_factory: Callable[[str, float], _Connection] = _open_connection,
    ) -> None:
        # Before anything else, and long before a socket: an unknown host is
        # refused at construction so a misconfigured caller cannot even hold a
        # client pointed somewhere else.
        if host not in ALLOWED_HOSTS:
            raise ProviderError("blocked_host")
        self._host = host
        self._timeout = timeout
        self._contact_email = contact_email.strip()
        # NCBI's own rate-limit identifier for E-utilities, not a model
        # credential: a public database's courtesy key, which raises the
        # allowance from three requests a second to ten. Optional; empty by
        # default, and then nothing identifying the operator leaves the machine.
        self._ncbi_key = ncbi_key.strip()
        if min_interval is None:
            min_interval = MIN_REQUEST_INTERVAL_WITH_KEY if self._ncbi_key else MIN_REQUEST_INTERVAL
        self._min_interval = max(min_interval, 0.0)
        self._clock = clock
        self._sleep = sleep
        self._connect = connection_factory
        self._gate = threading.Lock()
        self._next_allowed_at: float | None = None

    # -- public ---------------------------------------------------------------

    def get(self, path: str, params: dict[str, str]) -> bytes:
        # Re-checked per call. The allowlist is the boundary, not a constructor
        # argument, and it costs nothing to assert it where the socket opens.
        if self._host not in ALLOWED_HOSTS:
            raise ProviderError("blocked_host")
        sent = {str(key): str(value) for key, value in params.items()}
        if self._ncbi_key:
            sent["api_key"] = self._ncbi_key
        query = urlencode(sent)
        target = f"{path}?{query}" if query else path

        failure: ProviderError | None = None
        for attempt in range(MAX_ATTEMPTS):
            self._throttle()
            try:
                return self._attempt(target)
            except ProviderError as exc:
                failure = exc
                if attempt == MAX_ATTEMPTS - 1 or not _retryable(exc):
                    raise
                self._sleep(RETRY_BACKOFF_SECONDS * (2**attempt))
        assert failure is not None  # unreachable: the loop returns or raises
        raise failure

    # -- internals ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        agent = USER_AGENT_BASE
        # Only if the owner opted in. The config default is empty, so by
        # default nothing identifying the owner leaves the machine.
        if self._contact_email:
            agent = f"{USER_AGENT_BASE} (mailto:{self._contact_email})"
        return {"User-Agent": agent, "Accept-Encoding": "identity"}

    def _attempt(self, target: str) -> bytes:
        connection = self._connect(self._host, self._timeout)
        try:
            connection.request("GET", target, headers=self._headers())
            response = connection.getresponse()
            status = int(response.status)
            # A redirect is a request to talk to somewhere the allowlist has
            # not seen. Refused, always, rather than followed and re-checked.
            if 300 <= status < 400:
                raise ProviderError("redirect_refused")
            if status != 200:
                raise ProviderError("http_error", status=status)
            declared = response.getheader("Content-Length", None)
            if declared is not None and declared.strip().isdigit():
                if int(declared) > MAX_BODY_BYTES:
                    raise ProviderError("too_large")
            # One byte past the cap: enough to know it is too long, never
            # enough to hold an unbounded body in memory.
            body = response.read(MAX_BODY_BYTES + 1)
            if len(body) > MAX_BODY_BYTES:
                raise ProviderError("too_large")
            return body
        except ProviderError:
            raise
        # socket.timeout is TimeoutError since 3.10; caught before OSError
        # because it is one, and a slow provider is not a broken one.
        except TimeoutError as exc:
            raise ProviderError("timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ProviderError("connection") from exc
        finally:
            try:
                connection.close()
            except Exception:  # pragma: no cover - close is best effort
                pass

    def _throttle(self) -> None:
        """Monotonic-clock spacing, shared by every call on this instance."""
        with self._gate:
            now = self._clock()
            next_allowed = self._next_allowed_at
            if next_allowed is not None and now < next_allowed:
                self._sleep(next_allowed - now)
                # A clock that did not move (a test's, or a coarse one) must
                # not collapse the spacing; take the later of the two.
                now = max(self._clock(), next_allowed)
            self._next_allowed_at = now + self._min_interval


def _retryable(error: ProviderError) -> bool:
    """Transient only. A 404 or a 400 is an answer, and asking again is rude."""
    if error.category in ("connection", "timeout"):
        return True
    return error.category == "http_error" and error.status in RETRY_STATUSES
