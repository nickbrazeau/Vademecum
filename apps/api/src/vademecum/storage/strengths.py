"""Strong and weak, and why (ADR 0026): the Improvement Map's evidence, topic by topic.

Every signal already recorded about a topic is gathered and shown with its
reason: board questions answered right and wrong (with the stems of the ones
missed), flashcards asked for again, open flags (including gaps a Socratic
session named), and exam-report areas above or below the mark. A topic's
standing is a simple sum of those signals, said as weak, mixed or strong with
the reasons beside it, so a learner can drill from a specialty to a topic to
the very question that was missed. It describes; it assigns nothing.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any

from . import map as map_store
from .flags import UNSORTED_TOPIC

MAX_EVIDENCE = 6


def _label(score: float) -> str:
    if score <= -0.5:
        return "weak"
    if score >= 0.5:
        return "strong"
    return "mixed"


def strengths(connection: sqlite3.Connection) -> dict[str, Any]:
    topics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"score": 0.0, "reasons": [], "missed": [], "flags": [], "areas": [], "answered": 0, "correct": 0, "again": 0}
    )

    for row in connection.execute(
        "SELECT q.topic, q.stem, a.correct FROM board_attempts a JOIN board_questions q ON q.id = a.question_id ORDER BY a.created_at DESC"
    ).fetchall():
        entry = topics[row["topic"]]
        entry["answered"] += 1
        if row["correct"]:
            entry["correct"] += 1
        elif len(entry["missed"]) < MAX_EVIDENCE and row["stem"] not in entry["missed"]:
            entry["missed"].append(row["stem"])

    for row in connection.execute(
        "SELECT c.topic, COUNT(*) AS n FROM flashcard_reviews r JOIN flashcards c ON c.id = r.card_id WHERE r.rating = 'again' GROUP BY c.topic"
    ).fetchall():
        topics[row["topic"]]["again"] += int(row["n"])

    for row in connection.execute(
        "SELECT topic, text FROM knowledge_gap_flags WHERE status = 'open' AND topic IS NOT NULL AND topic != '' ORDER BY created_at DESC"
    ).fetchall():
        if row["topic"].casefold() == UNSORTED_TOPIC:
            continue
        entry = topics[row["topic"]]
        if len(entry["flags"]) < MAX_EVIDENCE:
            entry["flags"].append(row["text"])
        entry.setdefault("flag_count", 0)
        entry["flag_count"] = entry.get("flag_count", 0) + 1

    for row in connection.execute(
        "SELECT a.topic, a.standing, a.quote FROM exam_areas a JOIN exam_reports r ON r.id = a.report_id WHERE r.status = 'parsed' ORDER BY r.created_at DESC"
    ).fetchall():
        entry = topics[row["topic"]]
        if len(entry["areas"]) < MAX_EVIDENCE:
            entry["areas"].append({"standing": row["standing"], "quote": row["quote"]})

    assigned = map_store.topic_specialties(connection)
    specialties = map_store.list_specialties(connection)
    page_specialty = {
        row["topic"]: row["specialty_id"]
        for row in connection.execute("SELECT topic, specialty_id FROM encyclopedia_entries WHERE specialty_id IS NOT NULL").fetchall()
    }

    # The learner model's reading of each topic (ADR 0031): still forming, fading, holding.
    from . import learner

    known = learner.model(connection)
    unit_state = {unit["key"]: unit for unit in known["units"]}

    def learner_state(topic: str) -> dict[str, Any] | None:
        unit = unit_state.get(known["by_name"].get(topic.casefold(), ""))
        if unit is None:
            return None
        return {"state": unit["state"], "label": unit["state_label"], "recall": unit["recall"], "next": unit["next"]}

    shaped: list[dict[str, Any]] = []
    for topic, entry in topics.items():
        reasons: list[str] = []
        score = 0.0
        if entry["answered"]:
            accuracy = entry["correct"] / entry["answered"]
            weight = min(1.0, entry["answered"] / 4)
            score += (accuracy - 0.6) * 2.5 * weight
            reasons.append(f"Board questions: {entry['correct']} of {entry['answered']} right.")
        if entry["again"]:
            score -= min(1.0, 0.25 * entry["again"])
            reasons.append(f"Flashcards asked for again {entry['again']} time{'s' if entry['again'] != 1 else ''}.")
        flag_count = entry.get("flag_count", 0)
        if flag_count:
            score -= min(1.5, 0.5 * flag_count)
            reasons.append(f"{flag_count} open flag{'s' if flag_count != 1 else ''}.")
        for area in entry["areas"]:
            if area["standing"] == "below":
                score -= 1.0
                reasons.append(f"Exam report: below the mark (“{area['quote']}”).")
            elif area["standing"] == "above":
                score += 1.0
                reasons.append(f"Exam report: above the mark (“{area['quote']}”).")
        if not reasons:
            continue
        specialty = assigned.get(topic)
        specialty_id = specialty.id if specialty is not None else page_specialty.get(topic)
        if specialty_id is None:
            matched = map_store.specialty_for(topic, assigned, specialties)
            specialty_id = matched.id if matched is not None else None
        shaped.append(
            {
                "topic": topic,
                "specialty_id": specialty_id,
                "score": round(max(-3.0, min(3.0, score)), 2),
                "label": _label(score),
                "reasons": reasons,
                "evidence": {"missed_questions": entry["missed"], "flags": entry["flags"], "exam_areas": entry["areas"]},
                "learner": learner_state(topic),
            }
        )

    names = {entry.id: entry.name for entry in specialties}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in shaped:
        grouped[item["specialty_id"] or "other"].append(item)
    out = []
    for specialty_id, items in grouped.items():
        items.sort(key=lambda item: item["score"])
        total = sum(item["score"] for item in items) / len(items)
        out.append(
            {
                "id": specialty_id,
                "name": names.get(specialty_id, "Other topics"),
                "score": round(total, 2),
                "label": _label(total),
                "weak": sum(1 for item in items if item["label"] == "weak"),
                "strong": sum(1 for item in items if item["label"] == "strong"),
                "topics": items,
            }
        )
    out.sort(key=lambda group: group["score"])
    return {"specialties": out, "topic_count": len(shaped)}
