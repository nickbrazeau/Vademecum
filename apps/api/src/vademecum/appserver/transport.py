"""Bytes in, bytes out: the managed ``codex app-server`` process.

This module knows about pipes, line framing and exit status. It knows nothing
about JSON-RPC, accounts or rate limits. That split is what lets the whole
protocol layer above it be tested against a scripted fake with no process
anywhere -- which is the only way AGENTS.md's "no production model calls in the
test suite" can be a property rather than a promise.

Everything is stdlib. ``asyncio.create_subprocess_exec`` already gives a
line-oriented reader over a child's stdout, and a dependency that wrapped it
would add supply-chain surface to a component whose whole job is to be a
trust boundary.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from .diagnostics import event
from .errors import BridgeUnavailable
from .hardening import startup_argv

# Generous, and finite. The account and rate-limit replies are a few hundred
# bytes; a megabyte line means something has gone wrong upstream, and an
# unbounded reader would turn that into this process's memory problem.
STREAM_LIMIT = 4 * 1024 * 1024

# How long a terminate is given before the child is killed.
TERMINATE_GRACE_SECONDS = 5.0

# The child's environment, built from an allowlist rather than inherited.
# HOME is what Codex resolves `~/.codex` against, so managed ChatGPT
# credentials are found; PATH is what it resolves helpers against. Nothing else
# from this process's environment is passed on, so a variable set for the web
# app cannot change how the model bridge authenticates.
INHERITED_ENVIRONMENT = ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME", "TERM")

# Codex's own home, if the owner has moved it. Passed through by name only.
CODEX_HOME = "CODEX_HOME"


class Transport(Protocol):
    """What the protocol layer needs from a process. Nothing more."""

    async def start(self) -> None: ...

    async def send_line(self, line: str) -> None: ...

    def stdout_lines(self) -> AsyncIterator[str]: ...

    def stderr_lines(self) -> AsyncIterator[bytes]: ...

    async def stop(self) -> str:
        """Stop the child and return a short exit description."""
        ...


def child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    environment = source if source is not None else dict(os.environ)
    child = {name: environment[name] for name in INHERITED_ENVIRONMENT if name in environment}
    if CODEX_HOME in environment:
        child[CODEX_HOME] = environment[CODEX_HOME]
    return child


class SubprocessTransport:
    """One ``codex app-server`` child, spoken to over stdio.

    ``cwd`` is the data directory rather than the checkout. The App Server
    resolves project context from its working directory, and pointing it at
    this source tree would hand it a repository it has no business seeing.

    The child is spawned with ``hardening.STARTUP_CONFIG_FLAGS``. Those belong
    here rather than at a call site because they must apply to every process
    this class ever starts: a tool policy that a caller can forget to pass is
    not a policy.
    """

    def __init__(self, executable: Path, cwd: Path) -> None:
        self._executable = executable
        self._cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self.oversized_lines = 0
        self.argv: list[str] = []

    def build_argv(self) -> list[str]:
        return [str(self._executable), "app-server", *startup_argv()]

    async def start(self) -> None:
        if not self._executable.is_file() or not os.access(self._executable, os.X_OK):
            # Categorised, and without the path: an error body that names a
            # filesystem path is the same leak as a log line that does.
            raise BridgeUnavailable("codex_not_found")
        self.argv = self.build_argv()
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd),
                env=child_environment(),
                limit=STREAM_LIMIT,
            )
        except OSError as exc:
            raise BridgeUnavailable("spawn_failed") from exc

    async def send_line(self, line: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise BridgeUnavailable("not_started")
        try:
            process.stdin.write(line.encode("utf-8"))
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as exc:
            raise BridgeUnavailable("write_failed") from exc

    async def stdout_lines(self) -> AsyncIterator[str]:
        process = self._process
        if process is None or process.stdout is None:
            return
        reader = process.stdout
        while True:
            try:
                raw = await reader.readline()
            except ValueError:
                # A line longer than STREAM_LIMIT. asyncio drops the buffered
                # fragment, so reading continues; the tail of that line will
                # arrive as its own unparseable line and be counted as
                # malformed. Counted here, never held.
                self.oversized_lines += 1
                event("appserver_oversized_line", count=self.oversized_lines)
                continue
            if not raw:
                return
            yield raw.decode("utf-8", errors="replace").rstrip("\r\n")

    async def stderr_lines(self) -> AsyncIterator[bytes]:
        process = self._process
        if process is None or process.stderr is None:
            return
        reader = process.stderr
        while True:
            try:
                raw = await reader.readline()
            except ValueError:
                self.oversized_lines += 1
                continue
            if not raw:
                return
            # Yielded as bytes on purpose. The caller counts length; decoding
            # would produce a string that something could later be tempted to
            # log.
            yield raw

    async def stop(self) -> str:
        process = self._process
        if process is None:
            return "not_started"
        if process.returncode is not None:
            return _describe(process.returncode)

        if process.stdin is not None and not process.stdin.is_closing():
            # Closing stdin is the polite stop: the App Server sees EOF and
            # exits on its own terms.
            try:
                process.stdin.close()
            except (BrokenPipeError, RuntimeError):
                pass

        try:
            process.terminate()
        except ProcessLookupError:
            return _describe(process.returncode if process.returncode is not None else 0)

        try:
            await asyncio.wait_for(process.wait(), timeout=TERMINATE_GRACE_SECONDS)
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        return _describe(process.returncode)

    @property
    def returncode(self) -> int | None:
        return self._process.returncode if self._process is not None else None


def _describe(returncode: int | None) -> str:
    if returncode is None:
        return "running"
    if returncode < 0:
        return f"signal:{-returncode}"
    return f"exit:{returncode}"
