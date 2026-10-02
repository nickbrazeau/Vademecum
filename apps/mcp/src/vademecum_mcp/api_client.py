"""The only thing in this package that talks to anything.

One HTTP client, to the local API on ``127.0.0.1``, and nothing else. There is
no provider endpoint here and no key: model turns and literature lookups happen
inside the API process, behind the same disclosures the web app shows. The
constructor refuses a base URL that is not loopback, so a misconfiguration
cannot turn this adapter into a client of something remote.

Errors cross this boundary as a code and a plain-language message taken from
the API's own error body, which never echoes submitted text (ADR 0002). Nothing
here builds a message out of a request or a response payload.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp.server.auth.middleware.auth_context import get_access_token

# The header the API reads a learner's token from (vademecum.tenancy). Named
# here rather than imported so this package's one dependency on the API stays
# the configuration module.
TOKEN_HEADER = "X-Vademecum-Token"

# httpx logs every request line -- method, full URL, status -- at INFO. A URL
# here carries record ids and query filters, which is the shape of a request,
# not its content, but the API's own logger already records that shape without
# the query string, and one log line per request is enough (ADR 0002, rule 5).
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)

from vademecum.config import ConfigError, is_loopback

from . import __version__

UNREACHABLE = (
    "Vademecum is not running on the Mac, or the MCP server cannot reach it. "
    "Start it there with ./scripts/dev.sh and try again."
)
TIMED_OUT = (
    "Vademecum on the Mac did not answer in time. If a build or a grading turn "
    "was in progress it may still complete; check its status before retrying."
)
UNEXPECTED = "Vademecum on the Mac answered in a way this tool could not use."
TOO_LARGE = "That file is larger than a tool result can carry. Open it in the web app instead."
# A picture stored by intake is at most 6 MB; a schematic at most 512 KB.
MAX_FILE_BYTES = 8 * 1024 * 1024


class ApiError(Exception):
    """A refusal or failure from the API, in the API's own words."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class Upstream:
    """A streamed API reply: status, lowercase headers, body, and a closer."""

    def __init__(self, *, status: int, headers: dict[str, str], body: Any, aclose: Any) -> None:
        self.status = status
        self.headers = headers
        self.body = body
        self.aclose = aclose


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        host = urlsplit(base_url).hostname or ""
        if not is_loopback(host):
            raise ConfigError(
                f"The MCP server only speaks to Vademecum on loopback; {base_url!r} is refused."
            )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout, connect=5.0),
            transport=transport,
            headers={"User-Agent": f"vademecum-mcp/{__version__}"},
        )
        # Set by the stdio server (ADR 0012): what to do when the API is not
        # there, which is to start it again. Called at most once per request,
        # then the request is retried once.
        self.recover: Callable[[], Awaitable[None]] | None = None

    @property
    def base_url(self) -> str:
        """The loopback origin this client speaks to."""
        return str(self._client.base_url).rstrip("/")

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward(
        self,
        method: str,
        path: str,
        *,
        params: Any,
        headers: dict[str, str],
        content: Any,
    ) -> "Upstream":
        """One request streamed through to the API, for the desk gateway.

        The gateway attaches the learner's token in ``headers`` itself, since a
        desk request has no tool context. The reply is streamed back as it
        arrives; the caller closes it when the body has been sent.
        """
        upstream_request = self._client.build_request(
            method, path, params=params, headers=headers, content=content
        )
        try:
            response = await self._client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            raise ApiError("unreachable", UNREACHABLE, 0) from exc
        return Upstream(
            status=response.status_code,
            headers={name.lower(): value for name, value in response.headers.items()},
            body=response.aiter_raw(),
            aclose=response.aclose,
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=_clean(params))

    async def get_bytes(self, path: str) -> tuple[str, bytes]:
        """A file the API serves (a picture, a schematic): its media type and bytes.

        Bounded so a tool result stays something a host will carry.
        """
        response = await self._request("GET", path, raw=True)
        if len(response.content) > MAX_FILE_BYTES:
            raise ApiError("too_large", TOO_LARGE, response.status_code)
        media_type = response.headers.get("content-type", "application/octet-stream")
        return media_type.split(";")[0].strip(), response.content

    async def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        *,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request(
            "POST", path, json=json, data=data, files=files, params=_clean(params)
        )

    async def patch(self, path: str, json: dict[str, Any]) -> Any:
        return await self._request("PATCH", path, json=json)

    async def put(self, path: str, json: dict[str, Any]) -> Any:
        return await self._request("PUT", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def _request(self, method: str, path: str, *, raw: bool = False, **kwargs: Any) -> Any:
        # Multi tenancy (ADR 0010): the API opens the workspace of whoever the
        # assistant's token belongs to. The token is the one this server
        # issued and the SDK verified for the current tool call; the API reads
        # the same access store to resolve it. Absent (stdio, single tenancy)
        # nothing is sent and the API treats the request as the owner's.
        current = get_access_token()
        if current is not None:
            headers = dict(kwargs.pop("headers", None) or {})
            headers[TOKEN_HEADER] = current.token
            kwargs["headers"] = headers
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiError("timeout", TIMED_OUT, 0) from exc
        except httpx.HTTPError as exc:
            if self.recover is None:
                raise ApiError("unreachable", UNREACHABLE, 0) from exc
            try:
                await self.recover()
                response = await self._client.request(method, path, **kwargs)
            except httpx.TimeoutException as again:
                raise ApiError("timeout", TIMED_OUT, 0) from again
            except Exception as again:  # noqa: BLE001 - recovery failed; say so plainly
                raise ApiError("unreachable", UNREACHABLE, 0) from again
        if raw and response.is_success:
            return response
        return _decode(response)


def _clean(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if not params:
        return None
    cleaned = {key: value for key, value in params.items() if value is not None}
    return cleaned or None


def _decode(response: httpx.Response) -> Any:
    if response.status_code == 204:
        return None
    try:
        body = response.json()
    except ValueError:
        body = None
    if response.is_success:
        return body
    code, message = "error", UNEXPECTED
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
        elif isinstance(body.get("detail"), str):
            message = body["detail"]
    raise ApiError(code, message, response.status_code)
