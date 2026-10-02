"""The local product (ADR 0012): one folder, one command, the assistant on this Mac.

Codex (in the ChatGPT app) and Claude Desktop start MCP servers themselves,
as local processes over stdio, from an entry in a configuration file each of
them owns. This module is what makes Vademecum one such entry:

* ``ensure_api`` -- in stdio mode the MCP server starts the API beside itself
  when nothing is listening, so a learner needs no second command and no
  terminal left open. The API is a child process; it stops when the
  assistant stops the server.
* ``write_codex_config`` / ``write_claude_config`` -- register the server with
  the assistant, replacing an older Vademecum entry, keeping everything else
  in the file, and leaving a backup beside it.

Nothing here talks to the network; the API is found the way every tool finds
it, through the one loopback client.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .api_client import ApiClient, ApiError

SERVER_NAME = "vademecum"
API_START_ATTEMPTS = 120  # quarter-seconds: thirty seconds for a first start with migrations

CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
CLAUDE_CONFIG = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"


# --- the API beside the server --------------------------------------------------------


async def api_answering(api: ApiClient) -> bool:
    try:
        await api.get("/api/health")
        return True
    except ApiError:
        return False


def start_api(log_path: Path, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    """The API as a child of this process, its output kept off stdio.

    stdout is the MCP protocol channel here, so the child's output goes to a
    log file in the data directory. Host mode unless the caller chose
    otherwise: the assistant on the other end of stdio is the model.
    """
    environment = dict(os.environ if env is None else env)
    environment.setdefault("VADEMECUM_MODEL_PROVIDER", "host")
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = open(log_path, "ab")  # noqa: SIM115 - held by the child until it exits
    return subprocess.Popen(
        [sys.executable, "-m", "vademecum"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


async def ensure_api(
    api: ApiClient,
    *,
    start: Callable[[], Any],
    attempts: int = API_START_ATTEMPTS,
    pause: float = 0.25,
) -> Any | None:
    """Start the API if nothing answers; return the child to stop later, or None."""
    if await api_answering(api):
        return None
    child = start()
    for _ in range(attempts):
        if await api_answering(api):
            return child
        exited = getattr(child, "poll", lambda: None)()
        if exited is not None:
            raise RuntimeError("the API stopped while starting; see its log in the data directory")
        await asyncio.sleep(pause)
    stop_child(child)
    raise RuntimeError("the API did not answer in time; see its log in the data directory")


def stop_child(child: Any) -> None:
    terminate = getattr(child, "terminate", None)
    if terminate is None:
        return
    terminate()
    try:
        child.wait(timeout=10)
    except Exception:  # noqa: BLE001 - a child that will not stop is killed
        kill = getattr(child, "kill", None)
        if kill is not None:
            kill()


# --- registering with the assistants -----------------------------------------------------


def _backup(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    backup.write_bytes(path.read_bytes())
    return backup


_TOML_TABLE = re.compile(r"^\s*\[")
_OUR_TABLE = re.compile(rf"^\s*\[mcp_servers\.{SERVER_NAME}(\.[^\]]*)?\]\s*$")


def _toml_string(value: str) -> str:
    return json.dumps(value)  # a JSON string literal is a valid TOML basic string


def write_codex_config(path: Path, *, command: str, args: list[str]) -> Path | None:
    """Add or replace ``[mcp_servers.vademecum]`` in Codex's TOML.

    Line-based on purpose: the standard library reads TOML but does not write
    it, and a rewrite that reserialised the whole file would lose the owner's
    comments and ordering. Every line outside our table is kept as it is.
    """
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept: list[str] = []
    skipping = False
    for line in lines:
        if _TOML_TABLE.match(line):
            skipping = bool(_OUR_TABLE.match(line))
        if not skipping:
            kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    block = [
        "",
        f"[mcp_servers.{SERVER_NAME}]",
        "enabled = true",
        f"command = {_toml_string(command)}",
        "args = [" + ", ".join(_toml_string(arg) for arg in args) + "]",
        "startup_timeout_sec = 60",
        "",
    ]
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text("\n".join(kept + block), encoding="utf-8")
    return backup


def write_claude_config(path: Path, *, command: str, args: list[str]) -> Path | None:
    """Add or replace ``mcpServers.vademecum`` in Claude Desktop's JSON."""
    data: dict[str, Any] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        if not isinstance(loaded, dict):
            raise ValueError("Claude Desktop's configuration is not a JSON object")
        data = loaded
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}
    servers[SERVER_NAME] = {"command": command, "args": list(args)}
    data["mcpServers"] = servers
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return backup


def launcher() -> tuple[str, list[str]]:
    """The command an assistant runs: this checkout's launcher, absolute."""
    from vademecum.config import find_repo_root

    root = find_repo_root()
    if root is None:
        raise RuntimeError("cannot find the checkout; run setup from inside it")
    return str(root / "scripts" / "mcp.sh"), ["--stdio"]
