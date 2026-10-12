"""Sealing the seat's backups with the owner's own key (ADR 0036).

Every object the seat puts in the bucket -- the databases, the stored files, the
manifests of anything sent in parts -- is sealed first with AES-256-GCM under a key made
on the owner's computer and given to this container as a secret. The bucket holds only
what this produces; Cloudflare's storage never has the key.

Format: ``VDMC1`` | key id (8 bytes) | nonce prefix (8 bytes) | chunks. The plaintext is
cut into 4 MiB chunks (at least one, so an empty object still carries a tag); chunk i is
sealed with the nonce prefix + i (4 bytes, big-endian) and, as associated data, the
object's own key, i and whether it is the last chunk. So a chunk cannot be moved to
another object or another place, the end cannot be cut off, and a sealed object cannot be
renamed. The sealed length follows exactly from the plaintext length, so sizes can still
be compared without opening anything.

An object without the header is a backup from before sealing. It is read only until the
bucket carries the marker ``mcp/sealed``, which is written once everything has been
sealed; from then on it is refused.
"""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VDMC1"
KEY_ID_BYTES = 8
PREFIX_BYTES = 8
HEADER = len(MAGIC) + KEY_ID_BYTES + PREFIX_BYTES
CHUNK = 4 * 1024 * 1024
TAG = 16
MARKER = "mcp/sealed"
ENV = "SEAT_BACKUP_KEY"


class SealError(Exception):
    """An object that cannot be opened: altered, cut short, sealed for another key, or
    plaintext where only sealed objects are accepted. The message says which."""


def key_from_text(text: str) -> bytes:
    """The key as the secret carries it: 32 bytes, base64."""
    try:
        key = base64.b64decode(text.strip(), validate=True)
    except ValueError as exc:
        raise SealError("the backup key is not base64") from exc
    if len(key) != 32:
        raise SealError("the backup key is not 32 bytes")
    return key


def key_id(key: bytes) -> bytes:
    return hashlib.sha256(key).digest()[:KEY_ID_BYTES]


def fingerprint(key: bytes) -> str:
    """The key id as text: safe to show and to compare, says nothing of the key."""
    return key_id(key).hex()


def _chunks(length: int) -> int:
    return max(1, -(-length // CHUNK))


def sealed_size(length: int) -> int:
    return HEADER + length + TAG * _chunks(length)


def opened_size(sealed: int) -> int:
    """The plaintext length a sealed length came from, or -1 if no length could have."""
    body = sealed - HEADER
    if body < TAG:
        return -1
    chunks = -(-body // (CHUNK + TAG))
    length = body - TAG * chunks
    return length if length >= 0 and sealed_size(length) == sealed else -1


def _aad(object_key: str, index: int, last: bool) -> bytes:
    return object_key.encode("utf-8") + struct.pack(">I?", index, last)


def seal(key: bytes, object_key: str, data: bytes) -> bytes:
    cipher = AESGCM(key)
    prefix = os.urandom(PREFIX_BYTES)
    count = _chunks(len(data))
    out = [MAGIC, key_id(key), prefix]
    for index in range(count):
        block = data[index * CHUNK : (index + 1) * CHUNK]
        nonce = prefix + struct.pack(">I", index)
        out.append(cipher.encrypt(nonce, block, _aad(object_key, index, index == count - 1)))
    return b"".join(out)


def is_sealed(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC


def open_sealed(key: bytes, object_key: str, blob: bytes) -> bytes:
    if not is_sealed(blob):
        raise SealError(f"{object_key}: not sealed")
    if blob[len(MAGIC) : len(MAGIC) + KEY_ID_BYTES] != key_id(key):
        raise SealError(f"{object_key}: sealed with another key")
    length = opened_size(len(blob))
    if length < 0:
        raise SealError(f"{object_key}: cut short or padded")
    prefix = blob[len(MAGIC) + KEY_ID_BYTES : HEADER]
    cipher = AESGCM(key)
    count = _chunks(length)
    pieces: list[bytes] = []
    at = HEADER
    for index in range(count):
        size = min(CHUNK, length - index * CHUNK) + TAG
        nonce = prefix + struct.pack(">I", index)
        try:
            pieces.append(cipher.decrypt(nonce, blob[at : at + size], _aad(object_key, index, index == count - 1)))
        except InvalidTag as exc:
            raise SealError(f"{object_key}: altered, moved or sealed for another object") from exc
        at += size
    return b"".join(pieces)


class SealedStore:
    """A store that seals what it puts and opens what it gets (see the module's note).

    ``list`` reports each sealed object at its plaintext size, so the seat's own size
    checks work unchanged; an unsealed object is reported at -1, which never matches a
    file on disk, so the next snapshot seals it and a restore reads it afresh."""

    def __init__(self, inner: Any, key: bytes) -> None:
        self.inner = inner
        self.key = key
        self._accept_plain: bool | None = None
        self._seal_seconds = 0.0

    # -- what the bucket holds -----------------------------------------------------

    def _entries(self, prefix: str) -> list[tuple[str, int, bool]]:
        listing = getattr(self.inner, "entries", None)
        if callable(listing):
            return listing(prefix)
        found = []
        for name, size in self.inner.list(prefix):
            blob = self.inner.get(name) or b""
            found.append((name, size, is_sealed(blob)))
        return found

    def list(self, prefix: str) -> list[tuple[str, int]]:
        return [(name, opened_size(size) if sealed else -1) for name, size, sealed in self._entries(prefix)]

    def uploaded(self, prefix: str) -> dict[str, str]:
        times = getattr(self.inner, "uploaded", None)
        return times(prefix) if callable(times) else {}

    def accepts_plain(self) -> bool:
        """Until the marker is in the bucket, a backup from before sealing is still read."""
        if self._accept_plain is None:
            self._accept_plain = self.inner.get(MARKER) is None
        return self._accept_plain

    def status(self, prefixes: tuple[str, ...]) -> dict[str, Any]:
        """Counts of what the bucket holds. A whole copy left from before an object went up in
        parts (its manifest sealed beside it) is never read again; it is counted apart, as
        superseded, rather than as unsealed."""
        entries = [entry for prefix in prefixes for entry in self._entries(prefix) if entry[0] != MARKER]
        sealed_names = {name for name, _size, is_it in entries if is_it}
        objects = sealed = superseded = 0
        for name, _size, is_it in entries:
            if not is_it and f"{name}.manifest" in sealed_names:
                superseded += 1
                continue
            objects += 1
            sealed += int(is_it)
        return {
            "objects": objects,
            "sealed": sealed,
            "plain": objects - sealed,
            "superseded": superseded,
            "key_id": fingerprint(self.key),
        }

    def mark_sealed(self) -> bool:
        if self.inner.get(MARKER) is not None:
            self._accept_plain = False
            return True
        done = self._put_raw(MARKER, seal(self.key, MARKER, b"sealed"))
        if done:
            self._accept_plain = False
        return done

    # -- reading and writing ----------------------------------------------------------

    def get(self, name: str) -> bytes | None:
        blob = self.inner.get(name)
        if blob is None:
            return None
        if is_sealed(blob):
            return open_sealed(self.key, name, blob)
        if self.accepts_plain():
            return blob
        raise SealError(f"{name}: not sealed, and every backup has been sealed since")

    def _put_raw(self, name: str, blob: bytes) -> bool:
        try:
            return bool(self.inner.put(name, blob, sealed=True))
        except TypeError:  # a store that does not record it
            return bool(self.inner.put(name, blob))

    def put(self, name: str, data: bytes) -> bool:
        import time

        started = time.perf_counter()
        blob = seal(self.key, name, data)
        self._seal_seconds += time.perf_counter() - started
        return self._put_raw(name, blob)

    def take_seal_seconds(self) -> float:
        """Time spent sealing since last asked: logged per snapshot, to check the cost."""
        spent, self._seal_seconds = self._seal_seconds, 0.0
        return spent

    def delete(self, name: str) -> bool:
        return bool(self.inner.delete(name))
