"""The home node's side of a sync (ADR 0015): pull, apply, push.

This is the one place, besides the literature watch, where this process opens
a connection to another machine -- and that machine is the learner's own
second Vademecum, at the address they configured, presenting the token they
set. What travels is the workspace itself: rows of every synced table in both
directions, and files by name. Nothing is sent anywhere else, and nothing is
sent at all unless `sync_peer_url` is set.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..storage import sync as sync_store
from ..storage.sync import Change, file_kinds

logger = logging.getLogger("vademecum.sync")

SYNC_HEADER = "X-Vademecum-Sync"


@runtime_checkable
class Transport(Protocol):
    """One HTTP exchange with the peer. Tests substitute an in-process one."""

    def request(self, method: str, path: str, *, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]: ...


class SyncError(RuntimeError):
    pass


class Peer:
    def __init__(self, transport: Transport, token: str) -> None:
        self._transport = transport
        self._headers = {SYNC_HEADER: token, "Accept": "application/json"}

    def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = dict(self._headers)
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
        status, raw = self._transport.request(method, path, headers=headers, body=payload)
        if status != 200:
            raise SyncError(f"the peer answered {status} to {method} {path.split('?')[0]}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise SyncError("the peer's reply was not JSON") from exc
        if not isinstance(data, dict):
            raise SyncError("the peer's reply had the wrong shape")
        return data

    def status(self) -> dict[str, Any]:
        return self._json("GET", "/api/sync/status")

    def changes(self, since: int) -> dict[str, Any]:
        return self._json("GET", f"/api/sync/changes?since={int(since)}")

    def apply(self, node_id: str, changes: list[Change]) -> dict[str, Any]:
        return self._json("POST", "/api/sync/apply", {"node_id": node_id, "changes": [c.as_dict() for c in changes]})

    def file(self, kind: str, name: str) -> bytes | None:
        status, raw = self._transport.request("GET", f"/api/sync/file/{kind}/{name}", headers=dict(self._headers), body=None)
        return raw if status == 200 else None

    def put_file(self, kind: str, name: str, data: bytes) -> bool:
        headers = dict(self._headers)
        headers["Content-Type"] = "application/octet-stream"
        status, raw = self._transport.request("PUT", f"/api/sync/file/{kind}/{name}", headers=headers, body=data)
        if status != 200:
            return False
        try:
            return bool(json.loads(raw.decode("utf-8")).get("stored"))
        except (ValueError, AttributeError):
            return False


def sync_once(
    connection: sqlite3.Connection,
    *,
    peer: Peer,
    source_dir: Path,
    role: sync_store.Role = "home",
    on_file: Callable[[str, str], None] | None = None,
) -> dict[str, Any]:
    """One round: pull the peer's changes and apply them, then push ours.

    Returns counts. Raises SyncError when the peer cannot be reached or
    answers badly; nothing partial is left behind, because each batch is one
    transaction and the cursor moves only with it.
    """
    me = sync_store.node_id(connection)
    status = peer.status()
    peer_id = str(status.get("node_id", ""))
    if not peer_id:
        raise SyncError("the peer did not say who it is")
    if peer_id == me:
        raise SyncError("the peer is this node")
    directories = file_kinds(source_dir)

    # --- pull ---------------------------------------------------------------
    pulled = applied = skipped = deferred = 0
    cursor = int(sync_store.state(connection)["pulled_through"])
    while True:
        page = peer.changes(cursor)
        changes = [Change.from_dict(item) for item in page.get("changes", [])]
        if not changes:
            break
        result = sync_store.apply_changes(
            connection, changes, role=role, directories=directories, fetch=peer.file
        )
        pulled += len(changes)
        applied += result.applied
        skipped += result.skipped
        deferred += result.deferred
        if result.through <= cursor:
            break
        cursor = result.through
        sync_store.record_sync(connection, peer_node_id=peer_id, pulled_through=cursor, note="pulling")
        if page.get("done", True) or result.deferred:
            break

    # --- push ---------------------------------------------------------------
    pushed = 0
    since = int(sync_store.state(connection)["pushed_through"])
    while True:
        changes, through, done = sync_store.changes_since(connection, since)
        if not changes:
            break
        # Files first, so the peer never sees a row whose file it lacks.
        for change in changes:
            if change.op != "upsert" or change.table not in sync_store.FILE_COLUMNS or change.row is None:
                continue
            column, kind = sync_store.FILE_COLUMNS[change.table]
            name = change.row.get(column)
            path = directories[kind] / name if isinstance(name, str) and name else None
            if path is not None and path.is_file() and not peer.put_file(kind, name, path.read_bytes()):
                raise SyncError("the peer did not take a file")
        reply = peer.apply(me, changes)
        pushed += len(changes)
        accepted = int(reply.get("through", through))
        if accepted <= since:
            break
        since = accepted
        sync_store.record_sync(connection, peer_node_id=peer_id, pushed_through=since, note="pushing")
        if done or int(reply.get("deferred", 0)):
            break

    note = f"pulled {pulled} (applied {applied}, deferred {deferred}); pushed {pushed}"
    sync_store.record_sync(connection, peer_node_id=peer_id, note=note)
    logger.info("sync_round pulled=%d applied=%d skipped=%d deferred=%d pushed=%d", pulled, applied, skipped, deferred, pushed)
    return {"pulled": pulled, "applied": applied, "skipped": skipped, "deferred": deferred, "pushed": pushed}


class HttpTransport:
    """The real thing: `http.client`, TLS for https, plain http for loopback only."""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        from urllib.parse import urlsplit

        from ..config import is_loopback

        parts = urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise SyncError("the peer address must be an https:// (or loopback http://) origin")
        if parts.scheme == "http" and not is_loopback(parts.hostname):
            raise SyncError("a peer beyond this machine must be reached over https")
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")
        self._timeout = timeout

    def request(self, method: str, path: str, *, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        import http.client
        import ssl

        if self._scheme == "https":
            connection = http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=ssl.create_default_context()
            )
        else:
            connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            connection.request(method, f"{self._prefix}{path}", body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise SyncError("the peer could not be reached") from exc
        finally:
            connection.close()
