"""Session logs from the phone, taken in from the source folder's inbox (ADR 0016).

A session log is what the assistant on the phone wrote at the end of a
session from the pack: attempts and flags, in one fenced JSON block. The
learner saves it as a file into ``<source folder>/inbox/``. Each scan reads
every file there once -- a file is remembered by its digest -- and records
what it recognises: an attempt against a question that exists, a flag with
its text. Anything else is reported and left alone. Nothing is deleted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ..db import transaction
from ..storage import flags as flag_store
from ..storage import learning, tutor
from ..storage.common import NotFoundError, utc_now
from .pack import PACK_VERSION, SESSION_FENCE

INBOX_DIRNAME = "inbox"
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_ATTEMPTS = 500
MAX_FLAGS = 200
OUTCOMES = {"correct", "partially_correct", "incorrect"}
_FENCE = re.compile(rf"```{re.escape(SESSION_FENCE)}\s*\n(.*?)\n```", re.S)
_STATE_PREFIX = "inbox:"


def _seen(connection: sqlite3.Connection, digest: str) -> bool:
    return connection.execute("SELECT 1 FROM app_state WHERE key = ?", (_STATE_PREFIX + digest,)).fetchone() is not None


def _remember(tx: sqlite3.Connection, digest: str, note: str) -> None:
    tx.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (_STATE_PREFIX + digest, note, utc_now()),
    )


def parse_session(text: str) -> list[dict[str, Any]]:
    """Every session block in *text*, or the whole text if it is one JSON object."""
    found: list[dict[str, Any]] = []
    for match in _FENCE.finditer(text):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and "vademecum_session" in data:
            found.append(data)
    if not found:
        stripped = text.strip()
        if stripped.startswith("{"):
            try:
                data = json.loads(stripped)
            except ValueError:
                data = None
            if isinstance(data, dict) and "vademecum_session" in data:
                found.append(data)
    return found


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def apply_session(connection: sqlite3.Connection, session: dict[str, Any]) -> dict[str, int]:
    """Record one session's attempts and flags. Unknown questions are counted, not stored."""
    counts = {"attempts": 0, "flags": 0, "unknown_questions": 0, "malformed": 0}
    attempts = session.get("attempts") if isinstance(session.get("attempts"), list) else []
    flags = session.get("flags") if isinstance(session.get("flags"), list) else []
    with transaction(connection):
        for item in attempts[:MAX_ATTEMPTS]:
            if not isinstance(item, dict):
                counts["malformed"] += 1
                continue
            question_id = _text(item.get("question_id"), 64)
            outcome = _text(item.get("outcome"), 32)
            answer = _text(item.get("answer"), 8000)
            if not question_id or outcome not in OUTCOMES:
                counts["malformed"] += 1
                continue
            try:
                learning.get_question(connection, question_id)
            except NotFoundError:
                counts["unknown_questions"] += 1
                continue
            tutor.record_attempt(
                connection,
                question_id=question_id,
                outcome=outcome,
                graded_by="model",
                answer=answer,
                feedback=_text(item.get("feedback"), 4000),
                missing_or_unsafe=_text(item.get("missing_or_unsafe"), 2000),
                improved_answer=_text(item.get("improved_answer"), 2000),
                uncertainty="Graded by the assistant on the phone, from the pack, outside a checked turn.",
            )
            counts["attempts"] += 1
        for item in flags[:MAX_FLAGS]:
            if not isinstance(item, dict):
                counts["malformed"] += 1
                continue
            text = _text(item.get("text"), 4000)
            if not text:
                counts["malformed"] += 1
                continue
            topic = _text(item.get("topic"), 120) or None
            flag_store.create_flag(connection, text=text, topic=topic)
            counts["flags"] += 1
    return counts


def scan_inbox(connection: sqlite3.Connection, folder: Path) -> dict[str, Any]:
    """Take in every new session log in the inbox. Idempotent; returns a report."""
    inbox = folder / INBOX_DIRNAME
    report: dict[str, Any] = {
        "present": inbox.is_dir(),
        "files": 0,
        "attempts": 0,
        "flags": 0,
        "unknown_questions": 0,
        "already_taken": 0,
        "nothing_recognised": [],
    }
    if not inbox.is_dir():
        return report
    for path in sorted((p for p in inbox.iterdir() if p.is_file()), key=lambda p: p.name.casefold()):
        if path.name.startswith((".", "~")) or path.suffix.lower() not in (".md", ".txt", ".json"):
            continue
        try:
            if path.stat().st_size > MAX_LOG_BYTES:
                report["nothing_recognised"].append(path.name)
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if _seen(connection, digest):
            report["already_taken"] += 1
            continue
        sessions = parse_session(raw.decode("utf-8", errors="replace"))
        if not sessions:
            report["nothing_recognised"].append(path.name)
            continue
        report["files"] += 1
        totals = {"attempts": 0, "flags": 0, "unknown_questions": 0}
        with transaction(connection) as tx:
            for session in sessions:
                if int(session.get("vademecum_session", 0) or 0) > PACK_VERSION:
                    continue
                counts = apply_session(connection, session)
                for key in totals:
                    totals[key] += counts[key]
            _remember(tx, digest, f"{path.name}: {totals['attempts']} attempts, {totals['flags']} flags")
        for key in totals:
            report[key] += totals[key]
    return report
