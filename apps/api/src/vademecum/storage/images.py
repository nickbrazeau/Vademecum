"""Pictures that belong to a source (ADR 0013). All SQL for ``source_images``."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..db import transaction
from ..ingest.images import ExtractedImage
from .common import NotFoundError, new_id, utc_now

IMAGES_DIRNAME = "attachments/images"


@dataclass(frozen=True)
class SourceImage:
    id: str
    source_id: str
    ordinal: int
    unit_index: int
    locator: str
    origin: str
    media_type: str
    byte_size: int
    width: int
    height: int
    sha256: str
    stored_name: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        """What the API returns: no stored name, no digest, no path."""
        data = asdict(self)
        del data["stored_name"]
        del data["sha256"]
        return data


def _row(row: sqlite3.Row) -> SourceImage:
    return SourceImage(**{key: row[key] for key in row.keys()})


def stored_name_for(sha256: str, media_type: str) -> str:
    return f"{sha256}.{'jpg' if media_type == 'image/jpeg' else 'png'}"


def write_file(directory: Path, image: ExtractedImage) -> str:
    """The bytes, content-addressed; a second copy of the same picture is free."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = stored_name_for(image.sha256, image.media_type)
    target = directory / name
    if not target.exists():
        target.write_bytes(image.data)
        target.chmod(0o600)
    return name


def replace_images(
    connection: sqlite3.Connection, *, source_id: str, images: list[ExtractedImage], directory: Path
) -> int:
    """This source's pictures are exactly ``images``: rows replaced, files kept by digest."""
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("DELETE FROM source_images WHERE source_id = ?", (source_id,))
        tx.execute("UPDATE sources SET images_at = ? WHERE id = ?", (now, source_id))
        for ordinal, image in enumerate(images):
            name = write_file(directory, image)
            tx.execute(
                "INSERT INTO source_images (id, source_id, ordinal, unit_index, locator, origin,"
                " media_type, byte_size, width, height, sha256, stored_name, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("img"),
                    source_id,
                    ordinal,
                    image.unit_index,
                    image.locator,
                    image.origin,
                    image.media_type,
                    len(image.data),
                    image.width,
                    image.height,
                    image.sha256,
                    name,
                    now,
                ),
            )
    return len(images)


def list_images(connection: sqlite3.Connection, source_id: str) -> list[SourceImage]:
    rows = connection.execute(
        "SELECT * FROM source_images WHERE source_id = ? ORDER BY ordinal", (source_id,)
    ).fetchall()
    return [_row(row) for row in rows]


def count_images(connection: sqlite3.Connection, source_id: str) -> int:
    return int(connection.execute("SELECT COUNT(*) FROM source_images WHERE source_id = ?", (source_id,)).fetchone()[0])


def get_image(connection: sqlite3.Connection, image_id: str) -> SourceImage:
    row = connection.execute("SELECT * FROM source_images WHERE id = ?", (image_id,)).fetchone()
    if row is None:
        raise NotFoundError("image", image_id)
    return _row(row)


def stored_names_of(connection: sqlite3.Connection, source_id: str) -> list[tuple[str, str]]:
    rows = connection.execute(
        "SELECT sha256, stored_name FROM source_images WHERE source_id = ?", (source_id,)
    ).fetchall()
    return [(row["sha256"], row["stored_name"]) for row in rows]


def remove_unreferenced(connection: sqlite3.Connection, directory: Path, names: list[tuple[str, str]]) -> int:
    """Delete files whose digest no row mentions any more. Called after a source goes."""
    removed = 0
    for sha256, name in names:
        still = connection.execute("SELECT 1 FROM source_images WHERE sha256 = ? LIMIT 1", (sha256,)).fetchone()
        if still is None and name.startswith(sha256):
            (directory / name).unlink(missing_ok=True)
            removed += 1
    return removed
