"""Knowledge Gap Flags.

Capture is one POST with one required field. Nothing here asks the owner to
categorise, rate, or schedule anything.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query, Response, status

from ..storage import flags as store
from . import schemas
from .deps import get_connection

router = APIRouter(tags=["flags"])


@router.get("/flags", response_model=list[schemas.Flag])
def list_flags(
    status_filter: schemas.FlagStatus | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1, le=500),
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    return [
        flag.as_dict()
        for flag in store.list_flags(connection, status=status_filter, limit=limit)
    ]


@router.post("/flags", response_model=schemas.Flag, status_code=status.HTTP_201_CREATED)
def create_flag(
    payload: schemas.FlagCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.create_flag(
        connection, text=payload.text, topic=payload.topic, pile_id=payload.pile_id
    ).as_dict()


@router.get("/flags/{flag_id}", response_model=schemas.Flag)
def get_flag(
    flag_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    return store.get_flag(connection, flag_id).as_dict()


@router.patch("/flags/{flag_id}", response_model=schemas.Flag)
def update_flag(
    flag_id: str,
    payload: schemas.FlagUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.update_flag(
        connection,
        flag_id,
        text=payload.text,
        topic=payload.topic,
        status=payload.status,
        pile_id=payload.pile_id,
    ).as_dict()


@router.delete("/flags/{flag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_flag(
    flag_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Response:
    store.delete_flag(connection, flag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
