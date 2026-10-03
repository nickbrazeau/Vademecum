"""Reading an exam report into content areas (ADR 0020): one model turn, checked.

The report's text goes to the model inside the usual fence, with the list of
subspecialties it may name. What comes back is kept only where the quote is
really in the report and the standing is one of the three; an area without a
true quote is dropped, not repaired. Runs on the Mac's own connection (codex
or claude mode); in host mode the pipeline's pending-turn registry is used
when the caller hands one in, otherwise the report waits.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import map as map_store
from ..storage import reports as store
from ..storage.learning import quote_in
from . import prompts, schemas

logger = logging.getLogger("vademecum.reports")

WAITING = "Waiting for the Mac's own model connection (codex or claude mode) to read it."


def _runner(turn_factory: Any, report_id: str):
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped("report", report_id)()
    return turn_factory()


def check_areas(payload: dict[str, Any], text: str, specialty_ids: set[str]) -> list[dict[str, Any]]:
    """What the server keeps of the model's answer: quoted, placed, bounded."""
    kept: list[dict[str, Any]] = []
    for area in payload.get("areas") or []:
        if not isinstance(area, dict):
            continue
        quote = str(area.get("quote") or "")
        topic = str(area.get("topic") or "").strip()
        if not topic or not quote_in(text, quote):
            continue
        specialty = str(area.get("specialty") or "").strip()
        kept.append(
            {
                "topic": topic,
                "specialty": specialty if specialty in specialty_ids else "",
                "standing": area.get("standing"),
                "quote": quote,
                "note": str(area.get("note") or ""),
            }
        )
    return kept[: schemas.MAX_AREAS_PER_REPORT]


async def parse_report(database_path: Path, report_id: str, turn_factory: Any) -> dict[str, Any]:
    """Read one report. Returns the report as a dict, parsed or failed."""
    connection = connect(database_path)
    try:
        text = store.report_text(connection, report_id)
        specialties = [(entry.id, entry.name) for entry in map_store.list_specialties(connection)]
    finally:
        connection.close()
    ids = {identifier for identifier, _ in specialties}
    try:
        runner = _runner(turn_factory, report_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.REPORT_DEVELOPER,
            prompt=prompts.report_prompt(text, specialties),
            output_schema=schemas.REPORT_SCHEMA,
        )
        areas = check_areas(reply.payload, text, ids)
        connection = connect(database_path)
        try:
            return store.set_parsed(connection, report_id, areas).as_dict()
        finally:
            connection.close()
    except BridgeError as exc:
        logger.info("report_parse_failed category=%s", exc.category)
        connection = connect(database_path)
        try:
            return store.set_failed(connection, report_id, f"The model connection failed ({exc.category}). Try again from the map.").as_dict()
        finally:
            connection.close()
    except Exception as exc:  # noqa: BLE001 - a failure is recorded on the report, never lost
        logger.error("report_parse_error error=%s", type(exc).__name__)
        connection = connect(database_path)
        try:
            return store.set_failed(connection, report_id, "Reading this report failed. Try again from the map.").as_dict()
        finally:
            connection.close()


async def parse_pending(database_path: Path, turn_factory: Any) -> int:
    """Every uploaded, unread report, one at a time. Returns how many were read."""
    connection = connect(database_path)
    try:
        pending = store.unparsed_report_ids(connection)
    finally:
        connection.close()
    done = 0
    for report_id in pending:
        result = await parse_report(database_path, report_id, turn_factory)
        if result.get("status") == "parsed":
            done += 1
    return done


def parser_for(database_path: Path, turn_factory: Any) -> Callable[[], Any]:
    async def run() -> int:
        return await parse_pending(database_path, turn_factory)

    return run
