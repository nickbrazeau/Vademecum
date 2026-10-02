"""Test fixtures.

Every test gets an empty database in a ``tmp_path``, which satisfies ADR 0003's
outside-the-source-tree rule naturally. No test makes a network request or a
model call of any kind.

Two guards enforce the second half of that, now that a Codex App Server bridge
exists:

* ``no_real_codex`` is autouse and replaces the one call in the codebase that
  can create a child process. Nothing in the suite can spawn ``codex
  app-server``, deliberately or by accident, including through a code path
  nobody thought to check.
* the default ``client`` builds the application with a transport factory that
  refuses. Any existing test that starts touching the bridge fails loudly
  rather than quietly reaching for a real one; tests that mean to use it ask
  for ``model_client`` and get the scripted fake.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vademecum.app import create_app
from vademecum.appserver import transport as transport_module
from vademecum.config import DATABASE_FILENAME, Settings
from vademecum.db import apply_migrations, connect

from fake_appserver import AccountScript, ScriptedTransport, factory

# The authority the app is configured to serve. Requests in the suite come from
# here, so mutating routes pass the same-origin guard exactly as the browser's
# would; test_origin.py drives the refusals deliberately.
LOCAL_ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture(autouse=True)
def no_settings_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The owner's real settings file must never reach a test (ADR 0012)."""
    monkeypatch.setenv("VADEMECUM_SETTINGS_FILE", str(tmp_path / "no-settings.env"))


@pytest.fixture(autouse=True)
def no_real_codex(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test spawns a real App Server. Not one, not ever."""

    async def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError(
            "a test tried to start a real codex app-server; use the scripted transport"
        )

    monkeypatch.setattr(transport_module.asyncio, "create_subprocess_exec", refuse)


def refusing_factory() -> Callable[[], transport_module.Transport]:
    def make() -> transport_module.Transport:
        raise AssertionError("this test's app has no App Server bridge wired up")

    return make


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765)


@pytest.fixture()
def data_dir(settings: Settings) -> Path:
    return settings.prepare()


@pytest.fixture()
def database_path(data_dir: Path) -> Path:
    return data_dir / DATABASE_FILENAME


@pytest.fixture()
def connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(database_path)
    apply_migrations(conn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def client(settings: Settings) -> Iterator[TestClient]:
    # base_url is a loopback authority the same-origin guard accepts, so the
    # guard is genuinely exercised rather than bypassed: TestClient's default
    # `http://testserver` is a foreign Host and is refused, which is the point.
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as test_client:
        yield test_client


@pytest.fixture()
def scripted_transport() -> ScriptedTransport:
    """A single scripted App Server, signed out by default."""
    return ScriptedTransport(responder=AccountScript())


@pytest.fixture()
def model_client(
    settings: Settings, scripted_transport: ScriptedTransport
) -> Iterator[TestClient]:
    """An application whose bridge speaks to the scripted transport.

    Two transports are queued: the first is what the lazy start finds, the
    second is what an explicit restart gets. Both are scripted; neither is a
    process.
    """
    second = ScriptedTransport(responder=scripted_transport.responder)
    app = create_app(settings, transport_factory=factory(scripted_transport, second))
    app.state.second_transport = second
    with TestClient(app, base_url=LOCAL_ORIGIN) as test_client:
        yield test_client


def run(coroutine) -> object:
    """Run one coroutine to completion.

    ``asyncio.run`` rather than a pytest-asyncio dependency: the bridge tests
    are a handful of self-contained coroutines, and AGENTS.md asks for a reason
    before a dependency is added. There is not one here.
    """
    return asyncio.run(coroutine)
