"""The build schedule (ADR 0018): read it, set it, run it now.

Setting it to enabled is the owner's standing consent to what each run
sends; the reply carries that disclosure, and the dashboard shows it before
the switch. Running now starts in the background and the reply says so;
`GET` reports progress and the last run.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from ..model.schedule import BuildScheduler
from ..storage import schedule as store
from ..storage.sources import ConflictError
from .deps import get_connection
from .routes_sources import CODEX_DESTINATION

router = APIRouter(prefix="/build/schedule", tags=["schedule"])

STANDING_CONSENT = (
    "With the schedule on, each run sends the next unbuilt excerpts of every pile -- their text, "
    "filenames, confidence labels and locations -- and the follow-up checks' claims, questions and "
    "retrieved abstracts, without a further prompt, until you turn it off. "
    + CODEX_DESTINATION
    + " Short public topic words still go to PubMed."
)


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    times: Annotated[list[str], Field(min_length=1, max_length=store.MAX_TIMES)]
    batches_per_run: Annotated[int, Field(ge=1, le=store.MAX_BATCHES_PER_RUN)] = 3


def get_scheduler(request: Request) -> BuildScheduler:
    scheduler = getattr(request.app.state, "build_scheduler", None)
    if scheduler is None:
        raise ConflictError("no_schedule", "This Vademecum has no build schedule; it is one owner's Mac that has one.")
    return scheduler


@router.get("")
def read_schedule(
    connection: sqlite3.Connection = Depends(get_connection),
    scheduler: BuildScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    return {**scheduler.describe(connection), "disclosure": STANDING_CONSENT}


@router.put("")
def write_schedule(
    payload: ScheduleIn,
    connection: sqlite3.Connection = Depends(get_connection),
    scheduler: BuildScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    try:
        store.set_schedule(connection, enabled=payload.enabled, times=payload.times, batches_per_run=payload.batches_per_run)
    except store.InvalidSchedule as exc:
        raise ConflictError("invalid_schedule", exc.message) from None
    scheduler.reschedule()
    return {**scheduler.describe(connection), "disclosure": STANDING_CONSENT}


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    scheduler: BuildScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    """Build now, every pile, in the background. Consent is the press of this button."""
    if not scheduler.can_run:
        raise ConflictError("needs_codex", scheduler.describe(connection)["blocked_reason"])
    if scheduler.is_running:
        raise ConflictError("run_in_progress", "A scheduled run is already in progress.")
    task = asyncio.create_task(scheduler.run_all(reason="requested"))
    request.app.state.build_schedule_task = task
    return {"started": True, **scheduler.describe(connection)}
