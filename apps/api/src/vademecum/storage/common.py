"""Shared helpers for the storage layer: stable ids, timestamps, hashes."""

from __future__ import annotations

import hashlib
import re
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


# A model told never to imply endorsement tends to say so in the text itself: "This is a
# description of the source's protocol, not an endorsement." The citation beside a statement
# already says whose it is, so the clause is dropped wherever generated prose is kept. Only a
# clause that opens with this/it/these is touched; a sentence about a real endorsement is not.
_DISCLAIMER = re.compile(
    r"(?:^|(?<=[.!?]\s)|;\s*)(?:this|it|these)\b[^.!?;]*\bnot\b[^.!?;]*\bendorsement\b[^.!?;]*(?:[.!?](?=\s|$))?",
    re.IGNORECASE,
)


def drop_disclaimers(text: str) -> str:
    """The text without any 'this is ..., not an endorsement' clause."""

    def closing(match: re.Match[str]) -> str:
        clause = match.group(0)
        return clause[-1] if clause.startswith(";") and clause[-1] in ".!?" else ""

    return " ".join(_DISCLAIMER.sub(closing, text).split())
