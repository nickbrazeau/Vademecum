"""Podcast episodes (ADR 0025): a script written from pages, and its audio.

An episode names the pages it was written from, holds its script as spoken
lines with a speaker each, its take-homes, the voices chosen, and -- once
rendered on the Mac -- the audio file's name beside the other stored files.
The script syncs; the audio stays where it was made, and anywhere else the
browser's own speech can read the script aloud.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from ..db import transaction
from .common import NotFoundError, new_id, utc_now

STATUSES = ("draft", "scripted", "rendered", "failed")
MAX_LINES = 80
MAX_LINE_CHARS = 700
SPEAKERS = ("A", "B")
PODCASTS_DIRNAME = "attachments/podcasts"


@dataclass(frozen=True)
class Episode:
    id: str
    title: str
    status: str
    status_detail: str
    entry_ids: tuple[str, ...]
    script: tuple[dict[str, str], ...]
    takeaways: tuple[str, ...]
    voices: dict[str, str]
    audio_name: str
    audio_bytes: int
    duration_seconds: int
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["entry_ids"] = list(self.entry_ids)
        data["script"] = [dict(line) for line in self.script]
        data["takeaways"] = list(self.takeaways)
        data["voices"] = dict(self.voices)
        data["words"] = sum(len(line["text"].split()) for line in self.script)
        data["has_audio"] = bool(self.audio_name)
        return data


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        data = json.loads(value or "")
    except ValueError:
        return fallback
    return data if isinstance(data, type(fallback)) else fallback


def _episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        status_detail=row["status_detail"],
        entry_ids=tuple(str(x) for x in _loads(row["entry_ids"], [])),
        script=tuple(
            {"speaker": str(line.get("speaker") or "A"), "text": str(line.get("text") or "")}
            for line in _loads(row["script"], [])
            if isinstance(line, dict)
        ),
        takeaways=tuple(str(x) for x in _loads(row["takeaways"], []) if isinstance(x, str)),
        voices={str(k): str(v) for k, v in _loads(row["voices"], {}).items()},
        audio_name=row["audio_name"],
        audio_bytes=int(row["audio_bytes"]),
        duration_seconds=int(row["duration_seconds"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def create_episode(connection: sqlite3.Connection, *, title: str, entry_ids: list[str]) -> Episode:
    episode_id = new_id("pod")
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO podcast_episodes (id, title, status, status_detail, entry_ids, script, takeaways, voices, audio_name, audio_bytes, duration_seconds, created_at, updated_at)"
            " VALUES (?, ?, 'draft', '', ?, '[]', '[]', '{}', '', 0, 0, ?, ?)",
            (episode_id, title[:120], json.dumps(list(dict.fromkeys(entry_ids))[:12]), now, now),
        )
    return get_episode(connection, episode_id)


def get_episode(connection: sqlite3.Connection, episode_id: str) -> Episode:
    row = connection.execute("SELECT * FROM podcast_episodes WHERE id = ?", (episode_id,)).fetchone()
    if row is None:
        raise NotFoundError("podcast episode", episode_id)
    return _episode(row)


def list_episodes(connection: sqlite3.Connection, *, limit: int = 50) -> list[Episode]:
    rows = connection.execute("SELECT * FROM podcast_episodes ORDER BY created_at DESC, id LIMIT ?", (max(1, int(limit)),)).fetchall()
    return [_episode(row) for row in rows]


def set_script(connection: sqlite3.Connection, episode_id: str, *, title: str, script: list[dict[str, str]], takeaways: list[str]) -> Episode:
    lines = [
        {"speaker": line["speaker"] if line.get("speaker") in SPEAKERS else "A", "text": " ".join(str(line.get("text") or "").split())[:MAX_LINE_CHARS]}
        for line in script[:MAX_LINES]
        if str(line.get("text") or "").strip()
    ]
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE podcast_episodes SET title = ?, status = 'scripted', status_detail = '', script = ?, takeaways = ?,"
            " audio_name = '', audio_bytes = 0, duration_seconds = 0, updated_at = ? WHERE id = ?",
            (title[:120] or "Untitled episode", json.dumps(lines), json.dumps([t[:200] for t in takeaways[:5]]), now, episode_id),
        )
    return get_episode(connection, episode_id)


def set_rendered(connection: sqlite3.Connection, episode_id: str, *, audio_name: str, audio_bytes: int, duration_seconds: int, voices: dict[str, str]) -> Episode:
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE podcast_episodes SET status = 'rendered', status_detail = '', audio_name = ?, audio_bytes = ?, duration_seconds = ?, voices = ?, updated_at = ? WHERE id = ?",
            (audio_name, int(audio_bytes), int(duration_seconds), json.dumps(voices), now, episode_id),
        )
    return get_episode(connection, episode_id)


def set_failed(connection: sqlite3.Connection, episode_id: str, detail: str, *, keep_script: bool = True) -> Episode:
    now = utc_now()
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE podcast_episodes SET status = ?, status_detail = ?, updated_at = ? WHERE id = ?",
            ("scripted" if keep_script and get_episode(connection, episode_id).script else "failed", detail[:500], now, episode_id),
        )
    return get_episode(connection, episode_id)


def delete_episode(connection: sqlite3.Connection, episode_id: str) -> str:
    """Remove the row; returns the audio name so the caller can remove the file."""
    episode = get_episode(connection, episode_id)
    with transaction(connection) as tx:
        tx.execute("DELETE FROM podcast_episodes WHERE id = ?", (episode_id,))
    return episode.audio_name
