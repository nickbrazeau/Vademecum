"""The Today cover sheet and the Improvement Map."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from . import schemas
from ..storage import map as map_store
from ..storage import overview as store
from .deps import get_connection

router = APIRouter(tags=["overview"])


@router.get("/today")
def today(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    return store.cover_sheet(connection)


@router.get("/improvement-map")
def improvement_map(connection: sqlite3.Connection = Depends(get_connection)) -> dict:
    return store.improvement_map(connection)


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
