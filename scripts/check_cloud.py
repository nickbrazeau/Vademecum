"""Check a deployed cloud copy from outside, as someone not signed in would see it (ADR 0036).

Every API path the app has (read from its own OpenAPI document, path parameters filled
with a placeholder), plus the MCP endpoint, the snapshot store and the relay flag, is
asked for without a session, a token or a key. Anything that answers 2xx with content,
other than the few pages meant to be public, is a failure, and the script exits 1.

    apps/api/.venv/bin/python scripts/check_cloud.py https://vademecum-seat.<you>.workers.dev

Run it after every deploy. It sends only plain unauthenticated requests, a few a second,
under the Worker's rate limit.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

PUBLIC = {"/login", "/health", "/auth/passkey.js", "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource"}
EXTRA = ["/mcp", "/__seat/list", "/__seat/object/db/vademecum.sqlite3", "/__relay/wanted", "/passkeys", "/api/sync/status", "/api/sync/seat"]
PAUSE = 1.1  # under the Worker's 60 a minute


def api_paths() -> list[str]:
    from vademecum.app import create_app
    from vademecum.config import Settings

    import tempfile

    with tempfile.TemporaryDirectory() as scratch:  # the app is built only to read its paths
        app = create_app(Settings(data_dir=Path(scratch), sources_folder_enabled=False))
        listed = list(app.openapi()["paths"])
    return sorted({re.sub(r"\{[^}]+\}", "x", path if path.startswith("/api") else "/api" + path) for path in listed})


def main(base: str) -> int:
    base = base.rstrip("/")
    targets = api_paths() + EXTRA
    failures: list[str] = []
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        for path in targets:
            response = client.get(base + path)
            body = response.content[:200]
            if 200 <= response.status_code < 300 and path not in PUBLIC and body.strip():
                failures.append(f"GET {path} -> {response.status_code}")
            time.sleep(PAUSE)
        health = client.get(base + "/health").json()
        if set(health) != {"status"}:
            failures.append(f"/health says more than ok: {sorted(health)}")
    print(f"checked {len(targets) + 1} paths without signing in")
    for failure in failures:
        print(f"OPEN: {failure}")
    print("all closed" if not failures else f"{len(failures)} open")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print("usage: check_cloud.py https://<your cloud copy>", file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
