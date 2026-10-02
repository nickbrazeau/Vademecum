"""The owner's passphrase, hashed with scrypt from the standard library.

Stored as one string carrying its own parameters, so a later change to the
work factor rehashes on next set rather than breaking verification.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

MIN_LENGTH = 12

# 128 * N * r bytes of memory: 16 MiB at these values, comfortably inside the
# interpreter's default scrypt memory limit and still slow enough per guess.
N = 2**14
R = 8
P = 1
DKLEN = 32
MAXMEM = 64 * 1024 * 1024


class WeakPassphrase(ValueError):
    pass


def hash_passphrase(passphrase: str) -> str:
    if len(passphrase) < MIN_LENGTH:
        raise WeakPassphrase(f"Use at least {MIN_LENGTH} characters.")
    salt = secrets.token_bytes(16)
    digest = _derive(passphrase, salt, N, R, P)
    return "scrypt${}${}${}${}${}".format(N, R, P, _b64(salt), _b64(digest))


def verify_passphrase(passphrase: str, stored: str) -> bool:
    """Constant-time comparison; any malformed record verifies as False."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = _derive(passphrase, base64.b64decode(salt), int(n), int(r), int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _derive(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=DKLEN, maxmem=MAXMEM
    )


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")
