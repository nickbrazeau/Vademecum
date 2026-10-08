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

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import encyclopedia as store
from ..storage import flags as flag_store
from ..storage import map as map_store
from ..storage.common import drop_disclaimers, utc_now
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


def check_entry(
    payload: dict[str, Any],
    *,
    handles: dict[str, str],
    specialty_ids: set[str],
    record_handles: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """What the server keeps: paragraphs whose handles all map, sections that keep a paragraph.

    A paragraph may rest on points, on literature records, or both; one that names
    nothing it can be traced to is dropped. At least one section must rest on a point,
    so a page is never literature alone.
    """
    record_handles = record_handles or {}
    sections: list[dict[str, Any]] = []
    cited: list[str] = []
    cited_records: list[str] = []
    for section in (payload.get("sections") or [])[: schemas.MAX_ENTRY_SECTIONS]:
        if not isinstance(section, dict):
            continue
        paragraphs: list[dict[str, Any]] = []
        for paragraph in (section.get("paragraphs") or [])[: schemas.MAX_ENTRY_PARAGRAPHS]:
            if not isinstance(paragraph, dict):
                continue
            text = drop_disclaimers(str(paragraph.get("text") or ""))
            ids = list(dict.fromkeys(handles[str(h)] for h in (paragraph.get("points") or []) if str(h) in handles))
            records = list(dict.fromkeys(record_handles[str(h)] for h in (paragraph.get("records") or []) if str(h) in record_handles))
            if not text or (not ids and not records):
                continue
            paragraphs.append({"text": text, "point_ids": ids, "record_ids": records})
        heading = " ".join(str(section.get("heading") or "").split())
        if paragraphs and heading:
            sections.append({"heading": heading, "paragraphs": paragraphs})
            for paragraph in paragraphs:
                cited.extend(paragraph["point_ids"])
                cited_records.extend(paragraph["record_ids"])
    if not sections or not cited:
        return None
    specialty = str(payload.get("specialty") or "").strip()
    return {
        "title": " ".join(str(payload.get("title") or "").split()),
        "summary": drop_disclaimers(str(payload.get("summary") or "")),
        "specialty_id": specialty if specialty in specialty_ids else None,
        "sections": sections,
        "cited": list(dict.fromkeys(cited)),
        "cited_records": list(dict.fromkeys(cited_records)),
    }


MAX_RECORDS_PER_PAGE = 8
MAX_ABSTRACT_CHARS = 1500


def review_literature(database_path: Path, topic: str, provider: Any) -> tuple[list[dict[str, Any]], str]:
    """One public search for the topic; the records kept, newest and guidelines first. Blocking."""
    from ..literature.http import ProviderError
    from ..literature.pubmed import QueryError
    from ..storage.learning import upsert_evidence_record

    if provider is None:
        return [], "No literature provider is configured."
    try:
        articles = provider.search(topic)
    except QueryError:
        return [], "The topic's wording cannot be searched."
    except ProviderError as exc:
        return [], f"The literature search failed ({exc.category})."
    kept: list[dict[str, Any]] = []
    connection = connect(database_path)
    try:
        for article in articles:
            if getattr(article, "is_notice", False) or getattr(article, "retracted", False):
                continue
            record_id = upsert_evidence_record(connection, article)
            kept.append(
                {
                    "id": record_id,
                    "title": str(getattr(article, "title", "") or ""),
                    "journal": str(getattr(article, "journal", "") or ""),
                    "year": str(getattr(article, "published_on", "") or "")[:4],
                    "abstract": str(getattr(article, "abstract", "") or "")[:MAX_ABSTRACT_CHARS],
                }
            )
            if len(kept) >= MAX_RECORDS_PER_PAGE:
                break
    finally:
        connection.close()
    return kept, "" if kept else "No public record matched the topic."


async def compile_topic(database_path: Path, topic: str, turn_factory: Any, provider: Any = None) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        points = points_for_topic(connection, topic)
        specialties = [(entry.id, entry.name) for entry in map_store.list_specialties(connection)]
        assigned = map_store.topic_specialties(connection)
    finally:
        connection.close()
    if not points:
        return {"topic": topic, "status": "skipped", "detail": "No points."}
    # The literature review: one public search for the topic's own words, in a
    # thread because the client blocks; a failed search is a note on the page.
    records, literature_note = await asyncio.to_thread(review_literature, database_path, topic, provider)
    record_handles = {f"r{index + 1}": record["id"] for index, record in enumerate(records)}
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
            prompt=prompts.entry_prompt(
                topic,
                listed,
                specialties,
                [(handle, r["title"], r["journal"], r["year"], r["abstract"]) for handle, r in zip(record_handles, records, strict=True)],
            ),
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
    checked = check_entry(reply.payload, handles=handles, specialty_ids=ids, record_handles=record_handles)
    if checked is None:
        logger.info("entry_compile_empty topic_points=%d", len(points))
        connection = connect(database_path)
        try:
            store.mark_entry_failed(connection, topic, "The page the model wrote cited none of the points, so it was not kept.")
        finally:
            connection.close()
        return {"topic": topic, "status": "failed", "detail": "unsourced"}
    # Figures from the owner's own material, beside the paragraphs whose points share their page or slide.
    from ..storage import figures as figure_store

    connection = connect(database_path)
    try:
        checked["sections"] = figure_store.place(connection, checked["sections"])
    finally:
        connection.close()
    owner_call = assigned.get(topic)
    specialty_id = owner_call.id if owner_call is not None else checked["specialty_id"]
    connection = connect(database_path)
    try:
        # Deleted by the owner while this page was being written: it is not written back.
        if topic.casefold() in store.deleted_pages(connection):
            logger.info("entry_skipped reason=deleted")
            return {"status": "skipped", "detail": "deleted"}
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
        store.set_entry_literature(
            connection, entry.id, [r["id"] for r in records], cited=set(checked["cited_records"]), note=literature_note
        )
    finally:
        connection.close()
    logger.info(
        "entry_compiled points=%d cited=%d sections=%d records=%d records_cited=%d",
        len(points),
        len(checked["cited"]),
        len(checked["sections"]),
        len(records),
        len(checked["cited_records"]),
    )
    return {"topic": topic, "status": "compiled", "entry_id": entry.id, "version": entry.version}


async def compile_pending(
    database_path: Path, turn_factory: Any, *, limit: int = MAX_COMPILES_PER_RUN, provider: Any = None
) -> dict[str, int]:
    connection = connect(database_path)
    try:
        topics = store.topics_to_compile(connection)[:limit]
    finally:
        connection.close()
    tally = {"compiled": 0, "failed": 0, "remaining": 0}
    for topic in topics:
        result = await compile_topic(database_path, topic, turn_factory, provider)
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
        # The letter is the interface's to add; a model that wrote "A. ..." is not wrong, just early.
        options = [re.sub(r"^\(?[A-Ea-e][.)]\s+", "", " ".join(str(o).split())) for o in (item.get("options") or [])]
        answer = str(item.get("answer") or "").strip().upper()
        explanation = drop_disclaimers(str(item.get("explanation") or ""))
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


# --- flashcards (ADR 0024) ----------------------------------------------------------

MIN_CARDS_PER_ENTRY = 4
MAX_CARD_GENERATIONS_PER_RUN = 12


def check_cards(payload: dict[str, Any], *, handles: dict[str, str]) -> list[dict[str, Any]]:
    """Front and back present, a cited point or a hold, a front that stands alone."""
    drafts: list[dict[str, Any]] = []
    for item in (payload.get("cards") or [])[: schemas.MAX_FLASHCARDS]:
        if not isinstance(item, dict):
            continue
        front = " ".join(str(item.get("front") or "").split())
        back = " ".join(str(item.get("back") or "").split())
        if not front or not back:
            continue
        point_ids = list(dict.fromkeys(handles[str(h)] for h in (item.get("points") or []) if str(h) in handles))
        hold = ""
        if not point_ids:
            hold = "The card cites none of the page's points, so its answer cannot be traced to a source."
        elif _LEANS_ON_TEXT.search(front):
            hold = "The front leans on 'the text' instead of standing on its own."
        drafts.append({"front": front, "back": back, "point_ids": point_ids, "hold_reason": hold})
    return drafts


async def generate_cards_for_entry(database_path: Path, entry_id: str, turn_factory: Any) -> dict[str, Any]:
    from ..storage import flashcards as card_store

    connection = connect(database_path)
    try:
        entry = store.get_entry(connection, entry_id)
        cited = store.cited_points(connection, list(entry.point_ids))
    finally:
        connection.close()
    handles = {f"p{index + 1}": point_id for index, point_id in enumerate(entry.point_ids)}
    page = f"{_page_text(entry, handles)}\n\nTHE POINTS THE PAGE RESTS ON:\n{_points_text(cited, handles)}"
    try:
        runner = _runner(turn_factory, "flashcards", entry_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.FLASHCARD_DEVELOPER,
            prompt=prompts.flashcard_prompt(page),
            output_schema=schemas.FLASHCARD_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("flashcard_generation_failed category=%s", exc.category)
        return {"entry_id": entry_id, "status": "failed", "written": 0, "held": 0}
    drafts = check_cards(reply.payload, handles=handles)
    connection = connect(database_path)
    try:
        tally = card_store.insert_cards(connection, entry_id=entry.id, topic=entry.topic, entry_version=entry.version, drafts=drafts)
    finally:
        connection.close()
    logger.info("flashcards_generated offered=%d written=%d held=%d", len(drafts), tally["written"], tally["held"])
    return {"entry_id": entry_id, "status": "generated", **tally}


async def generate_cards_pending(database_path: Path, turn_factory: Any, *, limit: int = MAX_CARD_GENERATIONS_PER_RUN) -> dict[str, int]:
    from ..storage import flashcards as card_store

    connection = connect(database_path)
    try:
        entries = card_store.entries_needing_cards(connection, minimum=MIN_CARDS_PER_ENTRY)[:limit]
    finally:
        connection.close()
    tally = {"entries": 0, "written": 0, "held": 0, "failed": 0}
    for entry_id in entries:
        result = await generate_cards_for_entry(database_path, entry_id, turn_factory)
        if result["status"] == "generated":
            tally["entries"] += 1
            tally["written"] += result["written"]
            tally["held"] += result["held"]
        else:
            tally["failed"] += 1
    return tally


async def refresh(
    database_path: Path, turn_factory: Any, *, reason: str = "requested", provider: Any = None
) -> dict[str, Any]:
    """Compile what is stale, then write questions and cards where a page has too few. Recorded for Today."""
    compiled = await compile_pending(database_path, turn_factory, provider=provider)
    generated = await generate_pending(database_path, turn_factory)
    cards = await generate_cards_pending(database_path, turn_factory)
    report = {"at": utc_now(), "reason": reason, "pages": compiled, "questions": generated, "cards": cards}
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
