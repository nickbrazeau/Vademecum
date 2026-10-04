"""The Socratic tutor (ADR 0025): a dialogue about one page, assessed at the end.

Two ways to run it, one record. In a chat host the assistant is the tutor:
``material()`` hands it the page, the rules and the dialogue so far, it
converses with the learner in voice or text, and records each exchange and
the final assessment through the API. On the Mac's own model connection the
server runs each turn itself: the page and the transcript go to the model,
and the next question comes back. Either way the gaps named at the end become
flags on the page's topic, so the map and the flashcards take them up.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import encyclopedia as pages
from ..storage import flags as flag_store
from ..storage import socratic as store
from . import prompts, schemas
from .encyclopedia import _page_text, _points_text

logger = logging.getLogger("vademecum.socratic")

NO_PAGE = "There is no encyclopedia page to work from yet. Compile the encyclopedia first."
HOST_MODE = (
    "In this Vademecum the Socratic tutor runs in the conversation: open Vademecum in ChatGPT "
    "or Claude, in voice or text, and ask for a Socratic session. The assistant asks, you answer, "
    "and the session is recorded here."
)
GAP_PREFIX = "From a Socratic session: "


def _runner(turn_factory: Any, session_id: str):
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped("socratic", session_id)()
    return turn_factory()


def page_material(connection, entry: pages.Entry) -> str:
    handles = {f"p{index + 1}": point_id for index, point_id in enumerate(entry.point_ids)}
    cited = pages.cited_points(connection, list(entry.point_ids))
    return f"{_page_text(entry, handles)}\n\nTHE POINTS THE PAGE RESTS ON:\n{_points_text(cited, handles)}"


def choose_page(connection, entry_id: str | None) -> pages.Entry | None:
    """The page asked for, else one weighted towards the map's gaps, else the page of the day."""
    if entry_id:
        return pages.get_entry(connection, entry_id)
    from ..storage.flashcards import improvement_weights

    weights = improvement_weights(connection)
    for entry in pages.list_entries(connection):
        if entry.status == "current" and (entry.topic.casefold() in weights["flagged"] or entry.topic.casefold() in weights["below"]):
            return entry
    return pages.page_of_the_day(connection)


def start(connection, *, entry_id: str | None, mode: str) -> dict[str, Any]:
    entry = choose_page(connection, entry_id)
    if entry is None:
        return {"session": None, "note": NO_PAGE}
    session = store.create_session(connection, entry_id=entry.id, topic=entry.topic, title=entry.title, mode=mode)
    return {"session": session.as_dict(), "note": ""}


def material(connection, session: store.Session) -> dict[str, Any]:
    """What a chat host's assistant needs to be the tutor: the page, the rules, the dialogue."""
    entry = pages.get_entry(connection, session.entry_id) if session.entry_id else None
    return {
        "session_id": session.id,
        "rules": prompts.SOCRATIC_DEVELOPER,
        "page": page_material(connection, entry) if entry else "",
        "transcript": [dict(turn) for turn in session.transcript],
        "exchanges": session.exchanges,
        "max_exchanges": store.MAX_EXCHANGES,
        "output_schema": schemas.SOCRATIC_ASSESSMENT_SCHEMA,
        "how": (
            "Ask one open question at a time, in the owner's voice or text channel; after each answer call "
            "socratic_turn with your question and their answer; when done call socratic_finish with the assessment."
        ),
    }


def file_gaps(connection, session: store.Session) -> int:
    """Each named gap becomes a flag on the page's topic, once."""
    existing = {flag.text for flag in flag_store.list_flags(connection, status="open")}
    filed = 0
    for gap in session.assessment.get("knowledge_gaps") or []:
        text = f"{GAP_PREFIX}{gap}"
        if text in existing:
            continue
        flag_store.create_flag(connection, text=text, topic=session.topic or None)
        filed += 1
    return filed


async def answer(database_path: Path, session_id: str, learner_text: str, turn_factory: Any) -> dict[str, Any]:
    """On the Mac's own connection: record the answer, run one turn, record the question."""
    connection = connect(database_path)
    try:
        session = store.get_session(connection, session_id)
        if session.status != "open":
            return {"session": session.as_dict(), "note": "This session has ended."}
        if learner_text.strip():
            session = store.append(connection, session_id, role="learner", text=learner_text)
        entry = pages.get_entry(connection, session.entry_id) if session.entry_id else None
        page = page_material(connection, entry) if entry else ""
        transcript = [(turn["role"], turn["text"]) for turn in session.transcript]
        exchanges = session.exchanges
    finally:
        connection.close()
    try:
        runner = _runner(turn_factory, session_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.SOCRATIC_DEVELOPER,
            prompt=prompts.socratic_prompt(page, transcript, exchanges),
            output_schema=schemas.SOCRATIC_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("socratic_turn_failed category=%s", exc.category)
        connection = connect(database_path)
        try:
            return {"session": store.get_session(connection, session_id).as_dict(), "note": f"The model connection failed ({exc.category}). Your answer is kept; try again."}
        finally:
            connection.close()
    payload = reply.payload
    done = bool(payload.get("done")) or exchanges >= store.MAX_EXCHANGES
    acknowledgement = " ".join(str(payload.get("acknowledgement") or "").split())
    question = " ".join(str(payload.get("question") or "").split())
    probe = str(payload.get("probe") or "")
    connection = connect(database_path)
    try:
        spoken = f"{acknowledgement} {question}".strip()
        if spoken:
            session = store.append(connection, session_id, role="tutor", text=spoken, probe=probe)
        filed = 0
        if done:
            session = store.finish(connection, session_id, payload.get("assessment") or {})
            filed = file_gaps(connection, session)
        return {"session": session.as_dict(), "note": "", "gaps_filed": filed}
    finally:
        connection.close()
