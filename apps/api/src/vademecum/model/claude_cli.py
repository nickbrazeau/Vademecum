"""The second model connection: Claude, through the Claude Code CLI on this Mac (ADR 0019).

The same shape as the Codex bridge (ADR 0006), the same rules. The CLI is
signed in with the owner's own Claude account, by them, in a terminal; this
process holds no key and configures none. Each turn is one `claude -p` call
in the workspace's isolated empty directory, with every tool switched off, a
single turn, and the output schema the pipeline requires; the CLI returns
structured output, which is validated here again before anything is kept.
Nothing of the CLI's own text is logged or returned: a failure is a category.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..appserver.account import ModelStatus
from ..appserver.errors import BridgeError, BridgeProtocolError, BridgeTimeout, BridgeUnavailable, LoginNotSupported
from ..appserver.turns import TurnResult, assert_enforceable, validate_against
from ..storage.common import utc_now

logger = logging.getLogger("vademecum.claude")

CLAUDE_CANDIDATES = (
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)

# Every tool the CLI has, switched off: a turn reads its prompt and answers.
DISALLOWED_TOOLS = "Bash,Read,Write,Edit,MultiEdit,NotebookEdit,Glob,Grep,LS,WebFetch,WebSearch,Agent,Task,TodoWrite"
MAX_OUTPUT_BYTES = 2_000_000

DETAIL = {
    "signed_in": "Signed in to Claude through the Claude Code CLI on this Mac. Build and Grade send only the content described in their previews.",
    "signed_out": "The Claude Code CLI on this Mac is not signed in. Open a terminal, run `claude`, and sign in with your Claude account; nothing is sent until then.",
    "not_found": "The Claude Code CLI is not installed where Vademecum expects it. Install Claude Code, then try again.",
    "unavailable": "The Claude Code CLI did not answer. Nothing was sent.",
}


def default_claude_path() -> Path:
    for candidate in CLAUDE_CANDIDATES:
        if candidate.is_file():
            return candidate
    return CLAUDE_CANDIDATES[0]


@dataclass(frozen=True)
class CliReply:
    ok: bool
    payload: Any
    category: str
    session_id: str
    duration_ms: float


async def _run_cli(args: list[str], *, cwd: Path, timeout: float) -> tuple[int, bytes, bytes]:
    env = dict(os.environ)
    # The CLI's own interactive affordances are not wanted in a turn.
    env.setdefault("CI", "1")
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise BridgeTimeout()
    return process.returncode or 0, out[:MAX_OUTPUT_BYTES], err[:MAX_OUTPUT_BYTES]


def parse_reply(raw: bytes) -> CliReply:
    """The CLI's JSON result, reduced to what this process may keep."""
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return CliReply(False, None, "protocol", "", 0.0)
    if not isinstance(data, dict):
        return CliReply(False, None, "protocol", "", 0.0)
    session = str(data.get("session_id") or "")
    duration = float(data.get("duration_ms") or 0.0)
    if data.get("is_error"):
        text = str(data.get("result") or "").lower()
        category = "signed_out" if "not logged in" in text or "/login" in text else "refused"
        return CliReply(False, None, category, session, duration)
    payload = data.get("structured_output")
    if payload is None:
        # Some versions answer with the JSON in `result` as text.
        try:
            payload = json.loads(str(data.get("result") or ""))
        except ValueError:
            payload = None
    return CliReply(payload is not None, payload, "" if payload is not None else "invalid_output", session, duration)


class ClaudeCliRunner:
    """One turn, through `claude -p`, in an isolated directory, with no tools."""

    def __init__(self, path: Path, *, workspace: Path, turn_timeout: float = 180.0) -> None:
        self._path = Path(path)
        self._workspace = Path(workspace)
        self._turn_timeout = turn_timeout

    async def run(
        self,
        *,
        instructions: str,
        developer_instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        max_output_chars: int = 200_000,
    ) -> TurnResult:
        if not self._workspace.is_dir():
            raise BridgeProtocolError("workspace_missing")
        if not self._path.is_file():
            raise BridgeUnavailable("claude_not_found")
        assert_enforceable(output_schema)
        system = f"{instructions}\n\n{developer_instructions}".strip()
        args = [
            str(self._path),
            "-p",
            prompt,
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(output_schema, separators=(",", ":")),
            "--system-prompt",
            system,
            "--max-turns",
            "1",
            "--disallowedTools",
            DISALLOWED_TOOLS,
        ]
        started = time.perf_counter()
        code, out, _err = await _run_cli(args, cwd=self._workspace, timeout=self._turn_timeout)
        reply = parse_reply(out)
        if not reply.ok:
            logger.info("claude_turn_failed category=%s exit=%d", reply.category or "unknown", code)
            if reply.category == "signed_out":
                raise LoginNotSupported("signed_out")
            if reply.category in ("protocol", "invalid_output"):
                raise BridgeProtocolError(reply.category)
            raise BridgeUnavailable(reply.category or "unavailable")
        raw_chars = len(json.dumps(reply.payload))
        if raw_chars > max_output_chars:
            raise BridgeProtocolError("output_too_large")
        if not validate_against(reply.payload, output_schema):
            raise BridgeProtocolError("invalid_output")
        return TurnResult(
            payload=reply.payload,
            raw_chars=raw_chars,
            turn_id=reply.session_id or "claude",
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )


class ClaudeCliBridge:
    """Status for the Model page, in the Codex bridge's shape. Nothing is started."""

    def __init__(self, path: Path, *, working_directory: Path, request_timeout: float = 20.0) -> None:
        self._path = Path(path)
        self._cwd = Path(working_directory)
        self._timeout = request_timeout

    @property
    def client(self) -> None:  # the Codex bridge exposes a client; there is none here
        return None

    def _status(self, state: str, *, signed_in: bool, reason: str | None, detail: str) -> ModelStatus:
        return ModelStatus(
            state=state,  # type: ignore[arg-type]
            signed_in=signed_in,
            plan=None,
            rate_limits=None,
            login_pending=False,
            detail=detail,
            reason=reason,
            checked_at=utc_now(),
        )

    async def status(self) -> ModelStatus:
        if not self._path.is_file():
            return self._status("unavailable", signed_in=False, reason="claude_not_found", detail=DETAIL["not_found"])
        try:
            code, out, _err = await _run_cli([str(self._path), "auth", "status"], cwd=self._cwd, timeout=self._timeout)
        except BridgeError:
            return self._status("unavailable", signed_in=False, reason="unavailable", detail=DETAIL["unavailable"])
        try:
            data = json.loads(out.decode("utf-8", "replace"))
        except ValueError:
            data = {}
        if code == 0 and isinstance(data, dict) and data.get("loggedIn") is True:
            return self._status("signed_in", signed_in=True, reason=None, detail=DETAIL["signed_in"])
        return self._status("signed_out", signed_in=False, reason="signed_out", detail=DETAIL["signed_out"])

    async def start_device_login(self) -> Any:
        # Sign-in is the CLI's own, in a terminal, by the owner. Never from here.
        raise LoginNotSupported("terminal_login")

    async def cancel_login(self) -> str:
        return "nothing_pending"

    async def restart(self) -> ModelStatus:
        return await self.status()

    async def aclose(self) -> None:
        return None
