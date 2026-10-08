"""Exam reports and the content areas read out of them (ADR 0020).

A report is a file the learner uploaded -- an in-training exam report, a
Step score report, a board feedback letter -- kept as text here and as the
original beside the other stored files. Its areas are what a model read out
of that text, each with a quote the server checked against the text itself.
The Improvement Map draws them; nothing here judges the learner.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

MAX_REPORT_TEXT = 200_000
STANDINGS = ("below", "at", "above")


@dataclass(frozen=True)
class ExamArea:
    id: str
    report_id: str
    ordinal: int
    topic: str
    specialty_id: str | None
    standing: str
    quote: str
    note: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExamReport:
    id: str
    display_name: str
    media_type: str
    sha256: str
    byte_size: int
    status: str
    status_detail: str
    created_at: str
    updated_at: str
    parsed_at: str | None
    text_chars: int
    areas: tuple[ExamArea, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["areas"] = [area.as_dict() for area in self.areas]
        return data


def _report(connection: sqlite3.Connection, row: sqlite3.Row) -> ExamReport:
    areas = tuple(
        ExamArea(**{key: r[key] for key in r.keys()})
        for r in connection.execute("SELECT * FROM exam_areas WHERE report_id = ? ORDER BY ordinal", (row["id"],)).fetchall()
    )
    return ExamReport(
        id=row["id"],
        display_name=row["display_name"],
        media_type=row["media_type"],
        sha256=row["sha256"],
        byte_size=row["byte_size"],
        status=row["status"],
        status_detail=row["status_detail"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        parsed_at=row["parsed_at"],
        text_chars=len(row["text"] or ""),
        areas=areas,
    )


def create_report(
    connection: sqlite3.Connection,
    *,
    display_name: str,
    media_type: str,
    sha256: str,
    byte_size: int,
    stored_name: str,
    text: str,
) -> ExamReport:
    report_id = new_id("rpt")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO exam_reports (id, display_name, media_type, sha256, byte_size, stored_name, text,"
            " status, status_detail, created_at, updated_at, parsed_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'uploaded', '', ?, ?, NULL)",
            (report_id, display_name, media_type, sha256, byte_size, stored_name, text[:MAX_REPORT_TEXT], now, now),
        )
    return get_report(connection, report_id)


def get_report(connection: sqlite3.Connection, report_id: str) -> ExamReport:
    row = connection.execute("SELECT * FROM exam_reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        raise NotFoundError("exam report", report_id)
    return _report(connection, row)


def report_text(connection: sqlite3.Connection, report_id: str) -> str:
    row = connection.execute("SELECT text FROM exam_reports WHERE id = ?", (report_id,)).fetchone()
    if row is None:
        raise NotFoundError("exam report", report_id)
    return str(row["text"] or "")


def stored_name_of(connection: sqlite3.Connection, report_id: str) -> str | None:
    row = connection.execute("SELECT stored_name FROM exam_reports WHERE id = ?", (report_id,)).fetchone()
    return None if row is None else row["stored_name"]


def list_reports(connection: sqlite3.Connection) -> list[ExamReport]:
    rows = connection.execute("SELECT * FROM exam_reports ORDER BY created_at DESC, id").fetchall()
    return [_report(connection, row) for row in rows]


def unparsed_report_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("SELECT id FROM exam_reports WHERE status = 'uploaded' ORDER BY created_at").fetchall()
    return [row["id"] for row in rows]


def delete_report(connection: sqlite3.Connection, report_id: str) -> None:
    get_report(connection, report_id)
    with transaction(connection) as tx:
        tx.execute("DELETE FROM exam_reports WHERE id = ?", (report_id,))


def set_parsed(connection: sqlite3.Connection, report_id: str, areas: list[dict[str, Any]]) -> ExamReport:
    """Replace the report's areas with what the model read, already checked."""
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute("DELETE FROM exam_areas WHERE report_id = ?", (report_id,))
        for ordinal, area in enumerate(areas):
            standing = area["standing"] if area["standing"] in STANDINGS else "at"
            tx.execute(
                "INSERT INTO exam_areas (id, report_id, ordinal, topic, specialty_id, standing, quote, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id("area"),
                    report_id,
                    ordinal,
                    str(area["topic"]).strip()[:60],
                    (str(area.get("specialty") or "").strip() or None),
                    standing,
                    str(area["quote"]).strip()[:200],
                    str(area.get("note") or "").strip()[:200],
                    now,
                ),
            )
        tx.execute(
            "UPDATE exam_reports SET status = 'parsed', status_detail = ?, parsed_at = ?, updated_at = ? WHERE id = ?",
            (f"{len(areas)} content areas read.", now, now, report_id),
        )
    return get_report(connection, report_id)


def set_failed(connection: sqlite3.Connection, report_id: str, detail: str) -> ExamReport:
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE exam_reports SET status = 'failed', status_detail = ?, updated_at = ? WHERE id = ?",
            (detail[:500], now, report_id),
        )
    return get_report(connection, report_id)


def areas_for_map(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every area from every parsed report, newest report first, for the map."""
    rows = connection.execute(
        "SELECT a.topic, a.specialty_id, a.standing, a.note, a.quote, r.id AS report_id, r.display_name, r.created_at"
        " FROM exam_areas a JOIN exam_reports r ON r.id = a.report_id"
        " WHERE r.status = 'parsed' ORDER BY r.created_at DESC, a.ordinal"
    ).fetchall()
    return [
        {
            "topic": row["topic"],
            "specialty_id": row["specialty_id"],
            "standing": row["standing"],
            "note": row["note"],
            "quote": row["quote"],
            "report_id": row["report_id"],
            "report": row["display_name"],
            "reported_at": row["created_at"],
        }
        for row in rows
    ]
