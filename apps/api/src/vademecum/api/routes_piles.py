"""Piles and their learning items."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response, status

from ..storage import piles as store
from . import schemas
from .deps import get_connection, get_source_dir

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
    pile_id: str,
    request: Request,
    everything: bool = False,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> Response:
    """An empty pile goes at once. With ``everything``, a pile is deleted with its files,
    points and questions (feedback of 11 October): on the Mac only, whose copy they are;
    its folder in the source folder is set aside, hidden, so the next scan does not
    bring it back, and nothing is lost that the owner cannot recover from there."""
    settings = request.app.state.settings
    if everything and settings.sync_role_name == "foris":
        raise store.ConflictError("on_the_mac", "A pile with files in it is deleted on Source, where its files are.")
    pile = store.get_pile(connection, pile_id)
    originals = store.delete_pile(connection, pile_id, everything=everything)
    if everything:
        _set_folder_aside(settings, pile.title)
        from ..storage import sources as source_store

        for stored_name, sha256 in originals:
            source_store.remove_original(source_dir, stored_name, sha256)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _set_folder_aside(settings, title: str) -> None:
    """The pile's folder, in whichever confidence folder it is, renamed with a leading dot."""
    import time

    from ..app import current_sources_dir
    from ..ingest.folder import PILES_DIRNAME, TIER_FOLDERS

    folder = current_sources_dir(settings) if settings.tenancy == "single" else None
    if folder is None:
        return
    for tier_folder in TIER_FOLDERS.values():
        place = folder / PILES_DIRNAME / tier_folder / title
        if place.is_dir():
            place.rename(place.with_name(f".deleted {title} {time.strftime('%Y-%m-%d %H%M')}"))


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
