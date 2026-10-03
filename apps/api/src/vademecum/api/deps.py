"""Request-scoped access to the database and to startup state.

A ``sqlite3.Connection`` is not thread-safe in any useful sense: it carries the
current transaction. FastAPI runs synchronous endpoints on a thread pool, so a
single shared connection would let one request's ``BEGIN`` sit inside another
request's work. Each request therefore opens its own connection and closes it
when the response is done, which is what SQLite is cheap enough to allow.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Depends, Request

from ..appserver import ModelBridge
from ..config import Settings
from ..db import connect
from ..storage.sources import ConflictError
from ..tenancy import OWNER_ID, TOKEN_HEADER, Unauthenticated, Workspace


def resolve_learner(request: Request) -> str:
    """Whose request this is (ADR 0010).

    Single tenancy: the owner, always. Multi tenancy: the learner the token
    resolves to, or a refusal. The token is read from one header and handed
    to the resolver as-is; it is never logged and never echoed.
    """
    if request.app.state.settings.tenancy == "single":
        return OWNER_ID
    token = (request.headers.get(TOKEN_HEADER) or "").strip()
    learner = request.app.state.token_resolver.resolve(token) if token else None
    if learner is None:
        raise Unauthenticated()
    return learner


async def get_workspace(request: Request) -> Workspace:
    """The learner's workspace, resolved once per request and cached on it."""
    cached = getattr(request.state, "workspace", None)
    if cached is not None:
        return cached
    workspace = await request.app.state.workspaces.get(resolve_learner(request))
    request.state.workspace = workspace
    return workspace


def _current(request: Request) -> Workspace:
    """The workspace an earlier dependency resolved. For plain-call helpers."""
    workspace = getattr(request.state, "workspace", None)
    if workspace is None:
        raise RuntimeError("workspace not resolved: declare get_connection or get_workspace first")
    return workspace


async def get_connection(
    workspace: Workspace = Depends(get_workspace),
) -> AsyncIterator[sqlite3.Connection]:
    """One connection per request, to this learner's database only."""
    connection = connect(workspace.database_path)
    try:
        yield connection
    finally:
        connection.close()


def get_database_path(workspace: Workspace = Depends(get_workspace)) -> Path:
    return workspace.database_path


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_data_dir(workspace: Workspace = Depends(get_workspace)) -> Path:
    return workspace.data_dir


def get_source_dir(workspace: Workspace = Depends(get_workspace)) -> Path:
    return workspace.source_dir


def get_build_service(workspace: Workspace = Depends(get_workspace)):
    return workspace.build_service


def get_watcher(request: Request):
    """This learner's literature watcher, or None when the watch is off."""
    return _current(request).watcher


def get_turn_factory(request: Request):
    """A factory for one schema-constrained model turn.

    A factory rather than a runner: each turn gets a fresh, ephemeral thread, so
    nothing carries over between a grading turn and anything else.
    """
    return request.app.state.turn_factory


def get_model_mode(request: Request) -> str:
    """``codex``, ``claude`` or ``host`` (ADR 0009, 0019): who does the model work."""
    return request.app.state.model_mode


def get_host_turns(request: Request):
    """This learner's pending-turn registry in host mode, or None in Codex mode."""
    return _current(request).host_turns


def get_model_bridge(request: Request) -> ModelBridge:
    """The one App Server bridge for this application.

    Created at startup, started at first use. A single instance on purpose:
    the whole point of the bridge is that there is one managed process, and a
    per-request bridge would be a per-request ``codex app-server``. It is the
    owner's, so in multi tenancy no learner may reach it.
    """
    if request.app.state.settings.tenancy != "single":
        raise ConflictError(
            "not_in_this_mode",
            "This Vademecum has no model connection of its own; grading and builds "
            "happen in your ChatGPT.",
        )
    return request.app.state.model_bridge
