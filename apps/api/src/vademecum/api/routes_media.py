"""Pictures kept beside a source, and schematics drawn for a point (ADR 0013).

Both are served as files from the records directory, never by path: the
route resolves a stored name inside a known directory and refuses anything
else. A schematic saved here is also written into the learner's source folder
when there is one, so it can be opened in Finder beside the material it
explains; the reply says whether that happened, not where.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse

from ..storage import images as image_store
from ..storage import learning
from ..storage import piles as pile_store
from ..storage import schematics as schematic_store
from ..storage import sources as source_store
from ..storage.common import NotFoundError
from . import schemas
from .deps import get_connection, get_source_dir

router = APIRouter(tags=["media"])


def _served(directory: Path, stored_name: str) -> Path:
    """A file inside *directory*, or 404. The name came from a row, not a request."""
    if not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg|svg)", stored_name):
        raise NotFoundError("file", stored_name)
    candidate = (directory / stored_name).resolve()
    if candidate.parent != directory.resolve() or not candidate.is_file():
        raise NotFoundError("file", stored_name)
    return candidate


@router.get("/sources/{source_id}/images")
def list_source_images(
    source_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    source_store.get_source(connection, source_id)
    return [image.as_dict() for image in image_store.list_images(connection, source_id)]


@router.get("/images/{image_id}")
def get_image(
    image_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> FileResponse:
    try:
        image = image_store.get_image(connection, image_id)
    except NotFoundError:
        # The phone's copy has no picture records, only the figures its pages place,
        # sent by the Mac (feedback of 6 October).
        from ..storage import figure_copies

        found = figure_copies.path_for(figure_copies.figures_dir(source_dir), image_id)
        if found is None:
            raise
        return FileResponse(found[0], media_type=found[1], headers={"Cache-Control": "no-store"})
    path = _served(source_dir.parent / "images", image.stored_name)
    return FileResponse(path, media_type=image.media_type, headers={"Cache-Control": "no-store"})


@router.get("/points/{point_id}/schematics")
def list_point_schematics(
    point_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    learning.get_point(connection, point_id)
    return [item.as_dict() for item in schematic_store.list_schematics(connection, point_id)]


@router.post("/points/{point_id}/schematics", status_code=201)
def save_point_schematic(
    point_id: str,
    payload: schemas.SchematicCreate,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    point = learning.get_point(connection, point_id)
    schematic = schematic_store.save_schematic(
        connection,
        point_id=point_id,
        title=payload.title,
        svg=payload.svg,
        directory=source_dir.parent / "schematics",
    )
    saved_to_folder = False
    folder = getattr(request.app.state, "sources_folder", None)
    if folder is not None:
        try:
            pile = pile_store.get_pile(connection, point.pile_id)
            target_dir = folder / "schematics" / schematic_store.safe_file_title(pile.title)
            target_dir.mkdir(parents=True, exist_ok=True)
            name = f"{schematic_store.safe_file_title(schematic.title)} ({schematic.id[-6:]}).svg"
            (target_dir / name).write_bytes((source_dir.parent / "schematics" / schematic.stored_name).read_bytes())
            saved_to_folder = True
        except OSError:
            saved_to_folder = False
    data = schematic.as_dict()
    data["saved_to_folder"] = saved_to_folder
    data["support"] = point.support
    data["support_label"] = learning.SUPPORT_LABEL.get(point.support, point.support)
    return data


@router.get("/schematics/{schematic_id}")
def get_schematic(
    schematic_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> FileResponse:
    schematic = schematic_store.get_schematic(connection, schematic_id)
    path = _served(source_dir.parent / "schematics", schematic.stored_name)
    return FileResponse(path, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})
