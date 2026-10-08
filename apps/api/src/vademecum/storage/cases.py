"""The Case Series hub (ADR 0022): other people's teaching cases, by title and link.

An entry is one case from a named series -- an NEJM Case Record, a Clinical
Problem-Solving article, a Clinical Problem Solvers episode, a Curbsiders
episode -- kept as its title, its link, who made it and when. The publisher's
public text (show notes; nothing, for a journal article PubMed carries no
abstract for) is kept bounded in ``text`` so the teaching points a model
writes can be checked against it; it is never returned by the API. The
points, the one-line summary and the think-first prompts are Vademecum's own
study notes beside the original, and every entry says whose work it is.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from . import app_state
from .common import NotFoundError, new_id, utc_now

CATALOGUE: tuple[dict[str, str], ...] = (
    {
        "id": "nejm_cpc",
        "name": "Case Records of the Massachusetts General Hospital",
        "short": "NEJM Case Records",
        "publisher": "The New England Journal of Medicine",
        "home": "https://www.nejm.org/",
    },
    {
        "id": "nejm_cps",
        "name": "Clinical Problem-Solving",
        "short": "NEJM Clinical Problem-Solving",
        "publisher": "The New England Journal of Medicine",
        "home": "https://www.nejm.org/",
    },
    {
        "id": "cps",
        "name": "The Clinical Problem Solvers",
        "short": "Clinical Problem Solvers",
        "publisher": "The Clinical Problem Solvers",
        "home": "https://clinicalproblemsolving.com/",
    },
    {
        "id": "curbsiders",
        "name": "The Curbsiders Internal Medicine Podcast",
        "short": "The Curbsiders",
        "publisher": "The Curbsiders",
        "home": "https://thecurbsiders.com/",
    },
)
SERIES_IDS: tuple[str, ...] = tuple(entry["id"] for entry in CATALOGUE)
_BY_ID = {entry["id"]: entry for entry in CATALOGUE}

STATUSES = ("new", "synthesised", "failed")
MAX_ATTEMPTS = 3
SNIPPET_CHARS = 280
MAX_TEXT = 12_000
MAX_POINTS = 6
MAX_THINK_FIRST = 4

KEY_SETTINGS = "case_series"
KEY_LAST_REFRESH = "case_series_last_refresh"
DEFAULT_INTERVAL_HOURS = 6.0
MIN_INTERVAL_HOURS = 1.0
MAX_INTERVAL_HOURS = 168.0


@dataclass(frozen=True)
class CaseEntry:
    id: str
    series: str
    subseries: str
    external_id: str
    title: str
    url: str
    credit: str
    published_on: str | None
    status: str
    status_detail: str
    attempts: int
    one_liner: str
    points: tuple[dict[str, str], ...]
    think_first: tuple[str, ...]
    specialty_id: str | None
    synthesised_at: str | None
    first_seen_at: str
    snippet: str
    text_chars: int

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["points"] = [dict(point) for point in self.points]
        data["think_first"] = list(self.think_first)
        catalogue = _BY_ID.get(self.series, {})
        data["series_name"] = catalogue.get("name", self.series)
        data["series_short"] = catalogue.get("short", self.series)
        data["publisher"] = catalogue.get("publisher", "")
        return data


def _json_list(value: str | None) -> list[Any]:
    try:
        data = json.loads(value or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _entry(row: sqlite3.Row) -> CaseEntry:
    text = row["text"] or ""
    snippet = " ".join(text.split())
    if len(snippet) > SNIPPET_CHARS:
        snippet = snippet[: SNIPPET_CHARS - 1].rstrip() + "…"
    points = tuple(
        {"point": str(item.get("point") or ""), "quote": str(item.get("quote") or "")}
        for item in _json_list(row["points"])
        if isinstance(item, dict)
    )
    return CaseEntry(
        id=row["id"],
        series=row["series"],
        subseries=row["subseries"],
        external_id=row["external_id"],
        title=row["title"],
        url=row["url"],
        credit=row["credit"],
        published_on=row["published_on"],
        status=row["status"],
        status_detail=row["status_detail"],
        attempts=int(row["attempts"]),
        one_liner=row["one_liner"],
        points=points,
        think_first=tuple(str(item) for item in _json_list(row["think_first"]) if isinstance(item, str)),
        specialty_id=row["specialty_id"],
        synthesised_at=row["synthesised_at"],
        first_seen_at=row["first_seen_at"],
        snippet=snippet,
        text_chars=len(text),
    )


# --- entries -----------------------------------------------------------------


def record_items(connection: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> int:
    """Keep every item not already known by (series, external id). Returns how many were new."""
    now = utc_now()
    added = 0
    with transaction(connection) as tx:
        for item in items:
            series = str(item.get("series") or "")
            external_id = str(item.get("external_id") or "").strip()
            title = " ".join(str(item.get("title") or "").split())
            url = str(item.get("url") or "").strip()
            if series not in SERIES_IDS or not external_id or not title or not url.startswith("https://"):
                continue
            cursor = tx.execute(
                "INSERT INTO case_entries (id, series, subseries, external_id, title, url, credit, published_on, text,"
                " status, status_detail, attempts, one_liner, points, think_first, specialty_id, synthesised_at,"
                " first_seen_at, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', '', 0, '', '[]', '[]', NULL, NULL, ?, ?, ?)"
                " ON CONFLICT(series, external_id) DO NOTHING",
                (
                    new_id("case"),
                    series,
                    str(item.get("subseries") or "")[:60],
                    external_id[:200],
                    title[:300],
                    url[:500],
                    str(item.get("credit") or "")[:300],
                    item.get("published_on") or None,
                    str(item.get("text") or "")[:MAX_TEXT],
                    now,
                    now,
                    now,
                ),
            )
            added += int(cursor.rowcount > 0)
    return added


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_entries(
    connection: sqlite3.Connection,
    *,
    series: str | None = None,
    specialty: str | None = None,
    q: str | None = None,
    limit: int = 100,
) -> list[CaseEntry]:
    clauses: list[str] = []
    params: list[Any] = []
    if series:
        clauses.append("series = ?")
        params.append(series)
    if specialty:
        clauses.append("specialty_id = ?")
        params.append(specialty)
    if q and q.strip():
        clauses.append("(title || ' ' || one_liner || ' ' || points || ' ' || think_first) LIKE ? ESCAPE '\\'")
        params.append(_like(q.strip()))
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"SELECT * FROM case_entries{where} ORDER BY COALESCE(published_on, '') DESC, first_seen_at DESC, id LIMIT ?",
        (*params, max(1, int(limit))),
    ).fetchall()
    return [_entry(row) for row in rows]


def get_entry(connection: sqlite3.Connection, entry_id: str) -> CaseEntry:
    row = connection.execute("SELECT * FROM case_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotFoundError("case", entry_id)
    return _entry(row)


def entry_material(connection: sqlite3.Connection, entry_id: str) -> tuple[str, str, str]:
    """The series, title and public text a synthesis may use, and nothing else."""
    row = connection.execute("SELECT series, title, text FROM case_entries WHERE id = ?", (entry_id,)).fetchone()
    if row is None:
        raise NotFoundError("case", entry_id)
    return row["series"], row["title"], row["text"] or ""


def pending_ids(connection: sqlite3.Connection, *, limit: int) -> list[str]:
    """Entries with no teaching points yet, newest first; a failure is retried a bounded number of times."""
    rows = connection.execute(
        "SELECT id FROM case_entries WHERE status = 'new' OR (status = 'failed' AND attempts < ?)"
        " ORDER BY COALESCE(published_on, '') DESC, first_seen_at DESC LIMIT ?",
        (MAX_ATTEMPTS, max(1, int(limit))),
    ).fetchall()
    return [row["id"] for row in rows]


def set_synthesised(
    connection: sqlite3.Connection,
    entry_id: str,
    *,
    one_liner: str,
    points: list[dict[str, str]],
    think_first: list[str],
    specialty_id: str | None,
    credit: str | None = None,
) -> CaseEntry:
    now = utc_now()
    kept_points = [
        {"point": str(p.get("point") or "").strip()[:300], "quote": str(p.get("quote") or "").strip()[:400]}
        for p in points[:MAX_POINTS]
    ]
    kept_prompts = [str(item).strip()[:200] for item in think_first[:MAX_THINK_FIRST] if str(item).strip()]
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE case_entries SET status = 'synthesised', status_detail = '', one_liner = ?, points = ?,"
            " think_first = ?, specialty_id = ?, synthesised_at = ?, updated_at = ?,"
            " credit = CASE WHEN ? != '' AND credit = '' THEN ? ELSE credit END WHERE id = ?",
            (
                one_liner.strip()[:200],
                json.dumps(kept_points),
                json.dumps(kept_prompts),
                specialty_id or None,
                now,
                now,
                (credit or "").strip()[:300],
                (credit or "").strip()[:300],
                entry_id,
            ),
        )
    return get_entry(connection, entry_id)


def set_failed(connection: sqlite3.Connection, entry_id: str, detail: str) -> CaseEntry:
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE case_entries SET status = 'failed', status_detail = ?, attempts = attempts + 1, updated_at = ?"
            " WHERE id = ?",
            (detail[:500], now, entry_id),
        )
    return get_entry(connection, entry_id)


NEW_CASE_DAYS = 21
MAX_NEW_CASES = 6


def new_cases(connection: sqlite3.Connection) -> list[CaseEntry]:
    """Cases published in the last three weeks, with their notes, not yet acknowledged on Today (ADR 0026)."""
    from datetime import date, timedelta

    since = (date.today() - timedelta(days=NEW_CASE_DAYS)).isoformat()
    rows = connection.execute(
        "SELECT * FROM case_entries WHERE acknowledged_at IS NULL AND status = 'synthesised'"
        " AND COALESCE(published_on, substr(first_seen_at, 1, 10)) >= ?"
        " ORDER BY COALESCE(published_on, '') DESC, first_seen_at DESC LIMIT ?",
        (since, MAX_NEW_CASES),
    ).fetchall()
    return [_entry(row) for row in rows]


def acknowledge(connection: sqlite3.Connection, entry_id: str) -> None:
    get_entry(connection, entry_id)
    with transaction(connection) as tx:
        tx.execute("UPDATE case_entries SET acknowledged_at = ?, updated_at = ? WHERE id = ?", (utc_now(), utc_now(), entry_id))


def counts(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute("SELECT series, status, COUNT(*) AS n FROM case_entries GROUP BY series, status").fetchall()
    by_series: dict[str, int] = {identifier: 0 for identifier in SERIES_IDS}
    total = 0
    pending = 0
    for row in rows:
        n = int(row["n"])
        total += n
        by_series[row["series"]] = by_series.get(row["series"], 0) + n
        if row["status"] != "synthesised":
            pending += n
    return {"total": total, "pending": pending, "by_series": by_series}


# --- settings and the record of refreshes -------------------------------------


_read = app_state.read_dict
_write = app_state.write_json


def clamp_interval(hours: float) -> float:
    try:
        value = float(hours)
    except (TypeError, ValueError):
        value = DEFAULT_INTERVAL_HOURS
    return max(MIN_INTERVAL_HOURS, min(value, MAX_INTERVAL_HOURS))


def get_settings(connection: sqlite3.Connection, *, default_interval_hours: float = DEFAULT_INTERVAL_HOURS) -> dict[str, Any]:
    """Off until chosen (ADR 0007's rule for anything on a timer); every series on once it is."""
    stored = _read(connection, KEY_SETTINGS) or {}
    chosen = stored.get("series") if isinstance(stored.get("series"), dict) else {}
    return {
        "enabled": bool(stored.get("enabled", False)),
        "interval_hours": clamp_interval(stored.get("interval_hours", default_interval_hours)),
        "series": {identifier: bool(chosen.get(identifier, True)) for identifier in SERIES_IDS},
    }


def set_settings(
    connection: sqlite3.Connection,
    *,
    enabled: bool,
    interval_hours: float,
    series: dict[str, bool] | None,
) -> dict[str, Any]:
    current = get_settings(connection)
    chosen = dict(current["series"])
    if series:
        for identifier, on in series.items():
            if identifier in SERIES_IDS:
                chosen[identifier] = bool(on)
    with transaction(connection) as tx:
        _write(tx, KEY_SETTINGS, {"enabled": bool(enabled), "interval_hours": clamp_interval(interval_hours), "series": chosen})
    return get_settings(connection)


def get_last_refresh(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return _read(connection, KEY_LAST_REFRESH)


def record_refresh(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        _write(tx, KEY_LAST_REFRESH, report)
