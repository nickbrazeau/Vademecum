"""Secrets kept in the macOS Keychain (ADR 0036): the sync token and the cloud copy's backup
key live here, not in the settings file, and are never typed, printed or put on a command
line. Through `/usr/bin/security`, whose interactive mode takes the command on standard
input, so a secret never shows in the process list. Elsewhere than macOS there is no
Keychain: reads find nothing and writes say so."""

from __future__ import annotations

import shlex
import subprocess
import sys

SECURITY = "/usr/bin/security"
SYNC_TOKEN = "Vademecum sync token"
BACKUP_KEY = "Vademecum cloud backup key"


class KeychainUnavailable(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "darwin"


def get(service: str, account: str) -> str | None:
    if not available():
        return None
    try:
        found = subprocess.run(
            [SECURITY, "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = found.stdout.strip()
    return value if found.returncode == 0 and value else None


def put(service: str, account: str, secret: str) -> None:
    """Add or replace the item. The secret goes on standard input, never on a command line."""
    if not available():
        raise KeychainUnavailable("there is no Keychain on this system")
    command = " ".join(
        ["add-generic-password", "-U", "-s", shlex.quote(service), "-a", shlex.quote(account), "-w", shlex.quote(secret)]
    )
    done = subprocess.run([SECURITY, "-i"], input=command + "\n", capture_output=True, text=True, timeout=20)
    if done.returncode != 0 or get(service, account) != secret:
        raise KeychainUnavailable("the Keychain did not keep the secret")


def delete(service: str, account: str) -> None:
    if available():
        subprocess.run([SECURITY, "delete-generic-password", "-s", service, "-a", account], capture_output=True, timeout=10)
