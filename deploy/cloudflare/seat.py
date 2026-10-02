"""The seat: Vademecum's away node in a Cloudflare container (ADR 0017).

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
MAX_OBJECT_BYTES = 95 * 1024 * 1024  # what the Worker in front will carry in one request


def say(message: str) -> None:
    print(f"seat: {message}", flush=True)


class Store(Protocol):
    def list(self, prefix: str) -> list[tuple[str, int]]: ...
    def get(self, key: str) -> bytes | None: ...
    def put(self, key: str, data: bytes) -> bool: ...


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

    def list(self, prefix: str) -> list[tuple[str, int]]:
        status, raw = self._request("GET", f"/list?prefix={quote(prefix, safe='/')}")
        if status != 200:
            return []
        try:
            objects = json.loads(raw.decode("utf-8")).get("objects", [])
        except ValueError:
            return []
        return [(str(o["key"]), int(o["size"])) for o in objects if "key" in o]

    def get(self, key: str) -> bytes | None:
        status, raw = self._request("GET", f"/object/{quote(key, safe='/')}")
        return raw if status == 200 else None

    def put(self, key: str, data: bytes) -> bool:
        status, _ = self._request("PUT", f"/object/{quote(key, safe='/')}", data)
        return status == 200


def store_from_environment() -> WorkerStore | None:
    url = os.environ.get("SEAT_STORE_URL", "")
    key = os.environ.get("SEAT_KEY", "")
    if not url or not key:
        return None
    return WorkerStore(url, key)


# --- durability -------------------------------------------------------------------


def restore_databases(data: Path, store: Store) -> str:
    """Bring the databases down if this container starts with no records."""
    if (data / "vademecum.sqlite3").exists():
        return "present"
    data.mkdir(parents=True, exist_ok=True)
    found = False
    for relative, prefix in DATABASES:
        target = data / relative
        payload = store.get(f"{prefix}/{target.name}")
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
        for key, size in store.list(f"{name}/"):
            target = data / key
            if target.exists() and target.stat().st_size == size:
                continue
            payload = store.get(key)
            if payload is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            restored += 1
    return restored


def snapshot(data: Path, store: Store) -> dict[str, int]:
    """A consistent copy of every database, then everything new up to the store."""
    stage = data / ".snapshot"
    results = {"db": 0, "files": 0, "skipped": 0}
    for relative, prefix in DATABASES:
        source = data / relative
        if not source.exists():
            continue
        stage.mkdir(parents=True, exist_ok=True)
        copy = stage / f"{prefix}-{source.name}"
        with sqlite3.connect(source) as live, sqlite3.connect(copy) as backup:
            live.backup(backup)
        if store.put(f"{prefix}/{source.name}", copy.read_bytes()):
            results["db"] += 1
    for name in FILES:
        root = data / name
        if not root.is_dir():
            continue
        remote = {key: size for key, size in store.list(f"{name}/")}
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            key = f"{name}/{path.relative_to(root).as_posix()}"
            size = path.stat().st_size
            if remote.get(key) == size:
                continue
            if size > MAX_OBJECT_BYTES:
                results["skipped"] += 1
                continue
            if store.put(key, path.read_bytes()):
                results["files"] += 1
    return results


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
            "VADEMECUM_SYNC_ROLE": "away",
            "VADEMECUM_SOURCES_FOLDER_ENABLED": "false",
            "VADEMECUM_PORT": str(API_PORT),
        }
    )
    return env


def gateway_environment() -> dict[str, str]:
    env = api_environment()
    env.update({"VADEMECUM_MCP_PORT": str(MCP_PORT), "VADEMECUM_MCP_DESK_DIST": WEB_DIST})
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
    threading.Thread(target=lambda: say(f"files restored: {restore_files(DATA, store)}"), daemon=True).start()

    stopping = False

    def stop(signum, frame) -> None:  # noqa: ANN001
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last = time.monotonic()
    while not stopping:
        time.sleep(1)
        if api.poll() is not None or gateway.poll() is not None:
            say("a process exited; stopping")
            break
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
