"""The public-literature watch: topics, checks, updates and the weekly schedule.

Only short public topic strings reach the provider. No passage, note, learning
point or answer is ever put into a search -- the validation that guarantees it
lives in ``literature/pubmed.py`` and is applied again there.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Request, Response, status

from ..storage import learning
from ..storage import literature as store
from . import schemas
from ..tenancy import Workspace
from .deps import get_connection, get_watcher, get_workspace

router = APIRouter(prefix="/literature", tags=["literature"])

PROVIDER = "PubMed (NCBI E-utilities)"


@router.get("/topics")
def list_topics(connection: sqlite3.Connection = Depends(get_connection)) -> list[dict]:
    return [topic.as_dict() for topic in store.list_topics(connection)]


@router.post("/topics", status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: schemas.LiteratureTopicCreate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.create_topic(
        connection, label=payload.label, query=payload.query
    ).as_dict()


@router.patch("/topics/{topic_id}")
def update_topic(
    topic_id: str,
    payload: schemas.LiteratureTopicUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.update_topic(
        connection,
        topic_id,
        label=payload.label,
        query=payload.query,
        enabled=payload.enabled,
    ).as_dict()


@router.delete("/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic(
    topic_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> Response:
    store.delete_topic(connection, topic_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/check")
async def check_now(
    payload: schemas.LiteratureCheckRequest,
    workspace: Workspace = Depends(get_workspace),
) -> dict:
    """Run a check now. Explicit, and the only manual trigger."""
    watcher = workspace.watcher
    if watcher is None:
        return {
            "checks": [],
            "summary": {},
            "message": (
                "The literature watch is switched off in this installation, so "
                "nothing was requested."
            ),
        }
    return await watcher.check_now(payload.topic_id)


@router.get("/suggestions")
def suggestions(
    limit: int = 12, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    """Topics worth watching, taken from what builds have already produced.

    No model call and no provider call: these are tags that already exist in the
    bank. Creating a watch from one is a separate explicit action, and it does
    not switch on the weekly schedule.
    """
    return {
        "suggestions": learning.topic_suggestions(
            connection, limit=max(1, min(limit, 40))
        ),
        "note": (
            "Adding a topic does not switch on weekly checking. Weekly checks are "
            "opt-in and only run while Vademecum is running on this Mac."
        ),
    }


@router.get("/updates")
def list_updates(
    state: str | None = None,
    limit: int = 50,
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    return [
        update.as_dict()
        for update in store.list_updates(
            connection, state=state, limit=max(1, min(limit, 200))
        )
    ]


@router.patch("/updates/{update_id}")
def set_update_state(
    update_id: str,
    payload: schemas.UpdateState,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    return store.set_update_state(connection, update_id, payload.state).as_dict()


@router.get("/settings")
def get_settings(
    request: Request, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    watcher = get_watcher(request)
    settings = store.get_settings(connection)
    return {
        **settings,
        "enabled": watcher is not None,
        "running": bool(watcher and watcher.running),
        "provider": PROVIDER,
        "unread": store.unread_count(connection),
    }


@router.put("/settings")
def set_settings(
    payload: schemas.LiteratureSettings,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """Turn the weekly check on or off. Opt-in: it is off until chosen.

    Nothing outside this process is scheduled. The loop runs while Vademecum is
    running and stops with it.
    """
    store.set_settings(
        connection,
        weekly_enabled=payload.weekly_enabled,
        interval_hours=payload.interval_hours,
        preferred_journals=payload.preferred_journals,
        guidelines_first=payload.guidelines_first,
    )
    watcher = get_watcher(request)
    apply = getattr(watcher, "apply_settings", None)
    if apply is not None:
        apply(
            weekly_enabled=payload.weekly_enabled, interval_hours=payload.interval_hours
        )
    return get_settings(request, connection)
