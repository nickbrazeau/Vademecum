"""What one Vademecum offers another (ADR 0015).

The *away* node serves these; the *home* node calls them. They exist only when
`sync_accept_token` is set, and every call must present that token in the
`X-Vademecum-Sync` header; otherwise the routes answer as if they were not
there. Single tenancy only: a node is one learner's workspace.

Rows go both ways through `changes` and `apply`; a file goes by name through
`file`, and only a name a row already carries can be asked for.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from ..storage import sync as sync_store
from ..storage.common import NotFoundError
from .deps import get_connection, get_settings_dep, get_source_dir

router = APIRouter(prefix="/sync", tags=["sync"])

SYNC_HEADER = "X-Vademecum-Sync"
STORED_NAME = re.compile(r"[0-9a-f]{64}\.[a-z0-9]{1,5}\Z")


class ChangesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=64)
    changes: list[dict[str, Any]] = Field(max_length=sync_store.MAX_BATCH)


def require_peer(
    request: Request,
    token: str | None = Header(default=None, alias=SYNC_HEADER),
) -> None:
    """The peer's token, or nothing at all: a wrong token and no token look the same."""
    settings = get_settings_dep(request)
    expected = settings.sync_accept_token
    if settings.tenancy != "single" or not expected or token is None:
        raise NotFoundError("route", request.url.path)
    if not hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")):
        raise NotFoundError("route", request.url.path)


@router.get("/status", dependencies=[Depends(require_peer)])
def sync_status(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    data = sync_store.state(connection)
    data["role"] = get_settings_dep(request).sync_role
    return data


@router.get("/changes", dependencies=[Depends(require_peer)])
def sync_changes(
    since: int = Query(ge=0),
    limit: int = Query(default=sync_store.MAX_BATCH, ge=1, le=sync_store.MAX_BATCH),
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    changes, through, done = sync_store.changes_since(connection, since, limit=limit)
    return {
        "node_id": sync_store.node_id(connection),
        "changes": [change.as_dict() for change in changes],
        "through": through,
        "done": done,
    }


@router.post("/apply", dependencies=[Depends(require_peer)])
def sync_apply(
    payload: ChangesIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    settings = get_settings_dep(request)
    changes = [sync_store.Change.from_dict(item) for item in payload.changes]
    # Files a row names arrive separately: the home node pushes rows only for
    # files it has already placed, and away pulls files it lacks by name on
    # its own next turn. Here, a row whose file is absent is deferred.
    result = sync_store.apply_changes(
        connection,
        changes,
        role=settings.sync_role,
        directories=sync_store.file_kinds(source_dir),
        fetch=None,
    )
    sync_store.record_sync(connection, peer_node_id=payload.node_id, note="applied from peer")
    return {"node_id": sync_store.node_id(connection), **result.as_dict()}


@router.put("/file/{kind}/{name}", dependencies=[Depends(require_peer)])
async def sync_put_file(kind: str, name: str, request: Request, source_dir: Path = Depends(get_source_dir)) -> dict:
    """A file the peer's rows will name. The name is its digest, so it checks itself."""
    from ..ingest.limits import MAX_UPLOAD_BYTES
    from ..storage.sync import store_file

    directories = sync_store.file_kinds(source_dir)
    if kind not in directories or not STORED_NAME.match(name):
        raise NotFoundError("file", name)
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_BYTES:
            return {"stored": False, "reason": "too_large"}
    if hashlib.sha256(bytes(body)).hexdigest() != name.split(".", 1)[0]:
        return {"stored": False, "reason": "digest_mismatch"}
    if not (directories[kind] / name).is_file():
        store_file(directories, kind, name, bytes(body))
    return {"stored": True}


@router.get("/file/{kind}/{name}", dependencies=[Depends(require_peer)])
def sync_file(kind: str, name: str, source_dir: Path = Depends(get_source_dir)) -> FileResponse:
    directories = sync_store.file_kinds(source_dir)
    if kind not in directories or not STORED_NAME.match(name):
        raise NotFoundError("file", name)
    path = (directories[kind] / name).resolve()
    if path.parent != directories[kind].resolve() or not path.is_file():
        raise NotFoundError("file", name)
    return FileResponse(path, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
