"""The Improvement Map's own state: specialties per topic, and where nodes sat.

Everything else the map shows is derived from flags and learning points
(`overview.improvement_map`). These two are the owner's, and are stored:

* a topic's specialty -- the owner's assignment. A name match ("Nephrology"
  is nephrology; "renal" is an alias) is offered on read and marked as such,
  and is never written, so a guess never becomes a decision;
* the position of each node in the graph, so the picture holds still between
  opens and only the new topics move.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, utc_now

# Shorthand the owner is likely to type as a topic. Lower-cased, compared to
# the whole topic string after trimming; never to a substring.
SPECIALTY_ALIASES: dict[str, str] = {
    "gim": "general-internal-medicine",
    "im": "general-internal-medicine",
    "general medicine": "general-internal-medicine",
    "cardio": "cardiology",
    "cards": "cardiology",
    "pulm": "pulmonology",
    "pulmonary": "pulmonology",
    "gi": "gastroenterology",
    "hepatology": "gastroenterology",
    "nephro": "nephrology",
    "renal": "nephrology",
    "endo": "endocrinology",
    "rheum": "rheumatology",
    "immuno": "allergy-immunology",
    "allergy": "allergy-immunology",
    "id": "infectious-disease",
    "infectious diseases": "infectious-disease",
    "neuro": "neurology",
    "heme": "hematology",
    "onc": "oncology",
    "derm": "dermatology",
    "psych": "psychiatry",
}

MAX_POSITIONS = 2000


@dataclass(frozen=True)
class Specialty:
    id: str
    name: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TopicSpecialty:
    id: str
    name: str
    assigned_by: str  # 'owner' | 'name'

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_specialties(connection: sqlite3.Connection) -> list[Specialty]:
    rows = connection.execute(
        "SELECT id, name FROM specialties ORDER BY sort_order, name"
    ).fetchall()
    return [Specialty(id=row["id"], name=row["name"]) for row in rows]


def topic_specialties(connection: sqlite3.Connection) -> dict[str, TopicSpecialty]:
    """Owner assignments only. Name matches are added by `specialty_for`."""
    rows = connection.execute(
        """
        SELECT ts.topic AS topic, s.id AS id, s.name AS name, ts.assigned_by AS assigned_by
        FROM topic_specialties ts JOIN specialties s ON s.id = ts.specialty_id
        """
    ).fetchall()
    return {
        row["topic"]: TopicSpecialty(id=row["id"], name=row["name"], assigned_by=row["assigned_by"])
        for row in rows
    }


def specialty_for(
    topic: str | None,
    assigned: dict[str, TopicSpecialty],
    specialties: list[Specialty],
) -> TopicSpecialty | None:
    """The owner's assignment, else a whole-string name or alias match, else nothing."""
    if topic is None:
        return None
    owner = assigned.get(topic)
    if owner is not None:
        return owner
    key = topic.strip().lower()
    by_id = {entry.id: entry for entry in specialties}
    for entry in specialties:
        if key == entry.name.lower():
            return TopicSpecialty(id=entry.id, name=entry.name, assigned_by="name")
    alias = SPECIALTY_ALIASES.get(key)
    if alias is not None and alias in by_id:
        entry = by_id[alias]
        return TopicSpecialty(id=entry.id, name=entry.name, assigned_by="name")
    return None


def set_topic_specialty(
    connection: sqlite3.Connection, topic: str, specialty_id: str | None
) -> TopicSpecialty | None:
    """Record the owner's call, or clear it. Unknown specialty ids are refused."""
    topic = topic.strip()
    with transaction(connection) as tx:
        if specialty_id is None:
            tx.execute("DELETE FROM topic_specialties WHERE topic = ?", (topic,))
            return None
        row = tx.execute(
            "SELECT id, name FROM specialties WHERE id = ?", (specialty_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError("specialty", specialty_id)
        tx.execute(
            "INSERT INTO topic_specialties (topic, specialty_id, assigned_by, updated_at)"
            " VALUES (?, ?, 'owner', ?)"
            " ON CONFLICT(topic) DO UPDATE SET specialty_id = excluded.specialty_id,"
            " assigned_by = 'owner', updated_at = excluded.updated_at",
            (topic, specialty_id, utc_now()),
        )
        return TopicSpecialty(id=row["id"], name=row["name"], assigned_by="owner")


def positions(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT topic, x, y FROM map_positions ORDER BY topic"
    ).fetchall()
    return [{"topic": row["topic"], "x": row["x"], "y": row["y"]} for row in rows]


def save_positions(
    connection: sqlite3.Connection, entries: list[tuple[str, float, float]]
) -> int:
    """Replace the remembered layout with this one.

    The client sends every node it drew, so a topic absent from the list has
    left the map and its row is dropped -- the table never outgrows the graph.
    """
    now = utc_now()
    kept = [(topic.strip(), float(x), float(y)) for topic, x, y in entries[:MAX_POSITIONS]]
    with transaction(connection) as tx:
        tx.execute("DELETE FROM map_positions")
        tx.executemany(
            "INSERT INTO map_positions (topic, x, y, updated_at) VALUES (?, ?, ?, ?)",
            [(topic, x, y, now) for topic, x, y in kept if topic],
        )
    return len(kept)
