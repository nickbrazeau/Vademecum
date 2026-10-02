"""Storage boundary.

Everything in here takes a connection and returns domain objects. HTTP handlers
own no SQL; every query is parameterised (ADR 0005).
"""

from .common import NotFoundError, content_hash, new_id, utc_now

__all__ = ["NotFoundError", "content_hash", "new_id", "utc_now"]
