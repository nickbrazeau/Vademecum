"""The seat: Vademecum's away node in a Cloudflare container (ADR 0017).

One process supervises two: the API (single tenancy, host mode, sync role
`away`) and the MCP gateway (OAuth, the web app behind its sign-in, the
proxied API, and the sync routes passed through). The container's disk is
not durable, so the records are restored from object storage at boot and
snapshotted back every few minutes and at shutdown: a consistent copy of
each SQLite database plus the stored files. `rclone` does the copying with
R2's S3-compatible endpoint; its credentials arrive as environment secrets.

Standard library only. Nothing here is part of the API or the MCP packages.
"""

from __future__ import annotations

import http.client
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

DATA = Path(os.environ.get("VADEMECUM_DATA_DIR", "/data"))
REMOTE = os.environ.get("SEAT_REMOTE", "r2:vademecum")
INTERVAL = int(os.environ.get("SEAT_SNAPSHOT_INTERVAL", "300"))
API_PORT = int(os.environ.get("VADEMECUM_PORT", "8765"))
MCP_PORT = int(os.environ.get("VADEMECUM_MCP_PORT", "8766"))
WEB_DIST = os.environ.get("VADEMECUM_MCP_DESK_DIST", "/app/web")

DATABASES = (("vademecum.sqlite3", "db"), ("mcp/access.sqlite3", "mcp"))
FILES = ("attachments",)

Runner = Callable[..., int]


def say(message: str) -> None:
    print(f"seat: {message}", flush=True)


def rclone(*args: str) -> int:
    return subprocess.run(["rclone", *args, "--quiet"], check=False).returncode


# --- durability -------------------------------------------------------------------


def restore(data: Path = DATA, remote: str = REMOTE, run: Runner = rclone) -> str:
    """Bring the last snapshot down if this container starts with no records."""
    if (data / "vademecum.sqlite3").exists():
        return "present"
    data.mkdir(parents=True, exist_ok=True)
    for relative, prefix in DATABASES:
        target = data / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        run("copy", f"{remote}/{prefix}", str(target.parent), "--include", target.name)
    for name in FILES:
        run("copy", f"{remote}/{name}", str(data / name))
    return "restored" if (data / "vademecum.sqlite3").exists() else "fresh"


def snapshot(data: Path = DATA, remote: str = REMOTE, run: Runner = rclone) -> dict[str, int]:
    """A consistent copy of every database, then everything up to object storage."""
    stage = data / ".snapshot"
    results: dict[str, int] = {}
    for relative, prefix in DATABASES:
        source = data / relative
        if not source.exists():
            continue
        copy_dir = stage / prefix
        copy_dir.mkdir(parents=True, exist_ok=True)
        copy = copy_dir / source.name
        with sqlite3.connect(source) as live, sqlite3.connect(copy) as backup:
            live.backup(backup)
        results[prefix] = run("sync", str(copy_dir), f"{remote}/{prefix}")
    for name in FILES:
        if (data / name).is_dir():
            results[name] = run("sync", str(data / name), f"{remote}/{name}")
    return results


# --- the two processes ------------------------------------------------------------


def answering(port: int, path: str = "/api/health") -> bool:
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("GET", path)
        return connection.getresponse().status == 200
    except OSError:
        return False
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


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
    required = ("VADEMECUM_MCP_PUBLIC_URL", "VADEMECUM_SYNC_ACCEPT_TOKEN", "VADEMECUM_MCP_PASSPHRASE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        say(f"not starting: missing {', '.join(missing)}")
        return 2
    say(f"records: {restore()}")
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
    say(f"up: gateway on {MCP_PORT}, snapshots every {INTERVAL}s to {REMOTE}")

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
            say(f"snapshot: {snapshot()}")
            last = time.monotonic()
    say(f"final snapshot: {snapshot()}")
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
    if command == "snapshot":
        say(f"snapshot: {snapshot()}")
        raise SystemExit(0)
    if command == "restore":
        say(f"records: {restore()}")
        raise SystemExit(0)
    print("usage: seat.py [run|snapshot|restore]", file=sys.stderr)
    raise SystemExit(64)
