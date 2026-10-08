"""Figures for the phone's copy (feedback of 6 October: photos not rendered in the text).

The cloud copy holds records only, never the pictures read from the owner's sources, so
an encyclopedia page's figures were broken images there. The Mac now sends the cloud
copy the pictures its current pages actually place -- no others -- and the cloud copy
keeps them by image id in attachments/figures/, which the seat mirrors to R2. A picture
no page uses any more is deleted there. The Mac's own pictures are untouched.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

IMAGE_ID = re.compile(r"\Aimg_[A-Za-z0-9]{6,64}\Z")
EXTENSIONS = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}
FILE_NAME = re.compile(r"\A(img_[A-Za-z0-9]{6,64})\.(png|jpg|svg)\Z")
PER_ROUND = 60  # pictures sent in one sync round; the rest follow in the next


def figures_dir(source_dir: Path) -> Path:
    return source_dir.parent / "figures"


def referenced(connection: sqlite3.Connection) -> list[str]:
    """Every image id a current page places, in page order, once each."""
    seen: dict[str, None] = {}
    for row in connection.execute("SELECT sections FROM encyclopedia_entries WHERE status = 'current' ORDER BY updated_at DESC"):
        try:
            sections = json.loads(row["sections"] or "[]")
        except ValueError:
            continue
        for section in sections if isinstance(sections, list) else []:
            for paragraph in section.get("paragraphs") or [] if isinstance(section, dict) else []:
                for figure in paragraph.get("figures") or [] if isinstance(paragraph, dict) else []:
                    image_id = str(figure.get("image_id") or "") if isinstance(figure, dict) else ""
                    if IMAGE_ID.match(image_id):
                        seen[image_id] = None
    return list(seen)


def held(directory: Path) -> dict[str, int]:
    """The figures this copy holds: image id -> size in bytes."""
    found: dict[str, int] = {}
    if not directory.is_dir():
        return found
    for path in directory.iterdir():
        match = FILE_NAME.match(path.name)
        if match and path.is_file():
            found[match.group(1)] = path.stat().st_size
    return found


def path_for(directory: Path, image_id: str) -> tuple[Path, str] | None:
    """The held figure for an image id, with its media type, or None."""
    if not IMAGE_ID.match(image_id):
        return None
    for extension, media_type in EXTENSIONS.items():
        candidate = directory / f"{image_id}.{extension}"
        if candidate.is_file():
            return candidate, media_type
    return None


def store(directory: Path, image_id: str, extension: str, data: bytes) -> bool:
    if not IMAGE_ID.match(image_id) or extension not in EXTENSIONS or not data:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    partial = directory / f".{image_id}.{extension}.partial"
    partial.write_bytes(data)
    partial.replace(directory / f"{image_id}.{extension}")
    return True


def prune(connection: sqlite3.Connection, directory: Path) -> int:
    """Delete held figures no current page places."""
    wanted = set(referenced(connection))
    removed = 0
    for image_id in held(directory):
        if image_id not in wanted:
            found = path_for(directory, image_id)
            if found:
                found[0].unlink(missing_ok=True)
                removed += 1
    return removed
