"""Teaching points for a case (ADR 0022): one model turn, checked against the public text.

A case's title and the publisher's public show notes go to the Mac's own
model connection inside the usual fence. What comes back is a one-line
summary, teaching points each resting on a verbatim quote, think-first
prompts (the questions the presentation invites, before the answer), a
subspecialty from the list, and the people the notes name. The server keeps a
teaching point only when its quote is really in the text, so a title alone --
the NEJM series, which PubMed carries no abstract for -- yields prompts and
no points. Runs on codex or claude mode; in host mode entries wait and the
hub says so.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import cases as store
from ..storage import map as map_store
from ..storage.learning import quote_in
from . import prompts, schemas

logger = logging.getLogger("vademecum.cases")

WAITING = (
    "Teaching points are written on the Mac's own model connection (codex or claude mode). "
    "Until then each case shows its title, who made it and the link to the original."
)
MAX_SYNTHESES_PER_REFRESH = 40


def _runner(turn_factory: Any, entry_id: str):
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped("case", entry_id)()
    return turn_factory()


def _names_in(credit: str, text: str) -> str:
    """Only the names the notes really contain; a name the model added is dropped."""
    haystack = text.casefold()
    kept: list[str] = []
    for part in credit.replace(" and ", ", ").split(","):
        name = " ".join(part.split())
        if len(name) >= 3 and name.casefold() in haystack and name not in kept:
            kept.append(name)
    return ", ".join(kept)[:300]


_PLAIN = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def _plain(value: str) -> str:
    """Curly quotes and dashes straightened on both sides: show notes mix them, and a
    quote that differs only in the shape of an apostrophe is the same words."""
    return value.translate(_PLAIN)


def quoted_in(text: str, quote: str) -> bool:
    return quote_in(_plain(text), _plain(quote))


# A note about the material is not a note about the case. A sentence that says what
# the title or the show notes do not tell is dropped, wherever the model put it.
_ABOUT_THE_MATERIAL = re.compile(
    r"(title alone|does not (establish|describe|disclose|specify|state|mention|indicate)"
    r"|not (described|disclosed|provided|stated|specified|given|detailed) in"
    r"|the (supplied|provided|available) (material|summary|notes|text)"
    r"|the (summary|notes|title|material) (does|do) not|no further (clinical )?(details|information)"
    r"|without (further|additional) (details|information)|not disclosed|is not described)",
    re.IGNORECASE,
)


def about_the_case(sentence: str) -> bool:
    return bool(sentence.strip()) and _ABOUT_THE_MATERIAL.search(sentence) is None


def check_synthesis(payload: dict[str, Any], *, text: str, specialty_ids: set[str]) -> dict[str, Any]:
    """What the server keeps of the model's answer: quoted points, bounded prompts, a listed specialty."""
    points: list[dict[str, str]] = []
    offered = 0
    for item in payload.get("teaching_points") or []:
        if not isinstance(item, dict):
            continue
        offered += 1
        point = " ".join(str(item.get("point") or "").split())
        quote = str(item.get("quote") or "")
        if point and text and quoted_in(text, quote) and about_the_case(point):
            points.append({"point": point, "quote": quote})
    if offered:
        # Counts only: how many points the model offered and how many quoted the text.
        logger.info("case_points offered=%d kept=%d text_chars=%d", offered, len(points), len(text))
    prompts_kept = [
        " ".join(str(item).split())
        for item in (payload.get("think_first") or [])
        if isinstance(item, str) and about_the_case(str(item))
    ]
    specialty = str(payload.get("specialty") or "").strip()
    one_liner = " ".join(str(payload.get("one_liner") or "").split())
    return {
        "one_liner": one_liner if about_the_case(one_liner) else "",
        "points": points[: store.MAX_POINTS],
        "think_first": prompts_kept[: store.MAX_THINK_FIRST],
        "specialty_id": specialty if specialty in specialty_ids else None,
        "credit": _names_in(str(payload.get("credit") or ""), text),
    }


async def synthesise_entry(database_path: Path, entry_id: str, turn_factory: Any) -> dict[str, Any]:
    """Write one case's study notes. Returns the entry as a dict, synthesised or failed."""
    connection = connect(database_path)
    try:
        series, title, text = store.entry_material(connection, entry_id)
        series_name = next((entry["name"] for entry in store.CATALOGUE if entry["id"] == series), series)
        specialties = [(entry.id, entry.name) for entry in map_store.list_specialties(connection)]
    finally:
        connection.close()
    ids = {identifier for identifier, _ in specialties}
    try:
        runner = _runner(turn_factory, entry_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.CASE_DEVELOPER,
            prompt=prompts.case_prompt(series_name, title, text, specialties),
            output_schema=schemas.CASE_SCHEMA,
        )
        checked = check_synthesis(reply.payload, text=text, specialty_ids=ids)
        connection = connect(database_path)
        try:
            return store.set_synthesised(connection, entry_id, **checked).as_dict()
        finally:
            connection.close()
    except BridgeError as exc:
        logger.info("case_synthesis_failed category=%s", exc.category)
        detail = f"The model connection failed ({exc.category}). It will be tried again at the next refresh."
    except Exception as exc:  # noqa: BLE001 - recorded on the entry, never lost
        logger.error("case_synthesis_error error=%s", type(exc).__name__)
        detail = "Writing the teaching points failed. It will be tried again at the next refresh."
    connection = connect(database_path)
    try:
        return store.set_failed(connection, entry_id, detail).as_dict()
    finally:
        connection.close()


async def synthesise_pending(database_path: Path, turn_factory: Any, *, limit: int = MAX_SYNTHESES_PER_REFRESH) -> dict[str, int]:
    """Every entry still without notes, newest first, one turn each. Returns the tally."""
    connection = connect(database_path)
    try:
        pending = store.pending_ids(connection, limit=limit)
    finally:
        connection.close()
    done = 0
    failed = 0
    for entry_id in pending:
        result = await synthesise_entry(database_path, entry_id, turn_factory)
        if result.get("status") == "synthesised":
            done += 1
        else:
            failed += 1
    return {"done": done, "failed": failed}
