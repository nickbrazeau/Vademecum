#!/usr/bin/env python3
"""Refresh the pinned Codex App Server schema fixtures.

    scripts/refresh-codex-schemas.py [--codex PATH] [--check]

The App Server protocol evolves with the Codex CLI, so AGENTS.md treats the
*installed* schema as the source of truth. This script asks the installed CLI
for its schema bundle and copies the client-facing part of it into
``schemas/codex-app-server/`` verbatim. Nothing here writes a schema by hand.

The full bundle is ~3.6 MB across 285 files, almost all of it thread, turn and
tool surface that Vademecum does not touch. Committing all of it would make the
one thing that matters -- a protocol change under the account and envelope
surface -- invisible in review. So this copies the files the bridge actually
depends on, byte for byte, and derives four sorted method-name lists from the
generated top-level unions. The method lists are what tests compare against, so
a Codex upgrade that adds a server-initiated request the bridge does not
classify fails the suite instead of being answered by a default.

``--check`` regenerates into a temporary directory and reports whether the
committed fixtures still match, without writing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Where the ChatGPT desktop app keeps the CLI on macOS. Overridable, because a
# standalone `codex` on PATH is just as valid a source of truth.
DEFAULT_CODEX = Path("/Applications/ChatGPT.app/Contents/Resources/codex")

REPO_ROOT = Path(__file__).resolve().parent.parent
DESTINATION = REPO_ROOT / "schemas" / "codex-app-server"

# Copied byte for byte. Key is the path inside the generated bundle; value is
# the path inside the fixture set.
VERBATIM = {
    # Transport envelopes. Note the absence of a `jsonrpc` member: the default
    # stdio framing is newline-delimited JSON without a JSON-RPC version header.
    "RequestId.json": "envelope/RequestId.json",
    "JSONRPCMessage.json": "envelope/JSONRPCMessage.json",
    "JSONRPCRequest.json": "envelope/JSONRPCRequest.json",
    "JSONRPCResponse.json": "envelope/JSONRPCResponse.json",
    "JSONRPCNotification.json": "envelope/JSONRPCNotification.json",
    "JSONRPCError.json": "envelope/JSONRPCError.json",
    "JSONRPCErrorError.json": "envelope/JSONRPCErrorError.json",
    "ClientNotification.json": "envelope/ClientNotification.json",
    # Initialization.
    "v1/InitializeParams.json": "v1/InitializeParams.json",
    "v1/InitializeResponse.json": "v1/InitializeResponse.json",
    # Account state, device-code login, cancellation, rate limits.
    "v2/GetAccountParams.json": "v2/GetAccountParams.json",
    "v2/GetAccountResponse.json": "v2/GetAccountResponse.json",
    "v2/LoginAccountParams.json": "v2/LoginAccountParams.json",
    "v2/LoginAccountResponse.json": "v2/LoginAccountResponse.json",
    "v2/CancelLoginAccountParams.json": "v2/CancelLoginAccountParams.json",
    "v2/CancelLoginAccountResponse.json": "v2/CancelLoginAccountResponse.json",
    "v2/GetAccountRateLimitsResponse.json": "v2/GetAccountRateLimitsResponse.json",
    # Notifications the bridge listens for.
    "v2/AccountLoginCompletedNotification.json": "v2/AccountLoginCompletedNotification.json",
    "v2/AccountUpdatedNotification.json": "v2/AccountUpdatedNotification.json",
    "v2/AccountRateLimitsUpdatedNotification.json": "v2/AccountRateLimitsUpdatedNotification.json",
    # The server-initiated approvals the bridge answers with a decision, in both
    # the current and the legacy spelling, pinned so the decision vocabulary
    # cannot drift underneath the refusal.
    "CommandExecutionRequestApprovalResponse.json": (
        "server-requests/CommandExecutionRequestApprovalResponse.json"
    ),
    "FileChangeRequestApprovalResponse.json": (
        "server-requests/FileChangeRequestApprovalResponse.json"
    ),
    "ExecCommandApprovalResponse.json": "server-requests/ExecCommandApprovalResponse.json",
    "ApplyPatchApprovalResponse.json": "server-requests/ApplyPatchApprovalResponse.json",
    # The thread/turn surface. Pinned from the grading slice onwards because the
    # bridge now starts threads and runs schema-constrained turns: the sandbox
    # mode, the approval policy, the turn status vocabulary, the error-info
    # vocabulary and the agentMessage item shape are all read off these, and a
    # silent change to any of them is a change to what a grading turn is allowed
    # to do.
    "v2/ThreadStartParams.json": "v2/ThreadStartParams.json",
    "v2/ThreadStartResponse.json": "v2/ThreadStartResponse.json",
    "v2/TurnStartParams.json": "v2/TurnStartParams.json",
    "v2/TurnStartResponse.json": "v2/TurnStartResponse.json",
    "v2/TurnStartedNotification.json": "v2/TurnStartedNotification.json",
    "v2/TurnCompletedNotification.json": "v2/TurnCompletedNotification.json",
    "v2/TurnInterruptParams.json": "v2/TurnInterruptParams.json",
    "v2/TurnInterruptResponse.json": "v2/TurnInterruptResponse.json",
    "v2/ItemStartedNotification.json": "v2/ItemStartedNotification.json",
    "v2/ItemCompletedNotification.json": "v2/ItemCompletedNotification.json",
    "v2/ErrorNotification.json": "v2/ErrorNotification.json",
    "v2/ThreadStartedNotification.json": "v2/ThreadStartedNotification.json",
    # config/read is how the hardening layer discovers the effective MCP server
    # names at runtime instead of hardcoding them.
    "v2/ConfigReadParams.json": "v2/ConfigReadParams.json",
    "v2/ConfigReadResponse.json": "v2/ConfigReadResponse.json",
}

# Derived: the method name of every variant in a generated top-level union.
DERIVED = {
    "ClientRequest.json": "methods/client-requests.json",
    "ClientNotification.json": "methods/client-notifications.json",
    "ServerRequest.json": "methods/server-requests.json",
    "ServerNotification.json": "methods/server-notifications.json",
}


def generate(codex: Path, out: Path) -> None:
    """Run the installed CLI's own generator. No flags that widen the surface.

    ``--experimental`` is deliberately absent: the bridge does not negotiate
    experimental capabilities, so pinning an experimental contract would pin
    something it must never speak.

    ``cwd`` is the neutral output directory rather than the checkout, so the
    CLI cannot pick up this repository as a project while generating.
    """
    subprocess.run(
        [str(codex), "app-server", "generate-json-schema", "--out", str(out)],
        check=True,
        stdout=subprocess.DEVNULL,
        cwd=out,
    )


def codex_version(codex: Path, cwd: Path) -> str:
    result = subprocess.run(
        [str(codex), "--version"], check=True, capture_output=True, text=True, cwd=cwd
    )
    return result.stdout.strip()


def method_names(bundle_file: Path) -> list[str]:
    """Every ``method`` enum in a top-level ``oneOf`` union, sorted."""
    document = json.loads(bundle_file.read_text(encoding="utf-8"))
    names: set[str] = set()
    for variant in document.get("oneOf", []):
        enum = variant.get("properties", {}).get("method", {}).get("enum", [])
        names.update(enum)
    return sorted(names)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(bundle: Path, destination: Path, version: str) -> None:
    for relative in ("envelope", "v1", "v2", "server-requests", "methods"):
        shutil.rmtree(destination / relative, ignore_errors=True)

    files: dict[str, str] = {}

    for source_name, target_name in sorted(VERBATIM.items()):
        source = bundle / source_name
        if not source.is_file():
            raise SystemExit(
                f"the installed Codex CLI generated no {source_name}. The protocol has "
                "moved; update VERBATIM in this script and review the bridge."
            )
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[target_name] = sha256(target)

    derived_from: dict[str, str] = {}
    for source_name, target_name in sorted(DERIVED.items()):
        source = bundle / source_name
        if not source.is_file():
            raise SystemExit(f"the installed Codex CLI generated no {source_name}.")
        target = destination / target_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(method_names(source), indent=2) + "\n", encoding="utf-8")
        files[target_name] = sha256(target)
        derived_from[target_name] = sha256(source)

    manifest = {
        "codex_version": version,
        "generator": "codex app-server generate-json-schema --out <dir>",
        "experimental": False,
        "note": (
            "Generated, never hand-written. Regenerate with "
            "scripts/refresh-codex-schemas.py; verify with --check."
        ),
        "verbatim_files": {name: files[name] for name in sorted(VERBATIM.values())},
        "derived_files": {name: files[name] for name in sorted(DERIVED.values())},
        "derived_source_sha256": derived_from,
    }
    (destination / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def differences(left: Path, right: Path) -> list[str]:
    def tree(root: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.name != "README.md"
        }

    before, after = tree(left), tree(right)
    changed = [name for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
    return changed


def resolve_codex(explicit: Path | None) -> Path | None:
    """First of: --codex, VADEMECUM_CODEX_PATH, the ChatGPT app, PATH."""
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    from_environment = os.environ.get("VADEMECUM_CODEX_PATH", "").strip()
    if from_environment:
        candidates.append(Path(from_environment))
    candidates.append(DEFAULT_CODEX)
    on_path = shutil.which("codex")
    if on_path:
        candidates.append(Path(on_path))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, default=None, help="path to the Codex CLI")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift against the installed CLI without writing anything",
    )
    arguments = parser.parse_args()

    codex = resolve_codex(arguments.codex)
    if codex is None:
        print(
            "No Codex CLI found. Pass --codex PATH, set VADEMECUM_CODEX_PATH, or put "
            "`codex` on PATH.",
            file=sys.stderr,
        )
        return 2

    with tempfile.TemporaryDirectory(prefix="codex-schema-") as scratch:
        bundle = Path(scratch) / "bundle"
        bundle.mkdir()
        version = codex_version(codex, Path(scratch))
        generate(codex, bundle)

        if arguments.check:
            candidate = Path(scratch) / "candidate"
            candidate.mkdir()
            build(bundle, candidate, version)
            changed = differences(DESTINATION, candidate)
            if changed:
                print(f"{version}: the committed fixtures differ from the installed CLI:")
                for name in changed:
                    print(f"  {name}")
                return 1
            print(f"{version}: the committed fixtures match the installed CLI.")
            return 0

        DESTINATION.mkdir(parents=True, exist_ok=True)
        build(bundle, DESTINATION, version)

    print(f"Refreshed schemas/codex-app-server/ from {version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
