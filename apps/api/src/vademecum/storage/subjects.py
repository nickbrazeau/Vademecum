"""One page per subject (feedback of 11 October; ADR 0035).

A page was one topic string, and topic strings are the model's free wording of each
learning point: "Antifungal mechanism", "Antifungal mechanisms", "Antifungal spectrum"
and "Antifungal toxicity" were four pages of one or two points each. Now a page is a
subject: its own topic, plus every topic filed under it in ``page_topics``. It gathers
the points of all of them and is written as one page, with a section per aspect.

Merging pages moves everything that hangs on the absorbed page to the one that stays --
board questions, flashcards (with their answers and reviews, which hang on those),
literature, Socratic sessions and podcast links -- and only then removes the absorbed
row, so no learning history is lost. An old link to an absorbed page opens the page it
went into.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from ..db import transaction
from . import app_state
from .common import utc_now

REDIRECTS_KEY = "page_redirects"
CONSOLIDATED_KEY = "pages_consolidated"


def spelling_key(topic: str) -> str:
    """A topic with case, hyphens, punctuation and plural endings set aside:
    "Beta-blockers" and "beta blocker" have one key."""
    text = topic.casefold().replace("-", " ").replace("/", " ")
    text = re.sub(r"[^a-z0-9 ]", "", text)
    words = []
    for word in text.split():
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
            word = word[:-1]
        words.append(word)
    return " ".join(words)


def filed(connection: sqlite3.Connection) -> dict[str, str]:
    """Each topic filed under a subject: topic -> subject."""
    return {row["topic"]: row["subject"] for row in connection.execute("SELECT topic, subject FROM page_topics")}


def subject_of(connection: sqlite3.Connection, topic: str) -> str:
    row = connection.execute("SELECT subject FROM page_topics WHERE topic = ?", (topic,)).fetchone()
    return row["subject"] if row else topic


def members(connection: sqlite3.Connection, subject: str) -> list[str]:
    """The subject's own topic first, then the topics filed under it."""
    rows = connection.execute("SELECT topic FROM page_topics WHERE subject = ? ORDER BY topic", (subject,)).fetchall()
    return [subject, *[row["topic"] for row in rows if row["topic"] != subject]]


def placed(connection: sqlite3.Connection) -> set[str]:
    """Topics that have a page or a place: a page's own topic, or any topic filed."""
    rows = connection.execute("SELECT topic FROM encyclopedia_entries UNION SELECT topic FROM page_topics").fetchall()
    return {row["topic"] for row in rows}


def members_by_subject(connection: sqlite3.Connection) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for topic, subject in filed(connection).items():
        if topic != subject:
            grouped.setdefault(subject, []).append(topic)
    return grouped


def file_under(tx: sqlite3.Connection, topic: str, subject: str) -> None:
    """File a topic under a subject, and anything filed under the topic with it. A subject
    filed under another is followed to its own subject, so there are never chains."""
    root = subject_of(tx, subject)
    if topic == root:
        return
    now = utc_now()
    tx.execute("UPDATE page_topics SET subject = ?, created_at = ? WHERE subject = ?", (root, now, topic))
    tx.execute(
        "INSERT INTO page_topics (topic, subject, created_at) VALUES (?, ?, ?)"
        " ON CONFLICT(topic) DO UPDATE SET subject = excluded.subject, created_at = excluded.created_at",
        (topic, root, now),
    )


def own_page(tx: sqlite3.Connection, topic: str) -> None:
    """A new topic looked at and found to need a page of its own: filed under itself, so it
    is compiled, and not asked about again."""
    tx.execute("INSERT OR IGNORE INTO page_topics (topic, subject, created_at) VALUES (?, ?, ?)", (topic, topic, utc_now()))


def redirects(connection: sqlite3.Connection) -> dict[str, str]:
    data = app_state.read_dict(connection, REDIRECTS_KEY) or {}
    return {str(k): str(v) for k, v in data.items()}


def resolve(connection: sqlite3.Connection, entry_id: str) -> str:
    """The page an id now opens: itself, or the page it was merged into."""
    seen: set[str] = set()
    moved = None
    while True:
        row = connection.execute("SELECT 1 FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is not None or entry_id in seen:
            return entry_id
        seen.add(entry_id)
        moved = moved if moved is not None else redirects(connection)
        if entry_id not in moved:
            return entry_id
        entry_id = moved[entry_id]


def merge(connection: sqlite3.Connection, survivor_id: str, absorbed_ids: list[str], *, title: str | None = None) -> dict[str, Any]:
    """Fold pages into one. The survivor keeps its id and topic and takes the others'
    topics, questions, cards, literature, sessions and podcast links; the absorbed rows go,
    and their ids open the survivor from now on. The survivor is written again from all
    its points at the next compile, because its points have changed."""
    absorbed_ids = [entry_id for entry_id in dict.fromkeys(absorbed_ids) if entry_id != survivor_id]
    survivor = connection.execute("SELECT id, topic FROM encyclopedia_entries WHERE id = ?", (survivor_id,)).fetchone()
    if survivor is None or not absorbed_ids:
        return {"merged": 0}
    subject = survivor["topic"]
    moved = redirects(connection)
    merged = 0
    with transaction(connection) as tx:
        for entry_id in absorbed_ids:
            row = tx.execute("SELECT id, topic FROM encyclopedia_entries WHERE id = ?", (entry_id,)).fetchone()
            if row is None:
                continue
            file_under(tx, row["topic"], subject)
            # The same question or card on both pages (their points overlapped): one copy stays,
            # and the answers and reviews of the other move to it, so no history is lost and the
            # page never holds a question twice (live, 11 October: this rolled every merge back).
            for kind, table, history, column in (
                ("question", "board_questions", "board_attempts", "question_id"),
                ("card", "flashcards", "flashcard_reviews", "card_id"),
            ):
                for item in tx.execute(f"SELECT id, content_hash FROM {table} WHERE entry_id = ?", (entry_id,)).fetchall():
                    kept = tx.execute(
                        f"SELECT id FROM {table} WHERE entry_id = ? AND content_hash = ?", (survivor_id, item["content_hash"])
                    ).fetchone()
                    if kept is not None:
                        tx.execute(f"UPDATE {history} SET {column} = ? WHERE {column} = ?", (kept["id"], item["id"]))
                        tx.execute(f"DELETE FROM {table} WHERE id = ?", (item["id"],))
                        continue
                    tx.execute(f"UPDATE {table} SET entry_id = ?, topic = ? WHERE id = ?", (survivor_id, subject, item["id"]))
            tx.execute(
                "UPDATE OR IGNORE encyclopedia_records SET entry_id = ? WHERE entry_id = ?", (survivor_id, entry_id)
            )
            tx.execute("UPDATE socratic_sessions SET entry_id = ? WHERE entry_id = ?", (survivor_id, entry_id))
            for episode in tx.execute("SELECT id, entry_ids FROM podcast_episodes WHERE entry_ids LIKE ?", (f'%"{entry_id}"%',)).fetchall():
                try:
                    ids = [str(x) for x in json.loads(episode["entry_ids"] or "[]")]
                except ValueError:
                    continue
                ids = list(dict.fromkeys(survivor_id if x == entry_id else x for x in ids))
                tx.execute("UPDATE podcast_episodes SET entry_ids = ? WHERE id = ?", (json.dumps(ids), episode["id"]))
            tx.execute("UPDATE page_changes SET entry_id = ? WHERE entry_id = ? AND applied_at IS NULL", (survivor_id, entry_id))
            # Anything that pointed here points at the survivor now.
            for old, new in list(moved.items()):
                if new == entry_id:
                    moved[old] = survivor_id
            moved[entry_id] = survivor_id
            tx.execute("DELETE FROM encyclopedia_entries WHERE id = ?", (entry_id,))
            merged += 1
        if title:
            tx.execute("UPDATE encyclopedia_entries SET title = ?, updated_at = ? WHERE id = ?", (title[:120], utc_now(), survivor_id))
        app_state.write_json(tx, REDIRECTS_KEY, moved)
    return {"merged": merged, "survivor": survivor_id}


def consolidated(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return app_state.read_dict(connection, CONSOLIDATED_KEY)


def mark_consolidated(connection: sqlite3.Connection, report: dict[str, Any]) -> None:
    with transaction(connection) as tx:
        app_state.write_json(tx, CONSOLIDATED_KEY, {**report, "at": utc_now()})
