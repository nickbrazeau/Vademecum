"""The Case Series hub (ADR 0022): list it, keep it updated, refresh it now.

What leaves the machine for this page is fixed: the same public requests to
three publishers every time, with nothing of the owner's in them, and -- for
the teaching points -- each case's public title and show notes, once, to the
Mac's own model connection. The disclosure below says so beside the switch.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import Field

from ..model.case_hub import NOT_HERE, CaseHub
from ..storage import cases as store
from ..storage.sources import ConflictError
from .deps import get_connection
from .schemas import Strict

router = APIRouter(prefix="/cases", tags=["cases"])

DISCLOSURE = (
    "Keeping the hub updated sends fixed public requests, with nothing of yours in them, to PubMed "
    "(for the two NEJM series), clinicalproblemsolving.com and thecurbsiders.com. Writing teaching "
    "points sends each case's public title and show notes, once, to the Mac's own model connection "
    "(Codex or Claude, on your sign-in; no API key). None of your material, notes, flags or answers "
    "is included in either."
)

CREDIT = (
    "Every case here is the work of its authors, hosts and publishers. Vademecum keeps a title, a "
    "link, who made it and a short public summary, and writes its own study notes beside them. Read, "
    "listen to and cite the original."
)

MAX_QUERY_CHARS = 100


class CaseSettingsIn(Strict):
    enabled: bool
    interval_hours: Annotated[float, Field(ge=store.MIN_INTERVAL_HOURS, le=store.MAX_INTERVAL_HOURS)] = store.DEFAULT_INTERVAL_HOURS
    series: dict[str, bool] | None = None


def get_hub(request: Request) -> CaseHub | None:
    return getattr(request.app.state, "case_hub", None)


def _settings_payload(hub: CaseHub | None, connection: sqlite3.Connection) -> dict[str, Any]:
    if hub is None:
        described: dict[str, Any] = {
            **store.get_settings(connection),
            "fetches_here": False,
            "can_synthesise": False,
            "running": False,
            "last_refresh": store.get_last_refresh(connection),
            "counts": store.counts(connection),
            "catalogue": [dict(entry) for entry in store.CATALOGUE],
            "note": NOT_HERE,
        }
    else:
        described = hub.describe(connection)
    return {**described, "disclosure": DISCLOSURE, "credit": CREDIT}


@router.get("")
def list_cases(
    series: str | None = None,
    specialty: str | None = None,
    q: str | None = None,
    limit: int = 100,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    if series and series not in store.SERIES_IDS:
        raise ConflictError("unknown_series", "That is not one of the series the hub follows.")
    query = (q or "").strip()[:MAX_QUERY_CHARS]
    entries = store.list_entries(
        connection,
        series=series or None,
        specialty=(specialty or "").strip() or None,
        q=query or None,
        limit=max(1, min(int(limit), 300)),
    )
    return {
        "entries": [entry.as_dict() for entry in entries],
        "catalogue": [dict(entry) for entry in store.CATALOGUE],
        "counts": store.counts(connection),
        "credit": CREDIT,
    }


@router.get("/settings")
def read_settings(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    return _settings_payload(get_hub(request), connection)


@router.put("/settings")
def write_settings(
    payload: CaseSettingsIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict[str, Any]:
    """Turn the hub on or off. Opt-in: it is off until chosen, and only Domi fetches."""
    store.set_settings(connection, enabled=payload.enabled, interval_hours=payload.interval_hours, series=payload.series)
    hub = get_hub(request)
    if hub is not None:
        hub.reschedule()
    return _settings_payload(hub, connection)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_now(request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Fetch every enabled series now and write notes for what is new, in the background."""
    hub = get_hub(request)
    if hub is None or not hub.fetches_here:
        raise ConflictError("not_here", NOT_HERE)
    if not hub.refresh_now():
        raise ConflictError("refresh_in_progress", "A refresh is already in progress.")
    return {"started": True, **_settings_payload(hub, connection)}
