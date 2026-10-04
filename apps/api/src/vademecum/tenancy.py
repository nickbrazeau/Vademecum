"""Who a request is for, and where that learner's workspace lives (ADR 0010).

Two tenancy modes, chosen at startup and never mixed:

* ``single`` -- the owner's Mac. One workspace at the data-directory root, no
  identity, no token: every request is the owner's, as it has always been.
* ``multi`` -- the hosted product. Every request carries an opaque token in
  ``X-Vademecum-Token``; a resolver turns it into a learner id or into a 401.
  Each learner has a directory of their own under ``learners/`` holding their
  own SQLite database, originals, exports and backups.

Isolation is by construction, not by predicate. A connection opened for a
request can only ever reach one learner's file, so there is no query in the
storage layer that could forget a ``WHERE learner_id = ?``. The storage layer
does not know tenancy exists.

The token is not an OAuth credential to this API: it is the one the MCP server
issued to the learner's assistant, and the API recognises it by reading the
same access store the MCP server writes. That is an internal contract
between two processes on one machine, pinned by a test in the MCP suite.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .config import DATA_SUBDIRECTORIES, DATABASE_FILENAME, MODEL_WORKSPACE_DIRNAME, SOURCE_FILES_DIRNAME, Settings
from .db import apply_migrations, connect
from .storage import encyclopedia as encyclopedia_store
from .storage import jobs
from .storage import literature as literature_store

logger = logging.getLogger("vademecum.tenancy")

OWNER_ID = "owner"
TOKEN_HEADER = "X-Vademecum-Token"
LEARNERS_DIRNAME = "learners"

# A learner id reaches the filesystem as a directory name, so it is a closed
# character set and a bounded length, checked before any path is built.
LEARNER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class Unauthenticated(Exception):
    """No usable token on a request in multi mode."""


class TokenResolver(Protocol):
    def resolve(self, token: str) -> str | None:
        """The learner id a live access token belongs to, or None."""

    def revoke_all_for(self, learner_id: str) -> int:
        """Revoke every token of one learner; returns how many."""


class StaticResolver:
    """Tokens known in advance. What the tests use."""

    def __init__(self, tokens: dict[str, str]) -> None:
        self._tokens = dict(tokens)
        self.revoked: list[str] = []

    def resolve(self, token: str) -> str | None:
        return self._tokens.get(token)

    def revoke_all_for(self, learner_id: str) -> int:
        gone = [token for token, learner in self._tokens.items() if learner == learner_id]
        for token in gone:
            del self._tokens[token]
        self.revoked.append(learner_id)
        return len(gone)


class SharedStoreResolver:
    """Reads the MCP server's access store: ``<data dir>/mcp/access.sqlite3``.

    Only the ``tokens`` table, only by SHA-256 digest, only live access
    tokens with a learner attached. The schema is the MCP server's; the MCP
    suite has a test that issues a real token and resolves it through here.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self._path))
        connection.row_factory = sqlite3.Row
        return connection

    def resolve(self, token: str) -> str | None:
        if not token or not self._path.exists():
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT learner_id FROM tokens WHERE token_hash = ? AND kind = 'access'"
                " AND revoked = 0 AND expires_at > ? AND learner_id IS NOT NULL",
                (digest, int(time.time())),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        learner = None if row is None else row["learner_id"]
        return learner if learner and LEARNER_ID.match(learner) else None

    def revoke_all_for(self, learner_id: str) -> int:
        if not self._path.exists():
            return 0
        connection = self._open()
        try:
            cursor = connection.execute(
                "UPDATE tokens SET revoked = 1 WHERE learner_id = ? AND revoked = 0", (learner_id,)
            )
            connection.commit()
            return cursor.rowcount
        except sqlite3.Error:
            return 0
        finally:
            connection.close()


# --- workspaces ---------------------------------------------------------------


@dataclass
class Workspace:
    """Everything a request needs that belongs to one learner."""

    learner_id: str
    data_dir: Path
    database_path: Path
    source_dir: Path
    model_workspace: Path
    build_service: Any
    host_turns: Any | None
    watcher: Any | None
    # The pipeline's turn factory: the host registry, or the owner's Codex.
    turn_factory: Any = None
    closed: bool = field(default=False)

    async def aclose(self) -> None:
        if self.closed:
            return
        self.closed = True
        await self.build_service.aclose()
        if self.watcher is not None:
            await self.watcher.aclose()


def prepare_workspace_database(database_path: Path, settings: Settings) -> None:
    """Bring one workspace's schema up to date and close out interrupted work."""
    connection = connect(database_path)
    try:
        applied = apply_migrations(connection)
        interrupted = jobs.sweep_interrupted(connection)
        tidied = encyclopedia_store.drop_stored_disclaimers(connection)
        literature_store.seed_settings(connection, interval_hours=settings.literature_interval_hours)
    finally:
        connection.close()
    if applied:
        logger.info("migrations_applied count=%d", len(applied))
    if interrupted:
        logger.info("interrupted_runs_closed count=%d", interrupted)
    if tidied:
        logger.info("stored_disclaimers_dropped count=%d", tidied)


class Workspaces:
    """Opens workspaces lazily, one per learner, and closes them together.

    ``open_services`` is the app factory's recipe for the per-workspace
    services (build service, host turns, watcher), so this module stays free
    of model and literature wiring.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        open_services: Callable[["Workspace"], Any],
    ) -> None:
        self._settings = settings
        self._root = settings.resolve_data_dir()
        self._open_services = open_services
        self._open: dict[str, Workspace] = {}
        self._lock = asyncio.Lock()

    @property
    def tenancy(self) -> str:
        return self._settings.tenancy

    def layout(self, learner_id: str) -> Path:
        """Where a learner's workspace lives. Validates the id first."""
        if not LEARNER_ID.match(learner_id):
            raise ValueError("learner id has characters that may not reach the filesystem")
        if self._settings.tenancy == "single":
            return self._root
        return self._root / LEARNERS_DIRNAME / learner_id

    def known(self) -> list[str]:
        return sorted(self._open)

    async def get(self, learner_id: str) -> Workspace:
        existing = self._open.get(learner_id)
        if existing is not None and not existing.closed:
            return existing
        async with self._lock:
            existing = self._open.get(learner_id)
            if existing is not None and not existing.closed:
                return existing
            workspace = await self._create(learner_id)
            self._open[learner_id] = workspace
            return workspace

    async def _create(self, learner_id: str) -> Workspace:
        data_dir = self.layout(learner_id)
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name in DATA_SUBDIRECTORIES:
            (data_dir / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        database_path = data_dir / DATABASE_FILENAME
        prepare_workspace_database(database_path, self._settings)
        workspace = Workspace(
            learner_id=learner_id,
            data_dir=data_dir,
            database_path=database_path,
            source_dir=data_dir / SOURCE_FILES_DIRNAME,
            model_workspace=data_dir / MODEL_WORKSPACE_DIRNAME,
            build_service=None,
            host_turns=None,
            watcher=None,
        )
        result = self._open_services(workspace)
        if asyncio.iscoroutine(result):
            await result
        logger.info("workspace_opened tenancy=%s", self._settings.tenancy)
        return workspace

    async def delete(self, learner_id: str) -> int:
        """Close and remove one learner's workspace. Returns files removed."""
        if self._settings.tenancy != "multi":
            raise ValueError("a single-owner workspace is not deleted through the API")
        directory = self.layout(learner_id)
        async with self._lock:
            workspace = self._open.pop(learner_id, None)
            if workspace is not None:
                await workspace.aclose()
            if not directory.exists():
                return 0
            count = sum(1 for path in directory.rglob("*") if path.is_file())
            shutil.rmtree(directory)
        logger.info("workspace_deleted files=%d", count)
        return count

    async def aclose(self) -> None:
        async with self._lock:
            for workspace in list(self._open.values()):
                await workspace.aclose()
            self._open.clear()
