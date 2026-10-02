"""Shared helpers for the storage layer: stable ids, timestamps, hashes."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone


class NotFoundError(LookupError):
    """A record was addressed by id and does not exist."""

    def __init__(self, kind: str, record_id: str) -> None:
        super().__init__(f"{kind} {record_id!r} does not exist")
        self.kind = kind
        self.record_id = record_id


def new_id(prefix: str) -> str:
    """A stable, local, opaque id. The prefix makes stray ids self-describing."""
    return f"{prefix}_{uuid.uuid4().hex}"


def utc_now() -> str:
    """UTC timestamp, ISO 8601, millisecond precision, Z-suffixed."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def content_hash(*parts: str) -> str:
    """SHA-256 over the content fields, for change detection and migration."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()
