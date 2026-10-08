"""Filing flags under topics (ADR 0021): the system's job, done at last.

A flag is one sentence the learner wrote, or a link they saved, and it lands
without a topic; the map can only draw it as "not filed yet". On the Mac's
own model connection, unfiled flags are sent in a batch and come back with
a short topic each and a subspecialty from the list. The topic is written on
the flag and the learner's own specialty call, if any, is never overridden.
Runs at a scheduled build, on "Build now", and on request from the map.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import flags as flag_store
from ..storage import map as map_store
from .runner import runner_for
from . import prompts, schemas

logger = logging.getLogger("vademecum.flags")

WAITING = "Filing flags needs the Mac's own model connection (codex or claude mode)."


async def file_unfiled(database_path: Path, turn_factory: Any) -> dict[str, Any]:
    """One turn for up to MAX_FLAGS_PER_FILING unfiled open flags."""
    connection = connect(database_path)
    try:
        unfiled = [f for f in flag_store.list_flags(connection, status="open") if not f.topic][: schemas.MAX_FLAGS_PER_FILING]
        specialties = [(entry.id, entry.name) for entry in map_store.list_specialties(connection)]
    finally:
        connection.close()
    if not unfiled:
        return {"filed": 0, "unfiled": 0, "note": "Nothing to file."}
    ids = {identifier for identifier, _ in specialties}
    try:
        runner = runner_for(turn_factory, "flags", "unfiled")
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.FLAGS_DEVELOPER,
            prompt=prompts.flags_prompt([(f.id, f.text) for f in unfiled], specialties),
            output_schema=schemas.FLAGS_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("flag_filing_failed category=%s", exc.category)
        return {"filed": 0, "unfiled": len(unfiled), "note": f"The model connection failed ({exc.category})."}
    known = {f.id: f for f in unfiled}
    filed = 0
    connection = connect(database_path)
    try:
        for item in reply.payload.get("flags") or []:
            if not isinstance(item, dict):
                continue
            flag_id = str(item.get("id") or "")
            topic = " ".join(str(item.get("topic") or "").split())[:60]
            if flag_id not in known or not topic:
                continue
            flag_store.update_flag(connection, flag_id, topic=topic)
            specialty = str(item.get("specialty") or "").strip()
            if specialty in ids and map_store.topic_specialties(connection).get(topic) is None:
                map_store.set_topic_specialty(connection, topic, specialty)
            filed += 1
    finally:
        connection.close()
    logger.info("flags_filed count=%d of=%d", filed, len(unfiled))
    return {"filed": filed, "unfiled": len(unfiled) - filed, "note": ""}


def filer_for(database_path: Path, turn_factory: Any) -> Callable[[], Any]:
    async def run() -> int:
        return int((await file_unfiled(database_path, turn_factory)).get("filed", 0))

    return run
