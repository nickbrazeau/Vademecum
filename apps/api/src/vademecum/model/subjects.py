"""Gathering pages into subjects (feedback of 11 October; ADR 0035): two checked model turns.

Once, the existing pages are grouped: first by spelling alone ("Beta-blockers", "Beta
blockers"), then by the model, specialty by specialty, from page titles and point counts
only -- public topic names, never a passage. A group is applied by folding its pages
into the one with the owner's edit, or else the most points; everything that hangs on
the others moves with them.

From then on, every new topic is placed before anything is written: on the page with
its spelling, or, asked of the model with the pages it might belong on, on one of them;
otherwise it gets a page of its own. So a build adds to pages instead of starting new
ones beside them.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect, transaction
from ..storage import encyclopedia as pages
from ..storage import page_files
from ..storage import subjects as store
from . import prompts, schemas
from .runner import runner_for

logger = logging.getLogger("vademecum.subjects")

GROUP_CHUNK = 120  # page titles in one grouping turn
REGROUP_AFTER = 40  # new pages since the last grouping before their specialties are grouped again
PLACE_CHUNK = 40  # new topics in one placing turn
PLACE_PER_RUN = 200  # new topics placed by the model in one refresh; the rest wait for the next
CANDIDATES_PER_TOPIC = 5
MAX_GROUP_POINTS = pages.MAX_PAGE_POINTS


def _current_pages(connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, topic, title, specialty_id, point_ids, body_md, created_at FROM encyclopedia_entries WHERE status = 'current'"
    ).fetchall()
    found = []
    for row in rows:
        try:
            import json

            point_ids = {str(x) for x in json.loads(row["point_ids"] or "[]")}
        except ValueError:
            point_ids = set()
        found.append(
            {
                "id": row["id"],
                "topic": row["topic"],
                "title": row["title"],
                "specialty_id": row["specialty_id"],
                "points": point_ids,
                "edited": bool((row["body_md"] or "").strip()),
                "created_at": row["created_at"],
            }
        )
    return found


def _apply_group(connection, folder: Path | None, group: list[dict[str, Any]], title: str | None) -> int:
    """Fold a group into one page. Two pages the owner edited are never folded together."""
    edited = [page for page in group if page["edited"]]
    if len(edited) > 1:
        return 0
    if len(set().union(*(page["points"] for page in group))) > MAX_GROUP_POINTS:
        return 0
    # The owner's edited page stays; else the page with the most points, the oldest on a tie.
    survivor = edited[0] if edited else sorted(group, key=lambda page: (-len(page["points"]), page["created_at"]))[0]
    absorbed = [page["id"] for page in group if page["id"] != survivor["id"]]
    for entry_id in absorbed:
        page_files.remove_file(connection, folder, entry_id)
    return int(store.merge(connection, survivor["id"], absorbed, title=title)["merged"])


def check_groups(payload: dict[str, Any], handles: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Groups whose pages all map, two or more each, no page in two groups."""
    taken: set[str] = set()
    kept: list[tuple[str, list[str]]] = []
    for group in (payload.get("groups") or [])[: schemas.MAX_GROUPS]:
        if not isinstance(group, dict):
            continue
        ids = [handles[str(h)] for h in (group.get("pages") or []) if str(h) in handles]
        ids = [entry_id for entry_id in dict.fromkeys(ids) if entry_id not in taken]
        title = " ".join(str(group.get("title") or "").split())[:120]
        if len(ids) < 2 or not title:
            continue
        taken.update(ids)
        kept.append((title, ids))
    return kept


async def consolidate(database_path: Path, turn_factory: Any, *, folder: Path | None = None) -> dict[str, Any]:
    """Group the pages into subjects. The first time, every page; after that, once enough
    new pages have been made, the specialties they are in, so new topics that belong
    together still end on one page. Resumable: buckets done are recorded, and a bucket
    whose turn failed is tried again at the next refresh."""
    connection = connect(database_path)
    try:
        state = store.consolidated(connection) or {}
        since = str(state.get("since") or "")
        if state.get("complete"):
            newer = [page for page in _current_pages(connection) if page["created_at"] > str(state.get("at") or "")]
            if len(newer) < REGROUP_AFTER:
                return {"skipped": True}
            since = str(state.get("at") or "")
            state = {"since": since}
        done = set(state.get("done") or [])
        merged = int(state.get("merged") or 0)
        # By spelling first: no model turn needed.
        by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for page in _current_pages(connection):
            by_key[store.spelling_key(page["topic"])].append(page)
        for group in by_key.values():
            if len(group) > 1:
                merged += _apply_group(connection, folder, group, None)
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for page in _current_pages(connection):
            buckets[page["specialty_id"] or "none"].append(page)
    finally:
        connection.close()

    failed = 0
    if since:
        # A regrouping: only the specialties a new page is in.
        buckets = {key: members for key, members in buckets.items() if any(page["created_at"] > since for page in members)}
    for specialty, members in sorted(buckets.items()):
        members.sort(key=lambda page: page["title"].casefold())
        for start in range(0, len(members), GROUP_CHUNK):
            chunk = members[start : start + GROUP_CHUNK]
            key = f"{specialty}:{start}:{len(chunk)}"
            if key in done or len(chunk) < 2:
                done.add(key)
                continue
            handles = {f"g{index + 1}": page["id"] for index, page in enumerate(chunk)}
            try:
                runner = runner_for(turn_factory, "subjects", specialty)
                reply = await runner.run(
                    instructions=prompts.BASE_INSTRUCTIONS,
                    developer_instructions=prompts.GROUP_DEVELOPER,
                    prompt=prompts.group_prompt([(h, page["title"], len(page["points"])) for h, page in zip(handles, chunk, strict=True)]),
                    output_schema=schemas.GROUP_SCHEMA,
                )
            except BridgeError as exc:
                logger.info("subjects_group_failed category=%s", exc.category)
                failed += 1
                continue
            by_id = {page["id"]: page for page in chunk}
            connection = connect(database_path)
            try:
                for title, ids in check_groups(reply.payload, handles):
                    merged += _apply_group(connection, folder, [by_id[entry_id] for entry_id in ids], title)
                done.add(key)
                store.mark_consolidated(connection, {"complete": False, "done": sorted(done), "merged": merged, "since": since})
            finally:
                connection.close()
    connection = connect(database_path)
    try:
        store.mark_consolidated(connection, {"complete": failed == 0, "done": sorted(done), "merged": merged, "since": since})
    finally:
        connection.close()
    logger.info("subjects_consolidated merged=%d failed=%d", merged, failed)
    return {"merged": merged, "failed": failed}


def check_placements(payload: dict[str, Any], topics: dict[str, str], targets: dict[str, str]) -> dict[str, str | None]:
    """topic -> the target it goes with (a page's topic, or another new topic), or None
    for a page of its own. Unmapped handles are left out, and so asked again."""
    placed: dict[str, str | None] = {}
    for item in payload.get("placements") or []:
        if not isinstance(item, dict) or str(item.get("topic")) not in topics:
            continue
        topic = topics[str(item["topic"])]
        target = targets.get(str(item.get("page") or ""))
        placed[topic] = target if target and target != topic else None
    return placed


def _topic_points(connection, topic: str) -> set[str]:
    rows = connection.execute(
        "SELECT p.id FROM learning_point_topics t JOIN learning_points p ON p.id = t.learning_point_id"
        " WHERE t.topic = ? AND p.held = 0 AND p.review_state = 'machine_reviewed'",
        (topic,),
    ).fetchall()
    return {row["id"] for row in rows}


async def place_new_topics(database_path: Path, turn_factory: Any) -> dict[str, int]:
    """Put each new topic on a page before anything is written (feedback of 11 October):

    - by spelling, on the page it is another wording of;
    - not at all, for now, when every point it holds is already on a page: a page for it
      would only repeat them; it is looked at again when it brings a point of its own;
    - by the model, among the pages and the other new topics it might go with; new topics
      put together start one page between them;
    - on a page of its own when there is nothing it could go with.
    """
    from ..storage.encyclopedia import _words

    tally = {"by_spelling": 0, "by_model": 0, "own": 0, "covered": 0, "waiting": 0}
    connection = connect(database_path)
    try:
        deleted = set(pages.deleted_pages(connection))
        known = store.placed(connection)
        new = [t for t in pages.topics_with_points(connection) if t not in known and t.casefold() not in deleted]
        if not new:
            return tally
        index = pages.current_page_index(connection)
        covered = {point for page in index for point in page["point_ids"]}
        spelled: dict[str, str] = {}
        for page in index:
            for name in page["names"]:
                spelled.setdefault(store.spelling_key(name), page["topic"])
        fresh: list[tuple[str, set[str]]] = []
        with transaction(connection) as tx:
            for topic in new:
                key = store.spelling_key(topic)
                if key in spelled:
                    store.file_under(tx, topic, spelled[key])
                    tally["by_spelling"] += 1
                    continue
                points = _topic_points(connection, topic)
                if points <= covered:
                    tally["covered"] += 1
                    continue
                fresh.append((topic, points))
        # The new topics as places too, so related ones can share a page.
        pseudo = [
            {"id": f"new:{topic}", "title": topic, "topic": topic, "words": _words(topic), "names": {" ".join(topic.lower().split())}, "point_ids": []}
            for topic, _ in fresh
        ]
        asking: list[tuple[str, list[dict[str, Any]]]] = []
        with transaction(connection) as tx:
            for topic, _points in fresh:
                others = index + [entry for entry in pseudo if entry["topic"] != topic]
                candidates = pages.pages_matching(others, topic, limit=CANDIDATES_PER_TOPIC, floor=0.2)
                if not candidates:
                    store.own_page(tx, topic)  # nothing it could go with
                    tally["own"] += 1
                    continue
                asking.append((topic, candidates))
    finally:
        connection.close()

    tally["waiting"] = max(0, len(asking) - PLACE_PER_RUN)
    asking = asking[:PLACE_PER_RUN]
    decided: dict[str, str | None] = {}
    for start in range(0, len(asking), PLACE_CHUNK):
        chunk = asking[start : start + PLACE_CHUNK]
        topic_handles = {f"t{index + 1}": topic for index, (topic, _) in enumerate(chunk)}
        offered: dict[str, dict[str, Any]] = {}
        for _, candidates in chunk:
            for page in candidates:
                offered.setdefault(page["id"], page)
        targets = {f"g{index + 1}": page["topic"] for index, page in enumerate(offered.values())}
        described = [
            (f"g{index + 1}", f"{page['title']} (a new topic, no page yet)" if page["id"].startswith("new:") else page["title"])
            for index, page in enumerate(offered.values())
        ]
        try:
            runner = runner_for(turn_factory, "place", "topics")
            reply = await runner.run(
                instructions=prompts.BASE_INSTRUCTIONS,
                developer_instructions=prompts.PLACE_DEVELOPER,
                prompt=prompts.place_prompt(list(topic_handles.items()), described),
                output_schema=schemas.PLACE_SCHEMA,
            )
        except BridgeError as exc:
            logger.info("subjects_place_failed category=%s", exc.category)
            tally["waiting"] += len(chunk)
            continue
        decided.update(check_placements(reply.payload, topic_handles, targets))

    # New topics put with new topics share one page: each group's subject is the topic the
    # others lead to, or the page that topic was put on.
    new_topics = {topic for topic, _ in fresh}

    def root(topic: str, seen: frozenset[str] = frozenset()) -> str:
        target = decided.get(topic)
        if target is None or topic in seen or target not in new_topics:
            return target if target is not None and target not in new_topics else topic
        return root(target, seen | {topic})

    connection = connect(database_path)
    try:
        with transaction(connection) as tx:
            for topic, _ in asking:
                if topic not in decided:
                    tally["waiting"] += 1  # not answered: asked again next time
                    continue
                home = root(topic)
                if home == topic:
                    store.own_page(tx, topic)
                    tally["own"] += 1
                else:
                    if home in new_topics:
                        store.own_page(tx, home)
                    store.file_under(tx, topic, home)
                    tally["by_model"] += 1
    finally:
        connection.close()
    logger.info(
        "subjects_placed spelling=%d model=%d own=%d covered=%d waiting=%d",
        tally["by_spelling"],
        tally["by_model"],
        tally["own"],
        tally["covered"],
        tally["waiting"],
    )
    return tally
