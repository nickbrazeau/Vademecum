"""Figures on encyclopedia pages, from the owner's own material (ADR 0026).

Nothing is drawn. A picture kept from a source (ADR 0013) knows its source and
the page or slide it came from; a learning point cites the same. So a figure
goes beside the paragraph whose points were taken from the page or slide the
picture is on, and says where it came from. Icons, logos and thin rules are
left out by size. Placement is the server's, by provenance, every time a page
is compiled and whenever the pictures or the points change; no model is asked.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..db import transaction
from .common import utc_now

MAX_PER_PARAGRAPH = 2
MAX_PER_PAGE = 6
MIN_SIDE = 140
MIN_AREA = 40_000
MAX_ASPECT = 4.0
_UNIT = re.compile(r"^\s*((?:page|slide|section|sheet|image)\s*\d*)", re.IGNORECASE)


def unit_of(locator: str) -> str:
    """'page 82 (characters 399-2767)' and 'page 82' are the same page."""
    match = _UNIT.match(locator or "")
    return " ".join(match.group(1).lower().split()) if match else (locator or "").strip().lower()


def _usable(row: sqlite3.Row) -> bool:
    width, height = int(row["width"]), int(row["height"])
    if min(width, height) < MIN_SIDE or width * height < MIN_AREA:
        return False
    return max(width, height) / max(1, min(width, height)) <= MAX_ASPECT


def figures_for_points(connection: sqlite3.Connection, point_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """For each point, the pictures on the page or slide it cites, largest and embedded first."""
    if not point_ids:
        return {}
    marks = ",".join("?" for _ in point_ids)
    cites = connection.execute(
        f"SELECT lps.learning_point_id, lps.source_id, lps.locator, s.display_name FROM learning_point_sources lps"
        f" JOIN sources s ON s.id = lps.source_id WHERE lps.learning_point_id IN ({marks}) AND s.excluded = 0",
        point_ids,
    ).fetchall()
    by_source: dict[str, list[sqlite3.Row]] = {}
    found: dict[str, list[dict[str, Any]]] = {}
    for cite in cites:
        if cite["source_id"] not in by_source:
            by_source[cite["source_id"]] = connection.execute(
                "SELECT id, locator, origin, width, height, byte_size, stored_name FROM source_images WHERE source_id = ?"
                " ORDER BY origin = 'embedded' DESC, byte_size DESC",
                (cite["source_id"],),
            ).fetchall()
        unit = unit_of(cite["locator"])
        for image in by_source[cite["source_id"]]:
            if unit_of(image["locator"]) != unit or not _usable(image):
                continue
            found.setdefault(cite["learning_point_id"], []).append(
                {
                    "image_id": image["id"],
                    "source": cite["display_name"],
                    "locator": image["locator"],
                    "width": int(image["width"]),
                    "height": int(image["height"]),
                    # The file's own extension, so the Markdown copy opens in any editor.
                    "ext": (("." + image["stored_name"].rsplit(".", 1)[-1]) if "." in image["stored_name"] else ""),
                }
            )
    return found


def place(connection: sqlite3.Connection, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same sections, each paragraph carrying the figures its points' pages hold; a figure once per page."""
    point_ids = [pid for section in sections for paragraph in section.get("paragraphs", []) for pid in paragraph.get("point_ids", [])]
    available = figures_for_points(connection, list(dict.fromkeys(point_ids)))
    used: set[str] = set()
    placed: list[dict[str, Any]] = []
    for section in sections:
        paragraphs = []
        for paragraph in section.get("paragraphs", []):
            figures: list[dict[str, Any]] = []
            for point_id in paragraph.get("point_ids", []):
                for figure in available.get(point_id, []):
                    if len(figures) >= MAX_PER_PARAGRAPH or len(used) >= MAX_PER_PAGE:
                        break
                    if figure["image_id"] in used:
                        continue
                    used.add(figure["image_id"])
                    figures.append(figure)
            paragraphs.append({**paragraph, "figures": figures})
        placed.append({**section, "paragraphs": paragraphs})
    return placed


def refresh(connection: sqlite3.Connection) -> int:
    """Re-place figures on every current page; writes only the pages that changed. Returns how many."""
    changed = 0
    rows = connection.execute("SELECT id, sections FROM encyclopedia_entries WHERE status = 'current'").fetchall()
    for row in rows:
        try:
            sections = json.loads(row["sections"] or "[]")
        except ValueError:
            continue
        if not isinstance(sections, list):
            continue
        placed = place(connection, sections)
        if placed != sections:
            with transaction(connection) as tx:
                tx.execute("UPDATE encyclopedia_entries SET sections = ?, updated_at = ? WHERE id = ?", (json.dumps(placed), utc_now(), row["id"]))
            changed += 1
    return changed
