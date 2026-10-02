"""Schematics the assistant draws for a learning point (ADR 0013).

SVG only, because SVG is text the learner can open anywhere and the assistant
can write without a renderer. Checked before it is kept: well-formed, an SVG
root, no script, no event handler, no foreign object, no reference that
leaves the document. A schematic is a study aid attached to a claim; it
carries that claim's support label wherever it is shown and is never itself
"verified".
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

SCHEMATICS_DIRNAME = "attachments/schematics"
MAX_SVG_BYTES = 512 * 1024
SVG_NS = "{http://www.w3.org/2000/svg}"
FORBIDDEN_TAGS = {"script", "foreignObject", "iframe", "object", "embed", "animate", "set", "image"}
HREF_ATTRIBUTES = {"href", "{http://www.w3.org/1999/xlink}href"}


class InvalidSchematic(ValueError):
    """An SVG that will not be kept, with a reason a person can act on."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Schematic:
    id: str
    learning_point_id: str
    title: str
    media_type: str
    byte_size: int
    sha256: str
    stored_name: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        del data["stored_name"]
        del data["sha256"]
        return data


def validate_svg(svg: str) -> bytes:
    """The SVG as bytes to store, or ``InvalidSchematic`` saying why not."""
    data = svg.strip().encode("utf-8")
    if not data:
        raise InvalidSchematic("The schematic is empty.")
    if len(data) > MAX_SVG_BYTES:
        raise InvalidSchematic(f"The schematic is larger than {MAX_SVG_BYTES // 1024} KB. Simplify it.")
    try:
        root = ElementTree.fromstring(data)
    except Exception:  # noqa: BLE001 - defusedxml raises several kinds; none should be echoed
        raise InvalidSchematic("The schematic is not well-formed SVG.") from None
    if root.tag != f"{SVG_NS}svg":
        raise InvalidSchematic("The document's root element must be <svg> in the SVG namespace.")
    for element in root.iter():
        tag = element.tag.split("}", 1)[-1] if isinstance(element.tag, str) else ""
        if tag in FORBIDDEN_TAGS:
            raise InvalidSchematic(f"<{tag}> is not allowed in a schematic.")
        for name, value in element.attrib.items():
            local = name.split("}", 1)[-1]
            if local.lower().startswith("on"):
                raise InvalidSchematic("Event handler attributes are not allowed in a schematic.")
            if name in HREF_ATTRIBUTES and not value.startswith("#"):
                raise InvalidSchematic("A schematic may only reference ids inside itself.")
            if local == "style" and re.search(r"url\s*\(", value, re.IGNORECASE):
                raise InvalidSchematic("Styles may not reference external resources.")
    return data


def write_file(directory: Path, sha256: str, data: bytes) -> str:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"{sha256}.svg"
    target = directory / name
    if not target.exists():
        target.write_bytes(data)
        target.chmod(0o600)
    return name


def save_schematic(
    connection: sqlite3.Connection, *, point_id: str, title: str, svg: str, directory: Path
) -> Schematic:
    data = validate_svg(svg)
    sha256 = hashlib.sha256(data).hexdigest()
    name = write_file(directory, sha256, data)
    schematic_id = new_id("sch")
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO schematics (id, learning_point_id, title, media_type, byte_size, sha256,"
            " stored_name, created_at) VALUES (?, ?, ?, 'image/svg+xml', ?, ?, ?, ?)",
            (schematic_id, point_id, title.strip()[:200], len(data), sha256, name, utc_now()),
        )
    return get_schematic(connection, schematic_id)


def get_schematic(connection: sqlite3.Connection, schematic_id: str) -> Schematic:
    row = connection.execute("SELECT * FROM schematics WHERE id = ?", (schematic_id,)).fetchone()
    if row is None:
        raise NotFoundError("schematic", schematic_id)
    return Schematic(**{key: row[key] for key in row.keys()})


def list_schematics(connection: sqlite3.Connection, point_id: str) -> list[Schematic]:
    rows = connection.execute(
        "SELECT * FROM schematics WHERE learning_point_id = ? ORDER BY created_at, id", (point_id,)
    ).fetchall()
    return [Schematic(**{key: row[key] for key in row.keys()}) for row in rows]


def safe_file_title(title: str) -> str:
    """A file name a person can read, from a title a model wrote."""
    cleaned = re.sub(r"[^A-Za-z0-9 _.-]+", " ", title).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return (cleaned or "schematic")[:80]
