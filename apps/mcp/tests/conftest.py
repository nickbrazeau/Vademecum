"""Fixtures.

The MCP server runs against the *real* API application, in-process, over
httpx's ASGI transport: no socket, no child process, no model call. The API is
built the way its own end-to-end test builds it -- a scripted turn runner and
a fake PubMed -- so a build and a grade run all the way through the storage
rules, and what the tools return is what the API actually returns.

Everything the API tests forbid is forbidden here too: the autouse guard makes
any attempt to spawn a real ``codex app-server`` fail loudly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

# ``tests.`` because this directory is a package (see __init__.py). Importing
# it also puts the API's test fakes on sys.path.
from tests.mcp_support import (  # noqa: I001
    ABSTRACT,
    API_ORIGIN,
    ASSESSMENT,
    EVIDENCE,
    GRADE,
    MCP_ORIGIN,
    SYNTHESIS,
)

from fake_appserver import AccountScript, ScriptedTransport, factory  # noqa: E402
from fake_model import FakeArticle, FakeProvider, ScriptedTurns  # noqa: E402
from mcp import Client  # noqa: E402

from vademecum.app import create_app  # noqa: E402
from vademecum.appserver import transport as transport_module  # noqa: E402
from vademecum.config import Settings as ApiSettings  # noqa: E402
from vademecum_mcp.api_client import ApiClient  # noqa: E402
from vademecum_mcp.config import McpSettings  # noqa: E402
from vademecum_mcp.server import create_server  # noqa: E402


@pytest.fixture(autouse=True)
def no_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The owner's real settings file must never reach a test (ADR 0012)."""
    monkeypatch.setenv("VADEMECUM_SETTINGS_FILE", str(tmp_path / "no-settings.env"))


@pytest.fixture(autouse=True)
def no_real_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test spawns a real App Server. Not one, not ever."""

    async def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test tried to start a real codex app-server")

    monkeypatch.setattr(transport_module.asyncio, "create_subprocess_exec", refuse)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def turns() -> ScriptedTurns:
    script = ScriptedTurns(synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT])
    script.grading = [GRADE]
    return script


@pytest.fixture
def provider() -> FakeProvider:
    return FakeProvider(
        articles=[FakeArticle(pmid="30012345", title="Lactate targets", abstract=ABSTRACT)]
    )


@pytest.fixture
def api_settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765)


@pytest.fixture
def mcp_settings(tmp_path: Path) -> McpSettings:
    return McpSettings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=8765,
        mcp_port=8766,
        mcp_public_url=MCP_ORIGIN,
        mcp_access_token_ttl=3600,
        mcp_refresh_token_ttl=86400,
    )


@pytest.fixture
async def api_app(api_settings: ApiSettings, turns: ScriptedTurns, provider: FakeProvider):
    """The real API, with its lifespan run, and every seam scripted."""
    # A signed-out scripted App Server for the status route; turns never reach
    # it because the turn factory below is replaced with the script.
    transports = factory(
        ScriptedTransport(responder=AccountScript()),
        ScriptedTransport(responder=AccountScript()),
    )
    app = create_app(api_settings, transport_factory=transports, provider_factory=lambda: provider)
    async with app.router.lifespan_context(app):
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        yield app


@pytest.fixture
async def api(api_app) -> AsyncIterator[ApiClient]:
    client = ApiClient(API_ORIGIN, timeout=30, transport=httpx.ASGITransport(app=api_app))
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def mcp_client(api: ApiClient) -> AsyncIterator[Client]:
    """The MCP server in memory, spoken to through the SDK's own client."""
    async with Client(create_server(api)) as client:
        yield client
