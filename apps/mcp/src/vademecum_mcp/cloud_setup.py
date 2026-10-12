"""`setup cloud`: a learner's own cloud copy, in one command (ADR 0036).

Everyone runs their own copy: their own Cloudflare account, bucket, Worker and secrets,
and nothing shared with anyone. This makes it one command instead of a page of steps:

* a new copy: sign-in checked, a bucket made, the Worker deployed, every secret made
  here and handed over on standard input, the Mac told where its copy is;
* ``--adopt``, a copy that already exists: its backups sealed with a key made here (the
  one thing it lacked), and the Mac's sync token moved into the Keychain;
* ``--rotate``: a new store key and sync token. (The backup key is not rotated: every
  backup is sealed with it.)

The backup key and the sync token are made here and kept in the Keychain; the user never
types or sees them. The passphrase, the way back in when Face ID is not at hand, is the
one thing asked for. No secret is printed or put on a command line.
"""

from __future__ import annotations

import base64
import getpass
import os
import re
import secrets
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

ADDRESS = re.compile(r"https://[a-z0-9.-]+\.workers\.dev")
LOCAL_CONFIG = "wrangler.local.jsonc"  # a learner's own, never committed


@dataclass
class Step:
    """One wrangler call: what it was, and nothing secret (secrets go on standard input)."""

    args: list[str]
    secret_input: bool = False


@dataclass
class Result:
    code: int
    output: str


@dataclass
class Runner:
    """Runs wrangler in the Worker's folder; tests give a fake one."""

    worker_dir: Path
    calls: list[Step] = field(default_factory=list)

    def __call__(self, args: list[str], *, secret: str | None = None) -> Result:
        self.calls.append(Step(args, secret is not None))
        done = subprocess.run(
            ["npx", "--yes", "wrangler", *args],
            cwd=self.worker_dir,
            input=secret,
            capture_output=True,
            text=True,
            timeout=1800,
            env=os.environ.copy(),
        )
        return Result(done.returncode, done.stdout + done.stderr)


class SetupFailed(RuntimeError):
    def __init__(self, step: str, detail: str = "") -> None:
        super().__init__(step)
        self.step = step
        self.detail = detail


def _check(result: Result, step: str) -> Result:
    if result.code != 0:
        raise SetupFailed(step, result.output[-600:])
    return result


def new_backup_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode()


def backup_key_for(host: str, keychain) -> tuple[str, bool]:  # noqa: ANN001
    """The copy's backup key from the Keychain, made there if it is not yet: (key, made)."""
    found = keychain.get(keychain.BACKUP_KEY, host)
    if found:
        return found, False
    key = new_backup_key()
    keychain.put(keychain.BACKUP_KEY, host, key)
    return key, True


def fingerprint(key_text: str) -> str:
    import hashlib

    return hashlib.sha256(base64.b64decode(key_text)).digest()[:8].hex()


def _put_secret(run: Callable[..., Result], name: str, value: str, config: list[str]) -> None:
    _check(run(["secret", "put", name, *config], secret=value), f"setting the secret {name}")


def adopt(*, run: Callable[..., Result], keychain, address: str, say: Callable[[str], None]) -> dict:  # noqa: ANN001
    """An existing copy: seal its backups with a key made here, move the sync token into the Keychain."""
    from vademecum.config import get_settings, remove_setting

    host = urlsplit(address).hostname or ""
    _check(run(["whoami"]), "checking you are signed in to Cloudflare (npx wrangler login)")
    key, made = backup_key_for(host, keychain)
    say(("Made a backup key and kept it in the Keychain" if made else "Using the backup key in the Keychain") + f" (fingerprint {fingerprint(key)}).")
    _put_secret(run, "SEAT_BACKUP_KEY", key, [])
    say("Gave it to your cloud copy as a secret.")
    _check(run(["deploy"]), "deploying the cloud copy")
    say("Deployed. Your cloud copy seals its backups from its next start.")
    settings = get_settings()
    if settings.sync_token and settings.sync_peer_url:
        keychain.put(keychain.SYNC_TOKEN, urlsplit(settings.sync_peer_url).hostname or host, settings.sync_token)
        remove_setting("SYNC_TOKEN")
        say("Moved the sync token into the Keychain.")
    return {"address": address, "fingerprint": fingerprint(key), "made_key": made}


def rotate(*, run: Callable[..., Result], keychain, address: str, config: list[str], say: Callable[[str], None]) -> dict:  # noqa: ANN001
    """A new store key and sync token; the deploy that follows restarts the copy on them."""
    host = urlsplit(address).hostname or ""
    _check(run(["whoami"]), "checking you are signed in to Cloudflare (npx wrangler login)")
    token = secrets.token_urlsafe(32)
    _put_secret(run, "SEAT_KEY", secrets.token_urlsafe(32), config)
    _put_secret(run, "VADEMECUM_SYNC_ACCEPT_TOKEN", token, config)
    _check(run(["deploy", *config]), "deploying the cloud copy")
    keychain.put(keychain.SYNC_TOKEN, host, token)
    say("New store key and sync token in place; the Mac's token is in the Keychain.")
    return {"address": address}


def create(
    *,
    run: Callable[..., Result],
    keychain,  # noqa: ANN001
    worker_dir: Path,
    passphrase: str,
    say: Callable[[str], None],
) -> dict:
    """A new copy on the signed-in Cloudflare account."""
    from vademecum.config import write_setting

    if len(passphrase) < 12:
        raise SetupFailed("the passphrase must be at least 12 characters")
    _check(run(["whoami"]), "checking you are signed in to Cloudflare (npx wrangler login)")
    bucket = f"vademecum-{secrets.token_hex(3)}"
    _check(run(["r2", "bucket", "create", bucket]), "making the backup bucket")
    say(f"Made the bucket {bucket}.")
    template = (worker_dir / "wrangler.jsonc").read_text(encoding="utf-8")
    local = worker_dir / LOCAL_CONFIG
    local.write_text(re.sub(r'"bucket_name": "[^"]*"', f'"bucket_name": "{bucket}"', template), encoding="utf-8")
    config = ["-c", LOCAL_CONFIG]
    first = _check(run(["deploy", *config]), "the first deploy")
    found = ADDRESS.search(first.output)
    if found is None:
        raise SetupFailed("reading the new copy's address from the deploy")
    address = found.group(0)
    host = urlsplit(address).hostname or ""
    say(f"Your cloud copy is at {address}.")
    sync_token = secrets.token_urlsafe(32)
    backup_key, _made = backup_key_for(host, keychain)
    for name, value in (
        ("SEAT_KEY", secrets.token_urlsafe(32)),
        ("VADEMECUM_SYNC_ACCEPT_TOKEN", sync_token),
        ("VADEMECUM_MCP_PASSPHRASE", passphrase),
        ("SEAT_BACKUP_KEY", backup_key),
    ):
        _put_secret(run, name, value, config)
    local.write_text(
        re.sub(r'"PUBLIC_URL": "[^"]*"', f'"PUBLIC_URL": "{address}"', local.read_text(encoding="utf-8")), encoding="utf-8"
    )
    _check(run(["deploy", *config]), "the deploy that tells the copy its own address")
    write_setting("SYNC_PEER_URL", address)
    keychain.put(keychain.SYNC_TOKEN, host, sync_token)
    say("This Mac syncs with it; the sync token and the backup key are in the Keychain.")
    return {"address": address, "fingerprint": fingerprint(backup_key), "bucket": bucket}


def main(mode: str, *, address: str | None = None) -> int:
    """Run from `setup cloud`. Returns an exit code; says each step as it goes."""
    from vademecum import keychain
    from vademecum.config import find_repo_root, get_settings

    root = find_repo_root()
    if root is None:
        print("Run this from the Vademecum checkout: the Worker is deployed from it.", file=sys.stderr)
        return 2
    if not keychain.available():
        print("This needs the macOS Keychain, where the keys are kept.", file=sys.stderr)
        return 2
    worker_dir = root / "deploy" / "cloudflare" / "worker"
    run = Runner(worker_dir)
    say = print
    try:
        if mode == "adopt":
            target = address or get_settings().sync_peer_url
            if not target:
                print("Give the copy's address: setup cloud --adopt https://...", file=sys.stderr)
                return 64
            outcome = adopt(run=run, keychain=keychain, address=target, say=say)
        elif mode == "rotate":
            target = address or get_settings().sync_peer_url
            config = ["-c", LOCAL_CONFIG] if (worker_dir / LOCAL_CONFIG).exists() else []
            outcome = rotate(run=run, keychain=keychain, address=target, config=config, say=say)
        else:
            if not sys.stdin.isatty():
                print("Run this from a terminal: it asks for a passphrase once.", file=sys.stderr)
                return 2
            first = getpass.getpass("A recovery passphrase (12+ characters), for when Face ID is not at hand: ")
            if getpass.getpass("The same again: ") != first:
                print("The two did not match; nothing was changed.", file=sys.stderr)
                return 2
            outcome = create(run=run, keychain=keychain, worker_dir=worker_dir, passphrase=first, say=say)
    except SetupFailed as exc:
        print(f"Stopped at: {exc.step}.", file=sys.stderr)
        if exc.detail:
            print(exc.detail, file=sys.stderr)
        return 1
    check = subprocess.run([sys.executable, str(root / "scripts" / "check_cloud.py"), outcome["address"]], timeout=900)
    print()
    print(f"Open {outcome['address']} on your phone, sign in with the passphrase once, then Settings → Face ID and sessions → Add Face ID.")
    print("Restart Vademecum on this Mac so it picks up the changes.")
    return 0 if check.returncode == 0 else 1
