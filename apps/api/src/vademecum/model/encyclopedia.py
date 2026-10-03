"""Compiling pages and writing board questions (ADR 0023): two checked model turns.

A page is compiled from one topic's learning points, handed to the model as
numbered handles; what comes back is kept only where every paragraph maps
its handles to real points, so nothing on a page is unsourced. A board
question is written from a page; the correct answer must cite the page's
points, the five options must be distinct, and a stem that leans on "the
text" is held. Both run on the Mac's own model connection (codex or claude
mode), after scheduled builds and on "Compile now".
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import encyclopedia as store
from ..storage import flags as flag_store
from ..storage import map as map_store
from ..storage.common import utc_now
from ..storage.learning import SUPPORT_LABEL, points_for_topic
from . import prompts, schemas

logger = logging.getLogger("vademecum.encyclopedia")

WAITING = (
    "Pages and board questions are written on the Mac's own model connection (codex or claude "
    "mode). This Vademecum shows what has been compiled."
)
MAX_COMPILES_PER_RUN = 12
MAX_GENERATIONS_PER_RUN = 12
MIN_QUESTIONS_PER_ENTRY = 4
MAX_CONTEXT_CASE_POINTS = 6
MAX_CONTEXT_FLAGS = 5

_LEANS_ON_TEXT = re.compile(
    r"\b(the (excerpt|passage|page|text|material|slide|source)s?\b|according to the (page|text|material))", re.IGNORECASE
)


def _runner(turn_factory: Any, kind: str, scope_id: str):
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped(kind, scope_id)()
    return turn_factory()


# --- pages ----------------------------------------------------------------------


def check_entry(payload: dict[str, Any], *, handles: dict[str, str], specialty_ids: set[str]) -> dict[str, Any] | None:
    """What the server keeps: paragraphs whose handles all map, sections that keep a paragraph."""
    sections: list[dict[str, Any]] = []
    cited: list[str] = []
    for section in (payload.get("sections") or [])[: schemas.MAX_ENTRY_SECTIONS]:
        if not isinstance(section, dict):
            continue
        paragraphs: list[dict[str, Any]] = []
        for paragraph in (section.get("paragraphs") or [])[: schemas.MAX_ENTRY_PARAGRAPHS]:
            if not isinstance(paragraph, dict):
                continue
            text = " ".join(str(paragraph.get("text") or "").split())
            ids = [handles[str(h)] for h in (paragraph.get("points") or []) if str(h) in handles]
            ids = list(dict.fromkeys(ids))
            if not text or not ids:
                continue
            paragraphs.append({"text": text, "point_ids": ids})
        heading = " ".join(str(section.get("heading") or "").split())
        if paragraphs and heading:
            sections.append({"heading": heading, "paragraphs": paragraphs})
            for paragraph in paragraphs:
                cited.extend(paragraph["point_ids"])
    if not sections:
        return None
    specialty = str(payload.get("specialty") or "").strip()
    return {
        "title": " ".join(str(payload.get("title") or "").split()),
        "summary": " ".join(str(payload.get("summary") or "").split()),
        "specialty_id": specialty if specialty in specialty_ids else None,
        "sections": sections,
        "cited": list(dict.fromkeys(cited)),
    }


async def compile_topic(database_path: Path, topic: str, turn_factory: Any) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        points = points_for_topic(connection, topic)
        specialties = [(entry.id, entry.name) for entry in map_store.list_specialties(connection)]
        assigned = map_store.topic_specialties(connection)
    finally:
        connection.close()
    if not points:
        return {"topic": topic, "status": "skipped", "detail": "No points."}
    handles = {f"p{index + 1}": point.id for index, point in enumerate(points)}
    listed = [
        (
            handle,
            point.claim,
            point.detail,
            SUPPORT_LABEL.get(point.support, point.support),
            "; ".join(f"{c.display_name} ({c.locator})" for c in point.citations[:3]) or "the pile",
        )
        for handle, point in zip(handles, points, strict=True)
    ]
    ids = {identifier for identifier, _ in specialties}
    try:
        runner = _runner(turn_factory, "entry", topic)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.ENTRY_DEVELOPER,
            prompt=prompts.entry_prompt(topic, listed, specialties),
            output_schema=schemas.ENTRY_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("entry_compile_failed category=%s", exc.category)
        connection = connect(database_path)
        try:
            store.mark_entry_failed(connection, topic, f"The model connection failed ({exc.category}); the previous page stands.")
        finally:
            connection.close()
        return {"topic": topic, "status": "failed", "detail": exc.category}
    checked = check_entry(reply.payload, handles=handles, specialty_ids=ids)
    if checked is None:
        logger.info("entry_compile_empty topic_points=%d", len(points))
        connection = connect(database_path)
        try:
            store.mark_entry_failed(connection, topic, "The page the model wrote cited none of the points, so it was not kept.")
        finally:
            connection.close()
        return {"topic": topic, "status": "failed", "detail": "unsourced"}
    owner_call = assigned.get(topic)
    specialty_id = owner_call.id if owner_call is not None else checked["specialty_id"]
    connection = connect(database_path)
    try:
        entry = store.upsert_entry(
            connection,
            topic=topic,
            title=checked["title"] or topic,
            specialty_id=specialty_id,
            summary=checked["summary"],
            sections=checked["sections"],
            point_ids=[point.id for point in points],
            points_hash_value=store.points_hash(points),
        )
        if specialty_id and map_store.topic_specialties(connection).get(topic) is None:
            map_store.set_topic_specialty(connection, topic, specialty_id)
    finally:
        connection.close()
    logger.info("entry_compiled points=%d cited=%d sections=%d", len(points), len(checked["cited"]), len(checked["sections"]))
    return {"topic": topic, "status": "compiled", "entry_id": entry.id, "version": entry.version}


async def compile_pending(database_path: Path, turn_factory: Any, *, limit: int = MAX_COMPILES_PER_RUN) -> dict[str, int]:
    connection = connect(database_path)
    try:
        topics = store.topics_to_compile(connection)[:limit]
    finally:
        connection.close()
    tally = {"compiled": 0, "failed": 0, "remaining": 0}
    for topic in topics:
        result = await compile_topic(database_path, topic, turn_factory)
        if result["status"] == "compiled":
            tally["compiled"] += 1
        elif result["status"] == "failed":
            tally["failed"] += 1
    connection = connect(database_path)
    try:
        tally["remaining"] = len(store.topics_to_compile(connection))
    finally:
        connection.close()
    return tally


# --- board questions -------------------------------------------------------------


def _page_text(entry: store.Entry, handles: dict[str, str]) -> str:
    by_id = {point_id: handle for handle, point_id in handles.items()}
    lines = [f"TITLE: {entry.title}", f"SUMMARY: {entry.summary}", ""]
    for section in entry.sections:
        lines.append(section["heading"].upper())
        for paragraph in section["paragraphs"]:
            cites = " ".join(f"[{by_id[p]}]" for p in paragraph["point_ids"] if p in by_id)
            lines.append(f"{paragraph['text']} {cites}".strip())
        lines.append("")
    return "\n".join(lines).strip()


def _points_text(points: list[dict[str, Any]], handles: dict[str, str]) -> str:
    by_id = {point_id: handle for handle, point_id in handles.items()}
    return "\n".join(f"[{by_id[p['id']]}] {p['claim']} (support: {p['support_label']})" for p in points if p["id"] in by_id)


def other_context(connection, entry: store.Entry) -> str:
    """Teaching points from the case series in the page's specialty, and the learner's own flags on the topic."""
    lines: list[str] = []
    if entry.specialty_id:
        rows = connection.execute(
            "SELECT title, points FROM case_entries WHERE specialty_id = ? AND status = 'synthesised' ORDER BY published_on DESC LIMIT 12",
            (entry.specialty_id,),
        ).fetchall()
        shown = 0
        for row in rows:
            for point in store._json_list(row["points"]):
                if isinstance(point, dict) and point.get("point") and shown < MAX_CONTEXT_CASE_POINTS:
                    lines.append(f"- Case series ({row['title'][:60]}): {point['point']}")
                    shown += 1
    flags = [f for f in flag_store.list_flags(connection, status="open") if (f.topic or "").strip().lower() == entry.topic.lower()][:MAX_CONTEXT_FLAGS]
    for flag in flags:
        lines.append(f"- The learner flagged: {flag.text[:200]}")
    return "\n".join(lines)


def check_board(payload: dict[str, Any], *, handles: dict[str, str]) -> list[dict[str, Any]]:
    """Structural checks; a question that fails a soft one is held with the reason, a hard one is dropped."""
    drafts: list[dict[str, Any]] = []
    for item in (payload.get("questions") or [])[: schemas.MAX_BOARD_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        stem = " ".join(str(item.get("stem") or "").split())
        options = [" ".join(str(o).split()) for o in (item.get("options") or [])]
        answer = str(item.get("answer") or "").strip().upper()
        explanation = " ".join(str(item.get("explanation") or "").split())
        if not stem or len(options) != store.OPTION_COUNT or any(not o for o in options) or answer not in store.LETTERS or not explanation:
            continue
        if len({o.lower() for o in options}) != store.OPTION_COUNT:
            continue
        point_ids = list(dict.fromkeys(handles[str(h)] for h in (item.get("points") or []) if str(h) in handles))
        hold = ""
        if not point_ids:
            hold = "The question cites none of the page's points, so its answer cannot be traced to a source."
        elif _LEANS_ON_TEXT.search(stem):
            hold = "The stem leans on 'the text' instead of standing on its own."
        elif any(re.search(r"\b(all|none) of the above\b", o, re.IGNORECASE) for o in options):
            hold = "An option is 'all' or 'none of the above'."
        drafts.append(
            {
                "stem": stem,
                "options": options,
                "answer_index": store.LETTERS.index(answer),
                "explanation": explanation,
                "objective": " ".join(str(item.get("objective") or "").split()),
                "point_ids": point_ids,
                "hold_reason": hold,
            }
        )
    return drafts


async def generate_for_entry(database_path: Path, entry_id: str, turn_factory: Any) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        entry = store.get_entry(connection, entry_id)
        cited = store.cited_points(connection, list(entry.point_ids))
        context = other_context(connection, entry)
    finally:
        connection.close()
    handles = {f"p{index + 1}": point_id for index, point_id in enumerate(entry.point_ids)}
    page = f"{_page_text(entry, handles)}\n\nTHE POINTS THE PAGE RESTS ON:\n{_points_text(cited, handles)}"
    try:
        runner = _runner(turn_factory, "board", entry_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.BOARD_DEVELOPER,
            prompt=prompts.board_prompt(page, context),
            output_schema=schemas.BOARD_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("board_generation_failed category=%s", exc.category)
        return {"entry_id": entry_id, "status": "failed", "written": 0, "held": 0}
    drafts = check_board(reply.payload, handles=handles)
    connection = connect(database_path)
    try:
        tally = store.insert_questions(connection, entry, drafts)
    finally:
        connection.close()
    logger.info("board_generated offered=%d written=%d held=%d", len(drafts), tally["written"], tally["held"])
    return {"entry_id": entry_id, "status": "generated", **tally}


async def generate_pending(database_path: Path, turn_factory: Any, *, limit: int = MAX_GENERATIONS_PER_RUN) -> dict[str, int]:
    connection = connect(database_path)
    try:
        entries = store.entries_needing_questions(connection, minimum=MIN_QUESTIONS_PER_ENTRY)[:limit]
    finally:
        connection.close()
    tally = {"entries": 0, "written": 0, "held": 0, "failed": 0}
    for entry in entries:
        result = await generate_for_entry(database_path, entry.id, turn_factory)
        if result["status"] == "generated":
            tally["entries"] += 1
            tally["written"] += result["written"]
            tally["held"] += result["held"]
        else:
            tally["failed"] += 1
    return tally


async def refresh(database_path: Path, turn_factory: Any, *, reason: str = "requested") -> dict[str, Any]:
    """Compile what is stale, then write questions where a page has too few. Recorded for Today."""
    compiled = await compile_pending(database_path, turn_factory)
    generated = await generate_pending(database_path, turn_factory)
    report = {"at": utc_now(), "reason": reason, "pages": compiled, "questions": generated}
    connection = connect(database_path)
    try:
        store.record_refresh(connection, report)
    finally:
        connection.close()
    logger.info(
        "encyclopedia_refresh reason=%s compiled=%d questions=%d held=%d",
        reason,
        compiled["compiled"],
        generated["written"],
        generated["held"],
    )
    return report
