"""What one Vademecum offers another (ADR 0015).

Foris serves these; domi calls them. They exist only when
`sync_accept_token` is set, and every call must present that token in the
`X-Vademecum-Sync` header; otherwise the routes answer as if they were not
there. Single tenancy only: a node is one learner's workspace.

Rows go both ways through `changes` and `apply`; a file goes by name through
`file`, and only a name a row already carries can be asked for.
"""

from __future__ import annotations

import asyncio
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
    # False in the lean scope: rows arrive without the files they name.
    files: bool = True


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
    data["role"] = get_settings_dep(request).sync_role_name
    # What this node holds of each peer's log, by its own records (ADR 0015, 0026).
    # The asking node names itself in the query: the cloud gateway passes the sync
    # token through but not other headers.
    peer = request.query_params.get("node", "") or request.headers.get("x-vademecum-node", "")
    data["received_through"] = sync_store.received_through(connection, peer) if peer else None
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
    # Files a row names arrive separately: domi pushes rows only for files
    # it has already placed, and fetches files foris's rows name on
    # its own next turn. Here, a row whose file is absent is deferred.
    result = sync_store.apply_changes(
        connection,
        changes,
        role=settings.sync_role_name,
        directories=sync_store.file_kinds(source_dir),
        fetch=None,
        require_files=payload.files,
    )
    sync_store.record_sync(connection, peer_node_id=payload.node_id, note="applied from peer")
    if result.deferred == 0:
        sync_store.record_received(connection, payload.node_id, result.through)
    # An episode listened to on the Mac gives its audio up here too (ADR 0027).
    from ..storage import podcasts

    podcasts.retire_audio(connection, podcasts.podcasts_dir(source_dir), role=settings.sync_role_name)
    # A page rewritten or deleted on the Mac: its old figures go too.
    from ..storage import figure_copies

    if settings.sync_role_name == "foris":
        figure_copies.prune(connection, figure_copies.figures_dir(source_dir))
    return {"node_id": sync_store.node_id(connection), **result.as_dict()}


@router.get("/podcast-audio", dependencies=[Depends(require_peer)])
def podcast_audio_held(connection: sqlite3.Connection = Depends(get_connection), source_dir: Path = Depends(get_source_dir)) -> dict:
    """Which episodes' audio this copy holds (ADR 0027), so the Mac sends only what is missing."""
    from ..storage import podcasts

    directory = podcasts.podcasts_dir(source_dir)
    held = [
        {"episode_id": e.id, "audio_name": e.audio_name, "bytes": (directory / e.audio_name).stat().st_size}
        for e in podcasts.unheard_with_audio(connection)
        if (directory / e.audio_name).is_file()
    ]
    return {"held": held, "keep": podcasts.CLOUD_KEEP}


@router.put("/podcast-audio/{episode_id}", dependencies=[Depends(require_peer)])
async def podcast_audio_put(
    episode_id: str,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    """An unheard episode's audio from the Mac, checked against its digest. Kept
    while it is among the newest unheard; retired when listened to."""
    from ..storage import podcasts

    episode = podcasts.get_episode(connection, episode_id)
    if not podcasts.AUDIO_NAME.match(episode.audio_name or "") or episode.listened_at is not None:
        return {"stored": False, "reason": "not wanted"}
    data = await request.body()
    if not data or len(data) > 80 * 1024 * 1024:
        return {"stored": False, "reason": "size"}
    if not await asyncio.to_thread(_digest_matches, data, request.headers.get("x-content-sha256", "")):
        return {"stored": False, "reason": "digest"}
    directory = podcasts.podcasts_dir(source_dir)
    await asyncio.to_thread(_write_whole, directory, episode.audio_name, data)
    podcasts.retire_audio(connection, directory, role=get_settings_dep(request).sync_role_name)
    return {"stored": (directory / episode.audio_name).is_file()}


class RelayReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[str, Any]


@router.get("/relay/wait", dependencies=[Depends(require_peer)])
async def relay_wait(request: Request, timeout: float = Query(default=20, ge=0, le=25)) -> dict:
    """The Mac's long poll for Socratic turns from the phone's tutor (feedback of 6 October)."""
    relay = getattr(request.app.state, "relay", None)
    if relay is None:
        return {"requests": []}
    return {"requests": await relay.wait(timeout)}


@router.post("/relay/{request_id}", dependencies=[Depends(require_peer)])
def relay_reply(request_id: str, payload: RelayReplyIn, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    """The Mac's answer to one turn: the next question, or the assessment."""
    from ..model import socratic as tutor

    relay = getattr(request.app.state, "relay", None)
    asked = relay.complete(request_id) if relay is not None else None
    if asked is None:
        return {"applied": False}
    if "error" in payload.payload:
        relay.errors[asked["session_id"]] = str(payload.payload["error"])[:40]
        return {"applied": False}
    try:
        result = tutor.apply_turn(connection, asked["session_id"], payload.payload)
    except Exception:  # noqa: BLE001 - a session deleted meanwhile
        return {"applied": False}
    return {"applied": True, "gaps_filed": result.get("gaps_filed", 0)}


@router.get("/figures", dependencies=[Depends(require_peer)])
def figures_held(source_dir: Path = Depends(get_source_dir)) -> dict:
    """Which page figures this copy holds (feedback of 6 October), so the Mac sends only the rest."""
    from ..storage import figure_copies

    return {"held": figure_copies.held(figure_copies.figures_dir(source_dir)), "per_round": figure_copies.PER_ROUND}


@router.put("/figures/{image_id}", dependencies=[Depends(require_peer)])
async def figure_put(
    image_id: str,
    request: Request,
    ext: str = Query(pattern="^(png|jpg|svg)$"),
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    """A figure one of this copy's pages places, from the Mac, checked against its digest.
    A picture no current page places is refused."""
    from ..storage import figure_copies

    if image_id not in set(figure_copies.referenced(connection)):
        return {"stored": False, "reason": "not placed"}
    data = await request.body()
    if not data or len(data) > 12 * 1024 * 1024:
        return {"stored": False, "reason": "size"}
    if not await asyncio.to_thread(_digest_matches, data, request.headers.get("x-content-sha256", "")):
        return {"stored": False, "reason": "digest"}
    stored = await asyncio.to_thread(figure_copies.store, figure_copies.figures_dir(source_dir), image_id, ext, data)
    return {"stored": stored}


# Hashing and writing up to 80 MB happen off the event loop, so a large upload from the
# Mac never stalls the requests the phone is making at the same moment.
def _digest_matches(data: bytes, digest: str) -> bool:
    # Required, not optional: an upload with no checksum once replaced a whole episode's
    # audio with one byte (feedback of 10 October). The Mac always sends one.
    return bool(digest) and hmac.compare_digest(hashlib.sha256(data).hexdigest(), digest)


def _write_whole(directory: Path, name: str, data: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / f".{name}.partial"
    partial.write_bytes(data)
    partial.replace(directory / name)


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
    data = bytes(body)
    if await asyncio.to_thread(lambda: hashlib.sha256(data).hexdigest()) != name.split(".", 1)[0]:
        return {"stored": False, "reason": "digest_mismatch"}
    if not (directories[kind] / name).is_file():
        await asyncio.to_thread(store_file, directories, kind, name, data)
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
