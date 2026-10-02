"""Pictures and on-device reading for sources stored before ADR 0013.

A source taken in before pictures were kept has no image rows and a NULL
``images_at``. This pass reads each such original once more through the
whole intake -- text, recognition, pictures -- and records the result through
the same ``store_upload`` path an upload takes, so a scan that was image-only
and can now be read is re-extracted (and its dependants invalidated for
revalidation) exactly as a re-upload would be. Runs in the background after
start-up, one source at a time, and marks every row it visits so it never
visits it again, including rows whose original has gone.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from ..db import transaction
from ..storage import images as image_store
from ..storage import sources as store
from ..storage.common import utc_now
from . import extract_all

logger = logging.getLogger("vademecum.ingest")


def pending(connection: sqlite3.Connection) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM sources WHERE images_at IS NULL").fetchone()[0])


def backfill_pictures(connection: sqlite3.Connection, *, source_dir: Path, limit: int | None = None) -> dict:
    """Gather pictures for every source that has not had them gathered."""
    rows = connection.execute(
        "SELECT id, pile_id, display_name, media_type, sha256, byte_size, confidence, stored_name"
        " FROM sources WHERE images_at IS NULL ORDER BY created_at, id"
    ).fetchall()
    if limit is not None:
        rows = rows[:limit]
    visited = reread = missing = pictures = 0
    for row in rows:
        visited += 1
        try:
            payload = (source_dir / row["stored_name"]).read_bytes()
        except OSError:
            missing += 1
            with transaction(connection) as tx:
                tx.execute("UPDATE sources SET images_at = ? WHERE id = ?", (utc_now(), row["id"]))
            continue
        kind = store._kind_of(row["media_type"])  # noqa: SLF001 - the storage layer's own mapping
        extraction, images = extract_all(kind, payload)
        stored = store.store_upload(
            connection,
            pile_id=row["pile_id"],
            display_name=row["display_name"],
            media_type=row["media_type"],
            sha256=row["sha256"],
            byte_size=row["byte_size"],
            confidence=row["confidence"],
            extraction=extraction,
        )
        if stored.outcome == "re_extracted":
            reread += 1
        pictures += image_store.replace_images(
            connection, source_id=stored.source.id, images=images, directory=source_dir.parent / "images"
        )
    if visited:
        logger.info("pictures_backfilled sources=%d reread=%d missing=%d pictures=%d", visited, reread, missing, pictures)
    return {"visited": visited, "reread": reread, "missing": missing, "pictures": pictures}
