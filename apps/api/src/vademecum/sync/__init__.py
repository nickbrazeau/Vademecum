"""Domi's side of a sync (ADR 0015): pull, apply, push.

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

    def status(self, node_id: str = "") -> dict[str, Any]:
        from urllib.parse import quote

        return self._json("GET", "/api/sync/status" + (f"?node={quote(node_id)}" if node_id else ""))

    def changes(self, since: int) -> dict[str, Any]:
        return self._json("GET", f"/api/sync/changes?since={int(since)}")

    def apply(self, node_id: str, changes: list[Change], *, files: bool = True) -> dict[str, Any]:
        return self._json(
            "POST", "/api/sync/apply", {"node_id": node_id, "changes": [c.as_dict() for c in changes], "files": files}
        )

    # --- the Socratic relay (feedback of 6 October) --------------------------

    def relay_wanted(self) -> float | None:
        """When the owner last had the tutor open on the phone (epoch seconds), read from
        the Worker in front of the phone's copy: it does not wake the container."""
        status, raw = self._transport.request("GET", "/__relay/wanted", headers=dict(self._headers), body=None)
        if status != 200:
            return None
        try:
            return float(json.loads(raw.decode("utf-8")).get("at"))
        except (ValueError, TypeError, AttributeError):
            return None

    def relay_wait(self, timeout: int = 20) -> list[dict[str, Any]]:
        data = self._json("GET", f"/api/sync/relay/wait?timeout={int(timeout)}")
        requests = data.get("requests", [])
        return [r for r in requests if isinstance(r, dict)] if isinstance(requests, list) else []

    def relay_reply(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json("POST", f"/api/sync/relay/{request_id}", {"payload": payload})

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


def push_podcast_audio(connection: sqlite3.Connection, peer: "Peer", source_dir: Path) -> int:
    """The newest unheard episodes' audio, to a peer that lacks it (ADR 0027).
    A peer that does not know the episode yet is skipped until the next round."""
    import hashlib

    from ..storage import podcasts

    try:
        offer = peer._json("GET", "/api/sync/podcast-audio")
    except SyncError:
        return 0  # an older peer, without the route
    # By size as well as id: an episode rendered again, in new voices, goes again.
    held = {
        (str(item.get("episode_id")), int(item.get("bytes") or 0)) for item in offer.get("held", []) if isinstance(item, dict)
    }
    keep = int(offer.get("keep") or podcasts.CLOUD_KEEP)
    directory = podcasts.podcasts_dir(source_dir)
    sent = 0
    for episode in podcasts.unheard_with_audio(connection)[:keep]:
        path = directory / episode.audio_name
        if not path.is_file() or (episode.id, path.stat().st_size) in held:
            continue
        data = path.read_bytes()
        headers = dict(peer._headers)
        headers["Content-Type"] = "application/octet-stream"
        headers["X-Content-SHA256"] = hashlib.sha256(data).hexdigest()
        status, raw = peer._transport.request("PUT", f"/api/sync/podcast-audio/{episode.id}", headers=headers, body=data)
        try:
            stored = status == 200 and bool(json.loads(raw.decode("utf-8")).get("stored"))
        except (ValueError, AttributeError):
            stored = False
        if stored:
            sent += 1
            logger.info("podcast_audio_sent bytes=%d", len(data))
    return sent


def push_figures(connection: sqlite3.Connection, peer: "Peer", source_dir: Path) -> int:
    """The pictures the cloud copy's pages place and it lacks (feedback of 6 October), a
    round's worth at a time. Read from the Mac's own picture store; nothing else is sent."""
    import hashlib

    from ..storage import figure_copies

    try:
        offer = peer._json("GET", "/api/sync/figures")
    except SyncError:
        return 0  # an older peer, without the route
    held = offer.get("held") if isinstance(offer.get("held"), dict) else {}
    budget = int(offer.get("per_round") or figure_copies.PER_ROUND)
    images_dir = source_dir.parent / "images"
    sent = 0
    for image_id in figure_copies.referenced(connection):
        if sent >= budget:
            break
        if image_id in held:
            continue
        row = connection.execute("SELECT stored_name, media_type FROM source_images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            continue
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/svg+xml": "svg"}.get(row["media_type"])
        path = images_dir / row["stored_name"]
        if extension is None or not path.is_file():
            continue
        data = path.read_bytes()
        headers = dict(peer._headers)
        headers["Content-Type"] = "application/octet-stream"
        headers["X-Content-SHA256"] = hashlib.sha256(data).hexdigest()
        status, raw = peer._transport.request("PUT", f"/api/sync/figures/{image_id}?ext={extension}", headers=headers, body=data)
        try:
            if status == 200 and json.loads(raw.decode("utf-8")).get("stored"):
                sent += 1
        except (ValueError, AttributeError):
            continue
    if sent:
        logger.info("figures_sent count=%d", sent)
    return sent


def sync_once(
    connection: sqlite3.Connection,
    *,
    peer: Peer,
    source_dir: Path,
    role: sync_store.Role = "domi",
    scope: sync_store.Scope = "full",
) -> dict[str, Any]:
    """One round: pull the peer's changes and apply them, then push ours.

    Returns counts. Raises SyncError when the peer cannot be reached or
    answers badly; nothing partial is left behind, because each batch is one
    transaction and the cursor moves only with it.
    """
    me = sync_store.node_id(connection)
    status = peer.status(me)
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
    # A peer restored from an older copy of itself says it holds less than we
    # think we sent; send again from there (ADR 0026). Re-applying is harmless.
    held = status.get("received_through")
    if isinstance(held, int) and 0 <= held < since:
        since = held
    elif held is None and status.get("role") == "foris" and "received_through" in status:
        since = 0
    while True:
        changes, through, done = sync_store.changes_since(connection, since, scope=scope)
        if not changes:
            if through <= since:
                break
            # A batch the lean scope emptied still moves the cursor.
            since = through
            sync_store.record_sync(connection, peer_node_id=peer_id, pushed_through=since, note="pushing")
            if done:
                break
            continue
        # Files first, so the peer never sees a row whose file it lacks.
        for change in changes if scope == "full" else ():
            if change.op != "upsert" or change.table not in sync_store.FILE_COLUMNS or change.row is None:
                continue
            column, kind = sync_store.FILE_COLUMNS[change.table]
            name = change.row.get(column)
            path = directories[kind] / name if isinstance(name, str) and name else None
            if path is not None and path.is_file() and not peer.put_file(kind, name, path.read_bytes()):
                raise SyncError("the peer did not take a file")
        reply = peer.apply(me, changes, files=scope == "full")
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

        if self._scheme == "https":
            from ..tls import client_context

            connection = http.client.HTTPSConnection(
                self._host, self._port, timeout=self._timeout, context=client_context()
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
