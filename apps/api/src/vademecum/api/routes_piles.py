"""Piles and their learning items."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Query, Response, status

from ..storage import piles as store
from . import schemas
from .deps import get_connection

router = APIRouter(tags=["piles"])


@router.get("/piles", response_model=list[schemas.Pile])
def list_piles(
    tier: schemas.Tier | None = Query(default=None),
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    return [pile.as_dict() for pile in store.list_piles(connection, tier=tier)]


@router.post("/piles", response_model=schemas.Pile, status_code=status.HTTP_201_CREATED)
def create_pile(
    payload: schemas.PileCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.create_pile(
        connection,
        title=payload.title,
        tier=payload.tier,
        description=payload.description,
    ).as_dict()


@router.get("/piles/{pile_id}", response_model=schemas.Pile)
def get_pile(
    pile_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    return store.get_pile(connection, pile_id).as_dict()


@router.patch("/piles/{pile_id}", response_model=schemas.Pile)
def update_pile(
    pile_id: str,
    payload: schemas.PileUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.update_pile(
        connection,
        pile_id,
        title=payload.title,
        tier=payload.tier,
        description=payload.description,
    ).as_dict()


@router.delete("/piles/{pile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_pile(
    pile_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Response:
    store.delete_pile(connection, pile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/piles/{pile_id}/items", response_model=list[schemas.LearningItem])
def list_items(
    pile_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    return [item.as_dict() for item in store.list_items(connection, pile_id)]


@router.post(
    "/piles/{pile_id}/items",
    response_model=schemas.LearningItem,
    status_code=status.HTTP_201_CREATED,
)
def create_item(
    pile_id: str,
    payload: schemas.ItemCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.create_item(
        connection,
        pile_id=pile_id,
        title=payload.title,
        body=payload.body,
        source=payload.source,
    ).as_dict()


@router.get("/items/{item_id}", response_model=schemas.LearningItem)
def get_item(
    item_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    return store.get_item(connection, item_id).as_dict()


@router.patch("/items/{item_id}", response_model=schemas.LearningItem)
def update_item(
    item_id: str,
    payload: schemas.ItemUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.update_item(
        connection,
        item_id,
        title=payload.title,
        body=payload.body,
        source=payload.source,
        pile_id=payload.pile_id,
    ).as_dict()


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(
    item_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Response:
    store.delete_item(connection, item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
