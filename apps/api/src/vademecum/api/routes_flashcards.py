"""Flashcards and preferences (ADR 0024). Both local: nothing here starts a model turn."""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from fastapi import APIRouter, Depends

from ..storage import flashcards as store
from ..storage import preferences
from .deps import get_connection
from .schemas import RecordId, Strict

router = APIRouter(prefix="/flashcards", tags=["flashcards"])
preferences_router = APIRouter(prefix="/preferences", tags=["preferences"])
activity_router = APIRouter(prefix="/activity", tags=["activity"])


class PageReviewIn(Strict):
    entry_id: RecordId


class ReviewIn(Strict):
    card_id: RecordId
    rating: Literal["again", "good"]


class PreferencesIn(Strict):
    visible_tabs: list[str] | None = None
    # The owner's order of the tabs between Today and Settings (ADR 0026).
    order: list[str] | None = None
    daily_goal: int | None = None


@router.get("")
def flashcards_overview(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return store.overview(connection)


@router.get("/next")
def next_card(not_id: str | None = None, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """A weighted draw, not a queue: the map's gaps come up more often. Nothing is due."""
    drawn = store.next_card(connection, not_id=not_id)
    if drawn is None:
        return {"card": None, "reasons": [], "citations": [], "deck": 0, "empty_reason": store.NO_CARDS}
    return {**drawn, "empty_reason": ""}


@router.post("/review")
def review(payload: ReviewIn, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Record how it went and draw the next card. Local; nothing leaves this machine."""
    recorded = store.record_review(connection, payload.card_id, payload.rating)
    drawn = store.next_card(connection, not_id=payload.card_id)
    return {"review": recorded, "next": {**drawn, "empty_reason": ""} if drawn else {"card": None, "reasons": [], "citations": [], "deck": 0, "empty_reason": store.NO_CARDS}}


@preferences_router.get("")
def read_preferences(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return preferences.get_preferences(connection)


@preferences_router.put("")
def write_preferences(payload: PreferencesIn, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    if payload.daily_goal is not None:
        preferences.set_daily_goal(connection, payload.daily_goal)
    if payload.visible_tabs is None and payload.order is None:
        return preferences.get_preferences(connection)
    order = None if payload.order is None else [name[:40] for name in payload.order[:40]]
    visible = payload.visible_tabs if payload.visible_tabs is not None else preferences.get_preferences(connection)["visible_tabs"]
    return preferences.set_visible_tabs(connection, [name[:40] for name in visible[:40]], order)


@activity_router.get("")
def activity_dashboard(connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """What the owner reviewed, day by day (ADR 0026). Counts what happened; nothing is due."""
    from ..storage import activity

    return activity.dashboard(connection)


@activity_router.post("/page")
def page_reviewed(payload: PageReviewIn, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    from ..storage import activity

    activity.record(connection, "page", payload.entry_id)
    return activity.dashboard(connection)
