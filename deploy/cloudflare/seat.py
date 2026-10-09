"""Foris: Vademecum's away copy in a Cloudflare container (ADR 0017).

One process supervises two: the API (single tenancy, host mode, sync role
`away`) and the MCP gateway (OAuth, the web app behind its sign-in, the
proxied API, and the sync routes passed through). The container's disk is
not durable, so the records are restored from the snapshot store at boot and
snapshotted back every few minutes and at shutdown: a consistent copy of
each SQLite database plus the stored files.

The store is the Worker in front of this container, under `/__seat/`,
answered from an R2 bucket binding; the container presents `SEAT_KEY`.
The databases come back before anything starts; the stored files come back
in the background, since the gateway and the tools do not need them to
answer. Standard library only. Nothing here is part of the API or the MCP
packages.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

DATA = Path(os.environ.get("VADEMECUM_DATA_DIR", "/data"))
INTERVAL = int(os.environ.get("SEAT_SNAPSHOT_INTERVAL", "300"))
API_PORT = int(os.environ.get("VADEMECUM_PORT", "8765"))
MCP_PORT = int(os.environ.get("VADEMECUM_MCP_PORT", "8766"))
WEB_DIST = os.environ.get("VADEMECUM_MCP_DESK_DIST", "/app/web")

DATABASES = (("vademecum.sqlite3", "db"), ("mcp/access.sqlite3", "mcp"))
FILES = ("attachments",)
# Stored files that are retired, not kept forever: podcast audio is deleted
# once listened to (ADR 0027), so the copy in the store follows the disk.
PRUNED = ("attachments/podcasts/", "attachments/figures/")
# Pruning waits for the boot restore: until then the disk is not the truth.
_files_restored = threading.Event()
# The Worker in front carries about 100 MB in one request; anything larger
# goes up in parts, with a manifest naming them.
PART_BYTES = 64 * 1024 * 1024
# What was last uploaded, by digest, so an unchanged database is not sent again.
_uploaded: dict[str, str] = {}


def say(message: str) -> None:
    print(f"seat: {message}", flush=True)


class Store(Protocol):
    def list(self, prefix: str) -> list[tuple[str, int]]: ...
    def uploaded(self, prefix: str) -> dict[str, str]: ...
    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, data: bytes) -> bool: ...
    def delete(self, key: str) -> bool: ...


class WorkerStore:
    """The snapshot store behind the Worker: list, get and put by key."""

    def __init__(self, base_url: str, key: str, *, timeout: float = 120.0) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("SEAT_STORE_URL must be an https:// address")
        self._host = parts.hostname
        self._port = parts.port
        self._prefix = parts.path.rstrip("/")
        self._key = key
        self._timeout = timeout

    def _request(self, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
        connection = http.client.HTTPSConnection(self._host, self._port, timeout=self._timeout)
        try:
            headers = {"x-seat-key": self._key}
            if body is not None:
                headers["content-type"] = "application/octet-stream"
                headers["content-length"] = str(len(body))
            connection.request(method, f"{self._prefix}{path}", body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException) as exc:
            say(f"store unreachable: {type(exc).__name__}")
            return 0, b""
        finally:
            connection.close()

    def _objects(self, prefix: str) -> list[dict]:
        status, raw = self._request("GET", f"/list?prefix={quote(prefix, safe='/')}")
        if status != 200:
            return []
        try:
            objects = json.loads(raw.decode("utf-8")).get("objects", [])
        except ValueError:
            return []
        return [o for o in objects if isinstance(o, dict) and "key" in o]

    def list(self, prefix: str) -> list[tuple[str, int]]:
        return [(str(o["key"]), int(o["size"])) for o in self._objects(prefix)]

    def uploaded(self, prefix: str) -> dict[str, str]:
        """When each object under the prefix was written (ISO 8601), as the bucket says."""
        return {str(o["key"]): str(o.get("uploaded") or "") for o in self._objects(prefix)}

    def get(self, key: str) -> bytes | None:
        status, raw = self._request("GET", f"/object/{quote(key, safe='/')}")
        return raw if status == 200 else None

    def put(self, key: str, data: bytes) -> bool:
        status, _ = self._request("PUT", f"/object/{quote(key, safe='/')}", data)
        return status == 200

    def delete(self, key: str) -> bool:
        status, _ = self._request("DELETE", f"/object/{quote(key, safe='/')}")
        return status == 200


def store_from_environment() -> WorkerStore | None:
    url = os.environ.get("SEAT_STORE_URL", "")
    key = os.environ.get("SEAT_KEY", "")
    if not url or not key:
        return None
    return WorkerStore(url, key)


# --- durability -------------------------------------------------------------------


def _digest(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def put_object(store: Store, key: str, path: Path) -> bool:
    """One object, or parts plus a manifest when it is too large for one request."""
    size = path.stat().st_size
    if size <= PART_BYTES:
        if not store.put(key, path.read_bytes()):
            return False
        # A database that once went up in parts and now fits in one: the old manifest
        # and parts go, or a restore would bring that old copy back (feedback of 9 October).
        for stale, _size in store.list(f"{key}."):
            if stale == f"{key}.manifest" or ".part-" in stale:
                store.delete(stale)
        return True
    parts: list[str] = []
    with path.open("rb") as handle:
        index = 0
        while True:
            block = handle.read(PART_BYTES)
            if not block:
                break
            part_key = f"{key}.part-{index:04d}"
            if not store.put(part_key, block):
                return False
            parts.append(part_key)
            index += 1
    manifest = json.dumps({"size": size, "parts": parts, "sha256": _digest(path)}).encode("utf-8")
    if not store.put(f"{key}.manifest", manifest):
        return False
    store.delete(key)  # the single-object copy, if one was ever made, is now the stale one
    return True


def get_object(store: Store, key: str) -> bytes | None:
    """The object, whole or reassembled from its parts. Where both forms exist (a stale
    manifest from when the object was larger), the one written last is the truth."""
    manifest = store.get(f"{key}.manifest")
    if manifest is None:
        return store.get(key)
    times = store.uploaded(key) if hasattr(store, "uploaded") else {}
    whole_at, manifest_at = times.get(key, ""), times.get(f"{key}.manifest", "")
    if whole_at and manifest_at and whole_at > manifest_at:
        say(f"{key}: the single copy is newer than the parts; restoring it")
        return store.get(key)
    try:
        parts = json.loads(manifest.decode("utf-8"))["parts"]
    except (ValueError, KeyError, TypeError):
        return None
    pieces: list[bytes] = []
    for part_key in parts:
        piece = store.get(part_key)
        if piece is None:
            return None
        pieces.append(piece)
    return b"".join(pieces)


def restore_databases(data: Path, store: Store) -> str:
    """Bring the databases down if this container starts with no records."""
    if (data / "vademecum.sqlite3").exists():
        return "present"
    data.mkdir(parents=True, exist_ok=True)
    found = False
    for relative, prefix in DATABASES:
        target = data / relative
        payload = get_object(store, f"{prefix}/{target.name}")
        if payload is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        found = True
    return "restored" if found else "fresh"


def restore_files(data: Path, store: Store) -> int:
    """Every stored file not yet on this disk, by key. Safe to run any time."""
    restored = 0
    for name in FILES:
        listed = dict(store.list(f"{name}/"))
        wanted: dict[str, int | None] = {}
        for key, size in listed.items():
            if ".part-" in key:
                continue
            if key.endswith(".manifest"):
                wanted[key[: -len(".manifest")]] = None  # size known only from the manifest
            else:
                wanted[key] = size
        for key, size in wanted.items():
            target = data / key
            if target.exists() and (size is None or target.stat().st_size == size):
                continue
            payload = get_object(store, key)
            if payload is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            restored += 1
    return restored


def snapshot_database(data: Path, store: Store, relative: str, prefix: str) -> str:
    """A consistent copy of one database, uploaded if it changed: "db", "unchanged",
    or "failed" (said, not swallowed); a database not there yet counts as unchanged."""
    source = data / relative
    if not source.exists():
        return "unchanged"
    stage = data / ".snapshot"
    stage.mkdir(parents=True, exist_ok=True)
    copy = stage / f"{prefix}-{source.name}"
    if copy.exists():
        copy.unlink()
    with sqlite3.connect(source) as live, sqlite3.connect(copy) as backup:
        live.backup(backup)
    key = f"{prefix}/{source.name}"
    digest = _digest(copy)
    if _uploaded.get(key) == digest:
        return "unchanged"
    if put_object(store, key, copy):
        _uploaded[key] = digest
        return "db"
    say(f"snapshot of {key} failed; it is tried again next time")
    return "failed"


# The connectors' sign-ins (registered clients, tokens) are small and written rarely, and
# losing one strands ChatGPT or Claude with an app id this copy no longer knows (feedback of
# 9 October). They are saved within seconds of changing, not at the next five-minute round.
ACCESS = ("mcp/access.sqlite3", "mcp")
ACCESS_CHECK_SECONDS = 5


def access_signature(data: Path) -> tuple[tuple[int, int], ...]:
    """What changes when the sign-in database does: the file and its write-ahead log."""
    base = data / ACCESS[0]
    signature = []
    for path in (base, base.with_name(base.name + "-wal")):
        try:
            stat = path.stat()
            signature.append((stat.st_mtime_ns, stat.st_size))
        except FileNotFoundError:
            signature.append((0, 0))
    return tuple(signature)


def snapshot(data: Path, store: Store) -> dict[str, int]:
    """A consistent copy of every database that changed, then every new file."""
    results = {"db": 0, "unchanged": 0, "files": 0, "failed": 0}
    for relative, prefix in DATABASES:
        results[snapshot_database(data, store, relative, prefix)] += 1
    for name in FILES:
        root = data / name
        if not root.is_dir():
            continue
        remote = {key: size for key, size in store.list(f"{name}/")}
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            key = f"{name}/{path.relative_to(root).as_posix()}"
            size = path.stat().st_size
            if remote.get(key) == size or f"{key}.manifest" in remote:
                continue
            if put_object(store, key, path):
                results["files"] += 1
            else:
                results["failed"] += 1
    results["pruned"] = prune(data, store) if _files_restored.is_set() else 0
    return results


def prune(data: Path, store: Store) -> int:
    """Delete from the store what was retired from the disk, under PRUNED only."""
    removed = 0
    for prefix in PRUNED:
        for key, _size in store.list(prefix):
            base = key.split(".part-")[0]
            base = base[: -len(".manifest")] if base.endswith(".manifest") else base
            if (data / base).is_file():
                continue
            if store.delete(key):
                removed += 1
    return removed


# --- the two processes ------------------------------------------------------------


def answering(port: int, path: str = "/api/health") -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", path)
        return connection.getresponse().status == 200
    except OSError:
        return False
    finally:
        connection.close()


def wait_for(port: int, path: str, *, attempts: int = 120, pause: float = 0.5) -> bool:
    for _ in range(attempts):
        if answering(port, path):
            return True
        time.sleep(pause)
    return False


def api_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "VADEMECUM_DATA_DIR": str(DATA),
            "VADEMECUM_TENANCY": "single",
            "VADEMECUM_MODEL_PROVIDER": "host",
            "VADEMECUM_SYNC_ROLE": "foris",
            "VADEMECUM_SOURCES_FOLDER_ENABLED": "false",
            "VADEMECUM_PORT": str(API_PORT),
        }
    )
    return env


def gateway_environment() -> dict[str, str]:
    env = api_environment()
    env.update(
        {
            "VADEMECUM_MCP_PORT": str(MCP_PORT),
            "VADEMECUM_MCP_DESK_DIST": WEB_DIST,
            # The Worker reaches the gateway on the container's private
            # interface, not loopback; the API behind it stays loopback.
            "VADEMECUM_MCP_LISTEN_ALL": "true",
        }
    )
    return env


def run() -> int:
    required = ("VADEMECUM_MCP_PUBLIC_URL", "VADEMECUM_SYNC_ACCEPT_TOKEN", "VADEMECUM_MCP_PASSPHRASE", "SEAT_STORE_URL", "SEAT_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        say(f"not starting: missing {', '.join(missing)}")
        return 2
    store = store_from_environment()
    assert store is not None
    say(f"records: {restore_databases(DATA, store)}")
    api = subprocess.Popen([sys.executable, "-m", "vademecum"], env=api_environment())
    if not wait_for(API_PORT, "/api/health"):
        say("the API did not answer; stopping")
        api.terminate()
        return 1
    gateway = subprocess.Popen([sys.executable, "-m", "vademecum_mcp", "serve", "--http"], env=gateway_environment())
    if not wait_for(MCP_PORT, "/health"):
        say("the gateway did not answer; stopping")
        gateway.terminate()
        api.terminate()
        return 1
    say(f"up: gateway on {MCP_PORT}, snapshots every {INTERVAL}s")
    def restore_then_allow_pruning() -> None:
        say(f"files restored: {restore_files(DATA, store)}")
        _files_restored.set()

    threading.Thread(target=restore_then_allow_pruning, daemon=True).start()

    stopping = False

    def stop(signum, frame) -> None:  # noqa: ANN001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last = time.monotonic()
    last_access_check = time.monotonic()
    access_seen = access_signature(DATA)
    while not stopping:
        time.sleep(1)
        if api.poll() is not None or gateway.poll() is not None:
            say("a process exited; stopping")
            break
        if time.monotonic() - last_access_check >= ACCESS_CHECK_SECONDS:
            last_access_check = time.monotonic()
            now_seen = access_signature(DATA)
            if now_seen != access_seen:
                outcome = snapshot_database(DATA, store, *ACCESS)
                if outcome != "failed":
                    access_seen = now_seen
                if outcome == "db":
                    say("sign-ins saved")
        if time.monotonic() - last >= INTERVAL:
            say(f"snapshot: {snapshot(DATA, store)}")
            last = time.monotonic()
    say(f"final snapshot: {snapshot(DATA, store)}")
    for child in (gateway, api):
        child.terminate()
    for child in (gateway, api):
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
    return 0


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    if command == "run":
        raise SystemExit(run())
    live_store = store_from_environment()
    if live_store is None:
        print("SEAT_STORE_URL and SEAT_KEY are needed", file=sys.stderr)
        raise SystemExit(2)
    if command == "snapshot":
        say(f"snapshot: {snapshot(DATA, live_store)}")
        raise SystemExit(0)
    if command == "restore":
        say(f"records: {restore_databases(DATA, live_store)}; files: {restore_files(DATA, live_store)}")
        raise SystemExit(0)
    print("usage: seat.py [run|snapshot|restore]", file=sys.stderr)
    raise SystemExit(64)
