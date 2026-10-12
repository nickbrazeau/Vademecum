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
PAUSE = 2.0  # well under the Worker's 60 a minute, leaving room for the Mac's own sync


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
    unverified: list[str] = []
    with httpx.Client(timeout=90, follow_redirects=False) as client:

        def ask(path: str) -> httpx.Response | None:
            """A cold container or the rate limit is not an answer: wait and ask again."""
            for attempt in range(5):
                try:
                    response = client.get(base + path)
                except httpx.HTTPError:
                    time.sleep(10 * (attempt + 1))
                    continue
                if response.status_code in (429, 502, 503):
                    time.sleep(15 * (attempt + 1))
                    continue
                return response
            return None

        for path in targets:
            response = ask(path)
            if response is None:
                unverified.append(path)
            elif 200 <= response.status_code < 300 and path not in PUBLIC and response.content[:200].strip():
                failures.append(f"GET {path} -> {response.status_code}")
            time.sleep(PAUSE)
        health = ask("/health")
        if health is None:
            unverified.append("/health")
        elif set(health.json()) != {"status"}:
            failures.append(f"/health says more than ok: {sorted(health.json())}")
    print(f"checked {len(targets) + 1} paths without signing in")
    for path in unverified:
        print(f"UNVERIFIED (no answer, or rate-limited): {path}")
    if unverified:
        failures.append(f"{len(unverified)} not verified")
    for failure in failures:
        print(f"OPEN: {failure}")
    print("all closed" if not failures else f"{len(failures)} open")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith("https://"):
        print("usage: check_cloud.py https://<your cloud copy>", file=sys.stderr)
        raise SystemExit(64)
    raise SystemExit(main(sys.argv[1]))
