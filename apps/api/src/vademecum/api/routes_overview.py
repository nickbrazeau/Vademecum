"""The Today cover sheet and the Improvement Map."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

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
