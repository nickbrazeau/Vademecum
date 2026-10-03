"""Today and the Improvement Map.

Both are descriptive. Nothing here returns something to clear, count down, or
keep a run going.

What Today shows as "worth a look" is *generated learning points*, with their
support status and their sources beneath them -- not raw uploaded text. Raw
plain-text items are source material; presenting them as though they had been
through synthesis and verification would be the same lie the support labels
exist to prevent.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from . import literature as literature_store
from . import map as map_store
from . import reports as reports_store
from . import sources as source_store
from .flags import list_flags
from .learning import bank_summary, list_points
from .piles import TIERS
from .tutor import overview as tutor_overview

WORTH_A_LOOK_LIMIT = 4
RECENT_FLAGS_LIMIT = 5
LITERATURE_LIMIT = 6

NO_MATERIAL = (
    "Nothing built yet. Upload sources in the Source library and run Build "
    "learning material — Vademecum only shows points it made from your own files."
)

NO_LITERATURE = (
    "No literature topics are set up yet, so nothing is being watched. Add a "
    "topic in the Source library to have Vademecum check PubMed for updates."
)


@dataclass(frozen=True)
class ConfidenceSummary:
    confidence: str
    label: str
    meaning: str
    pile_count: int
    source_count: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TopicGap:
    topic: str | None
    open_flags: int
    addressed_flags: int
    last_flagged_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def confidence_summary(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return source_store.confidence_summary(connection)


def cover_sheet(
    connection: sqlite3.Connection, *, on_day: date | None = None
) -> dict[str, Any]:
    # on_day chooses the page of the day (ADR 0023); nothing else here rotates by date.
    bank = bank_summary(connection)
    points = list_points(connection, held=False, limit=WORTH_A_LOOK_LIMIT)
    tutor = tutor_overview(connection)

    try:
        updates = literature_store.list_updates(
            connection, state="unread", limit=LITERATURE_LIMIT
        )
        unread = literature_store.unread_count(connection)
        topic_count = len(literature_store.list_topics(connection))
    except sqlite3.Error:  # pragma: no cover - schema is always present
        updates, unread, topic_count = [], 0, 0

    from . import encyclopedia as encyclopedia_store

    page = encyclopedia_store.page_of_the_day(connection, on_day=on_day)
    page_payload = None
    if page is not None:
        page_payload = page.as_dict()
        page_payload["citations"] = encyclopedia_store.cited_points(connection, list(page.point_ids))
    encyclopedia_counts = encyclopedia_store.entry_counts(connection)

    return {
        "page": page_payload,
        "encyclopedia": {**encyclopedia_counts, "message": "" if page is not None else encyclopedia_store.NO_PAGES},
        "worth_a_look": [point.as_dict() for point in points],
        "worth_a_look_message": "" if points else NO_MATERIAL,
        "held": {
            "points": bank["points_held"],
            "questions": bank["questions_held"],
            "needs_re_review": bank["points_needing_re_review"],
            "reasons": bank["hold_reasons"],
        },
        "literature": {
            "unread": unread,
            "updates": [update.as_dict() for update in updates],
            "topic_count": topic_count,
            "message": NO_LITERATURE if topic_count == 0 else "",
        },
        "tutor": {
            "eligible": tutor.eligible,
            "held": tutor.held,
            "answered_total": tutor.answered_total,
            "cycle": tutor.cycle.as_dict(),
            "message": "" if tutor.eligible else NO_MATERIAL,
        },
        "recent_flags": [
            flag.as_dict()
            for flag in list_flags(connection, status="open", limit=RECENT_FLAGS_LIMIT)
        ],
        "open_flag_count": len(list_flags(connection, status="open")),
        "sources": {
            **source_store.library_summary(connection),
            "coverage_meaning": source_store.COVERAGE_MEANING,
        },
        "confidences": confidence_summary(connection),
    }


def improvement_map(connection: sqlite3.Connection) -> dict[str, Any]:
    """Where the gaps are, grouped by topic. A map, not a work list."""
    rows = connection.execute(
        """
        SELECT topic,
               SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_flags,
               SUM(CASE WHEN status = 'addressed' THEN 1 ELSE 0 END) AS addressed_flags,
               MAX(created_at) AS last_flagged_at
        FROM knowledge_gap_flags
        GROUP BY topic
        """
    ).fetchall()
    topics = [
        TopicGap(
            topic=row["topic"],
            open_flags=row["open_flags"],
            addressed_flags=row["addressed_flags"],
            last_flagged_at=row["last_flagged_at"],
        )
        for row in rows
    ]
    # Most open gaps first; unfiled flags sort last so a named topic is never
    # buried under the pile the system has not filed yet.
    topics.sort(key=lambda gap: (gap.topic is None, -gap.open_flags, gap.topic or ""))

    covered = connection.execute(
        "SELECT topic, COUNT(*) AS n FROM learning_point_topics"
        " GROUP BY topic ORDER BY n DESC LIMIT 40"
    ).fetchall()
    clusters = topic_clusters(connection)
    specialties = map_store.list_specialties(connection)
    assigned = map_store.topic_specialties(connection)

    def specialty(topic: str | None) -> dict[str, Any] | None:
        entry = map_store.specialty_for(topic, assigned, specialties)
        return None if entry is None else entry.as_dict()

    return {
        "topics": [
            {
                **gap.as_dict(),
                "cluster": clusters.get(gap.topic),
                "specialty": specialty(gap.topic),
            }
            for gap in topics
        ],
        "covered_topics": [
            {
                "topic": row["topic"],
                "point_count": row["n"],
                "cluster": clusters.get(row["topic"]),
                "specialty": specialty(row["topic"]),
            }
            for row in covered
        ],
        "links": topic_links(connection),
        # What the learner's exam reports say, by content area (ADR 0020).
        "report_areas": reports_store.areas_for_map(connection),
        "specialties": [entry.as_dict() for entry in specialties],
        "positions": map_store.positions(connection),
        "confidences": confidence_summary(connection),
        "unfiled_flag_count": sum(
            gap.open_flags for gap in topics if gap.topic is None
        ),
        "bank": bank_summary(connection),
    }


TOPIC_LINK_LIMIT = 200


def topic_links(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Pairs of topics that the same learning point carries, for the map's graph.

    A link is a fact about the material, not a judgement: two topics are linked
    when at least one generated point was filed under both, and the weight is
    how many points did. Nothing is inferred from wording, and flags alone
    never create a link -- a flag has one topic and says nothing about others.
    Pairs are ordered so `a < b`, which keeps each pair to one row.
    """
    rows = connection.execute(
        """
        SELECT a.topic AS a, b.topic AS b, COUNT(*) AS n
        FROM learning_point_topics a
        JOIN learning_point_topics b
          ON b.learning_point_id = a.learning_point_id AND a.topic < b.topic
        GROUP BY a.topic, b.topic
        ORDER BY n DESC, a.topic, b.topic
        LIMIT ?
        """,
        (TOPIC_LINK_LIMIT,),
    ).fetchall()
    return [{"a": row["a"], "b": row["b"], "weight": row["n"]} for row in rows]


def topic_clusters(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """The pile each topic mostly comes from, as `{topic: {id, title, tier}}`.

    There is no specialty taxonomy in this version, so the only honest grouping
    is the owner's own: which pile produced the points on that topic. A topic
    with points in several piles takes the pile with the most; a topic with no
    points (a flag the system has not covered yet) has no cluster.
    """
    rows = connection.execute(
        """
        SELECT t.topic AS topic, p.pile_id AS pile_id, piles.title AS title,
               piles.tier AS tier, COUNT(*) AS n
        FROM learning_point_topics t
        JOIN learning_points p ON p.id = t.learning_point_id
        JOIN piles ON piles.id = p.pile_id
        GROUP BY t.topic, p.pile_id
        ORDER BY t.topic, n DESC, piles.title
        """
    ).fetchall()
    clusters: dict[str, dict[str, str]] = {}
    for row in rows:
        clusters.setdefault(
            row["topic"],
            {"id": row["pile_id"], "title": row["title"], "tier": row["tier"]},
        )
    return clusters


# Kept so existing callers and tests that import them keep working.
__all__ = [
    "ConfidenceSummary",
    "NO_LITERATURE",
    "NO_MATERIAL",
    "TIERS",
    "TopicGap",
    "confidence_summary",
    "cover_sheet",
    "improvement_map",
    "topic_clusters",
    "topic_links",
]
