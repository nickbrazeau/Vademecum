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
            "catalogue": store.catalogue(connection),
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
    if series and series not in store.series_ids(connection):
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
        "catalogue": store.catalogue(connection),
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


@router.post("/{entry_id}/acknowledge")
def acknowledge(entry_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Seen on Today: it leaves the list of new cases and stays in the hub."""
    store.acknowledge(connection, entry_id)
    return {"acknowledged": True}


# --- feeds the owner adds (feedback of 10 October) -----------------------------------


class FeedIn(Strict):
    url: Annotated[str, Field(min_length=9, max_length=500)]
    # False: only say which host would be contacted. True: the owner said yes.
    confirm: bool = False


@router.post("/feeds")
async def add_feed(payload: FeedIn, request: Request, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Add a feed of teaching cases. First, with confirm false, nothing is contacted: the
    reply names the one host this feed would add. With confirm true, the feed is read once
    from that host alone, kept if it is a feed, and refreshed with the others from then on."""
    import asyncio

    from ..literature.cases import feed_parts, fetch_feed
    from ..literature.http import HttpsFetcher, ProviderError

    try:
        host, _path, _query = feed_parts(payload.url)
    except ValueError as exc:
        raise ConflictError("feed_address", str(exc)) from None
    if not payload.confirm:
        return {
            "url": payload.url.strip(),
            "host": host,
            "ask": f"Vademecum would contact {host} to read this feed, now and at each refresh, and nothing else of yours goes with it. Add it?",
        }
    hub = get_hub(request)
    if hub is None or not hub.fetches_here:
        raise ConflictError("on_the_mac", "Feeds are added on the Mac, which fetches the case series.")
    settings = request.app.state.settings
    fetcher = HttpsFetcher(host, timeout=settings.literature_request_timeout, contact_email=settings.literature_contact_email, owner_confirmed=frozenset({host}))
    try:
        title, items = await asyncio.to_thread(fetch_feed, fetcher, payload.url.strip(), "feed_pending")
    except ProviderError as exc:
        raise ConflictError("not_a_feed", f"That address could not be read as a feed ({exc.category}).") from None
    except Exception:  # noqa: BLE001 - anything else about the reply means it is not a feed we can read
        raise ConflictError("not_a_feed", "That address did not answer with an RSS or Atom feed.") from None
    if not items:
        raise ConflictError("not_a_feed", "That feed has no items with links to read.")
    try:
        feed = store.add_feed(connection, url=payload.url.strip(), host=host, title=title or host)
    except ValueError as exc:
        raise ConflictError("too_many_feeds", str(exc)) from None
    new = store.record_items(connection, [{**item.as_dict(), "series": feed["id"]} for item in items])
    hub.reschedule()
    return {"feed": feed, "new": new}


@router.delete("/feeds/{feed_id}")
def remove_feed(feed_id: str, connection: sqlite3.Connection = Depends(get_connection)) -> dict[str, Any]:
    """Stop following a feed; its host is no longer contacted. Cases already kept stay."""
    if not store.remove_feed(connection, feed_id):
        raise ConflictError("unknown_feed", "That feed is not one you added.")
    return {"removed": True}
