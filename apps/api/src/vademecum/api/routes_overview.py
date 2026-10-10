"""The Today cover sheet and the Improvement Map."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, Request

from . import schemas
from ..storage import map as map_store
from ..storage import overview as store
from .deps import get_connection, get_model_mode

router = APIRouter(tags=["overview"])


@router.get("/today")
def today(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    return store.cover_sheet(connection)


FILED_ON_THE_MAC = (
    "This is the copy of Vademecum that runs in the cloud for your phone. Flags are filed under "
    "topics by your Mac's own model connection, and the topics come back here at the next sync."
)


@router.get("/improvement-map")
def improvement_map(connection: sqlite3.Connection = Depends(get_connection), mode: str = Depends(get_model_mode)) -> dict:
    data = store.improvement_map(connection)
    data["can_file_flags"] = mode in ("codex", "claude")
    data["filing_note"] = "" if data["can_file_flags"] else FILED_ON_THE_MAC
    return data


@router.get("/learner")
def learner_model(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    """The learner model (ADR 0031): each topic's estimate, and a plan for the next few minutes."""
    from ..storage import learner

    return learner.model(connection)


@router.get("/improvement-map/strengths")
def strengths(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    """Strong and weak, topic by topic, with the reasons and the evidence (ADR 0026)."""
    from ..storage import strengths as strengths_store

    return strengths_store.strengths(connection)


@router.put("/improvement-map/topics/specialty")
def set_topic_specialty(
    payload: schemas.TopicSpecialtyUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Record which specialty a topic belongs to. The owner's decision, stored as such."""
    entry = map_store.set_topic_specialty(connection, payload.topic, payload.specialty_id)
    return {"topic": payload.topic.strip(), "specialty": None if entry is None else entry.as_dict()}


@router.put("/improvement-map/positions")
def save_map_positions(
    payload: schemas.MapPositionsUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Remember where every node sat, so the next open starts from the same picture."""
    count = map_store.save_positions(
        connection, [(entry.topic, entry.x, entry.y) for entry in payload.positions]
    )
    return {"saved": count}



@router.get("/construction")
def construction(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """What is waiting in the source folder, what is read in, and how far each source is
    built (feedback of 6 October). Names files and piles, never a path."""
    from ..storage import construction as progress

    import json as _json

    folder = getattr(request.app.state, "sources_folder", None)
    # The folder scan's record of each file's contents, so a copy under another name is known
    # without reading it again; and what the background builder is doing (feedback of 10 October).
    cache = None
    cache_path = request.app.state.settings.resolve_data_dir() / "scan-cache.json"
    try:
        cache = _json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else None
    except (OSError, ValueError):
        cache = None
    scheduler = getattr(request.app.state, "build_scheduler", None)
    builder = scheduler.describe(connection) if scheduler is not None else None
    data = progress.progress(connection, folder, cache=cache, builder=builder)
    data["folder"]["scanning"] = bool(getattr(request.app.state, "scanning", False))
    return data
