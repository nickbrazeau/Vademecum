"""The podcast (ADR 0025): episodes, scripts, voices, audio.

Writing a script is a model turn on the Mac's own connection, with the
disclosure beside the button. Rendering is on-device speech; nothing of the
script leaves the Mac to be spoken. The audio is served from the records
directory by episode id; the script syncs and reads aloud anywhere.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import Field
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse

from ..db import connect
from ..model import podcasts as service
from ..storage import encyclopedia as pages
from ..storage import podcasts as store
from ..storage.sources import ConflictError
from .deps import get_settings_dep, get_connection, get_model_mode, get_source_dir
from .routes_sources import CLAUDE_DESTINATION, CODEX_DESTINATION
from .schemas import RecordId, Strict

router = APIRouter(prefix="/podcasts", tags=["podcasts"])

DISCLOSURE = (
    "Writing an episode sends the chosen pages -- their text and the points they rest on -- with the "
    "literature reviewed for them, related pages and case-series points already on this Mac, once, "
    "to write the script. In codex mode: " + CODEX_DESTINATION + " In claude mode: " + CLAUDE_DESTINATION
    + " Rendering the audio happens on this Mac, with its own voices or the on-device Kokoro voices; the script is spoken on-device and sent nowhere."
)
MAX_PAGES = 6


class EpisodeIn(Strict):
    entry_ids: list[RecordId] = []
    pick: str = "chosen"  # chosen | today | improvement | request
    title: str = ""
    # In the owner's own words (ADR 0027): written and voiced at once.
    request: str = Field(default="", max_length=300)


class ListenedIn(Strict):
    listened: bool = True


class RenderIn(Strict):
    voice_a: str = ""
    voice_b: str = ""


def _podcasts_dir(source_dir: Path) -> Path:
    return store.podcasts_dir(source_dir)


def _voices_dir(source_dir: Path) -> Path:
    """Kokoro's files (ADR 0027), in the data directory beside the attachments."""
    from ..model import kokoro

    return kokoro.voices_dir_for(source_dir.parent.parent)


def _here(data: dict[str, Any], source_dir: Path, episode: store.Episode) -> dict[str, Any]:
    """An episode as this machine can play it: the audio file stays on the Mac that
    rendered it, so a copy elsewhere says so instead of offering a player that
    cannot load."""
    present = bool(episode.audio_name) and (_podcasts_dir(source_dir) / episode.audio_name).is_file()
    elsewhere = bool(episode.audio_name) and not present and episode.listened_at is None
    return {**data, "has_audio": present, "audio_elsewhere": elsewhere, "progress": store.progress(_podcasts_dir(source_dir), episode.id)}


def _pages_for_request(connection: sqlite3.Connection, request: str, limit: int = 3) -> list[str]:
    """The encyclopedia pages a request names, matched as the Improvement Map matches topics."""
    return [page["id"] for page in pages.pages_matching(pages.current_page_index(connection), request, limit=limit)]


def _choose(connection: sqlite3.Connection, payload: EpisodeIn) -> list[str]:
    if payload.pick == "request":
        return _pages_for_request(connection, payload.request)
    if payload.pick == "today":
        page = pages.page_of_the_day(connection)
        return [page.id] if page else []
    if payload.pick == "improvement":
        from ..storage.flashcards import improvement_weights

        weights = improvement_weights(connection)
        chosen = [e.id for e in pages.list_entries(connection) if e.status == "current" and (e.topic.casefold() in weights["flagged"] or e.topic.casefold() in weights["below"])]
        if not chosen:
            page = pages.page_of_the_day(connection)
            chosen = [page.id] if page else []
        return chosen[:MAX_PAGES]
    return [entry_id for entry_id in payload.entry_ids if entry_id][:MAX_PAGES]


def _render_lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "podcast_render_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.podcast_render_lock = lock
    return lock


async def _produce(request: Request, episode_id: str, source_dir: Path) -> None:
    """Write the script and, for an episode asked for, voice it straight away (ADR 0027),
    reporting progress through the file beside the audio. One episode is voiced at a
    time; the next waits its turn."""
    workspace = request.state.workspace
    directory = _podcasts_dir(source_dir)
    store.set_progress(directory, episode_id, "writing")
    try:
        written = await service.write_script(workspace.database_path, episode_id, workspace.turn_factory)
        connection = connect(workspace.database_path)
        try:
            episode = store.get_episode(connection, episode_id)
        finally:
            connection.close()
        if not (episode.auto_render and episode.status == "scripted" and written.get("status") != "failed"):
            return
        store.set_progress(directory, episode_id, "queued")
        lock = _render_lock(request)
        async with lock:
            voices = _voices_dir(source_dir)
            await service.render(
                workspace.database_path, episode_id, directory, service.default_voices(voices),
                synth=getattr(request.app.state, "podcast_synth", None),
                encode=getattr(request.app.state, "podcast_encode", None) or service.afconvert_encode,
                voices_dir=voices,
            )
    finally:
        store.clear_progress(directory, episode_id)


def _start_script(request: Request, episode_id: str, source_dir: Path) -> None:
    task = asyncio.create_task(_produce(request, episode_id, source_dir))
    request.app.state.podcast_tasks = [t for t in getattr(request.app.state, "podcast_tasks", []) if not t.done()] + [task]


@router.get("")
def list_episodes(
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
    source_dir: Path = Depends(get_source_dir),
) -> dict[str, Any]:
    waiting = len(store.waiting(connection))
    return {
        "episodes": [_here(e.as_dict(), source_dir, e) for e in store.list_episodes(connection)],
        "waiting": waiting,
        "max_hosted": store.MAX_HOSTED,
        "can_write": mode in ("codex", "claude"),
        "can_render": mode in ("codex", "claude") and bool(service.available_voices(_voices_dir(source_dir))),
        "note": "" if mode in ("codex", "claude") else service.NEEDS_MAC,
        "disclosure": DISCLOSURE,
    }


@router.get("/voices")
def voices(source_dir: Path = Depends(get_source_dir)) -> dict[str, Any]:
    directory = _voices_dir(source_dir)
    return {"voices": service.available_voices(directory), "default": service.default_voices(directory)}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create(
    payload: EpisodeIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
    source_dir: Path = Depends(get_source_dir),
) -> dict[str, Any]:
    if mode not in ("codex", "claude"):
        raise ConflictError("needs_model", service.NEEDS_MODEL)
    if len(store.waiting(connection)) >= store.MAX_HOSTED:
        raise ConflictError(
            "full",
            f"There are already {store.MAX_HOSTED} episodes waiting to be heard. Listen to one, or remove one, to make room.",
        )
    asked = " ".join(payload.request.split())
    if payload.pick == "request" and not asked:
        raise ConflictError("no_request", "Say what the episode should be about.")
    entry_ids = _choose(connection, payload)
    if not entry_ids and payload.pick != "request":
        raise ConflictError("no_pages", "Choose at least one encyclopedia page, or compile the encyclopedia first.")
    titles = [pages.get_entry(connection, entry_id).title for entry_id in entry_ids]
    title = payload.title.strip() or (asked[:120] if payload.pick == "request" else ", ".join(titles)[:120])
    episode = store.create_episode(
        connection, title=title, entry_ids=entry_ids, request=asked if payload.pick == "request" else "", auto_render=payload.pick == "request"
    )
    _start_script(request, episode.id, source_dir)
    note = "Writing the script now; it is voiced as soon as it is written." if episode.auto_render else "Writing the script now."
    return {**episode.as_dict(), "note": note, "disclosure": DISCLOSURE}


@router.get("/{episode_id}")
def read(episode_id: str, connection: sqlite3.Connection = Depends(get_connection), source_dir: Path = Depends(get_source_dir)) -> dict[str, Any]:
    episode = store.get_episode(connection, episode_id)
    return _here(episode.as_dict(), source_dir, episode)


@router.post("/{episode_id}/script", status_code=status.HTTP_202_ACCEPTED)
async def rewrite(
    episode_id: str,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    mode: str = Depends(get_model_mode),
    source_dir: Path = Depends(get_source_dir),
) -> dict[str, Any]:
    store.get_episode(connection, episode_id)
    if mode not in ("codex", "claude"):
        raise ConflictError("needs_model", service.NEEDS_MODEL)
    _start_script(request, episode_id, source_dir)
    return {"started": True}


@router.post("/{episode_id}/render")
async def render(
    episode_id: str,
    payload: RenderIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
    mode: str = Depends(get_model_mode),
) -> dict[str, Any]:
    episode = store.get_episode(connection, episode_id)
    if mode not in ("codex", "claude"):
        raise ConflictError("needs_mac", service.NEEDS_MAC)
    if not episode.script:
        raise ConflictError("no_script", "This episode has no script yet.")
    directory = _voices_dir(source_dir)
    known = {voice["name"] for voice in service.available_voices(directory)}
    chosen = {"A": payload.voice_a if payload.voice_a in known else "", "B": payload.voice_b if payload.voice_b in known else ""}
    workspace = request.state.workspace
    # None: the real voices, rendered in a process of their own. Tests substitute both.
    synth = getattr(request.app.state, "podcast_synth", None)
    encode = getattr(request.app.state, "podcast_encode", None) or service.afconvert_encode
    # One episode voiced at a time: a second waits rather than halving both.
    lock = _render_lock(request)
    async with lock:
        result = await service.render(
            workspace.database_path, episode_id, _podcasts_dir(source_dir), chosen, synth=synth, encode=encode, voices_dir=directory
        )
    if "error" in result:
        raise ConflictError("no_script", result["error"])
    return result


@router.get("/{episode_id}/audio")
def audio(episode_id: str, connection: sqlite3.Connection = Depends(get_connection), source_dir: Path = Depends(get_source_dir)) -> FileResponse:
    episode = store.get_episode(connection, episode_id)
    if not episode.audio_name:
        raise ConflictError("no_audio", "This episode has not been rendered on the Mac.")
    path = _podcasts_dir(source_dir) / episode.audio_name
    if not path.is_file():
        raise ConflictError("no_audio", "The audio file is not on this machine; it stays on the Mac that rendered it.")
    return FileResponse(path, media_type="audio/mp4", filename=f"{episode.title[:60] or 'episode'}.m4a")


@router.post("/{episode_id}/listened")
def listened(
    episode_id: str,
    payload: ListenedIn,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict[str, Any]:
    """Finished, on any device: the episode drops to the archive (ADR 0026) and its
    audio is deleted to save space (ADR 0027). Local; sends nothing."""
    store.set_listened(connection, episode_id, payload.listened)
    if payload.listened:
        store.retire_audio(connection, _podcasts_dir(source_dir), role=get_settings_dep(request).sync_role_name)
    episode = store.get_episode(connection, episode_id)
    return _here(episode.as_dict(), source_dir, episode)


@router.delete("/{episode_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete(episode_id: str, connection: sqlite3.Connection = Depends(get_connection), source_dir: Path = Depends(get_source_dir)) -> None:
    audio_name = store.delete_episode(connection, episode_id)
    if audio_name:
        try:
            (_podcasts_dir(source_dir) / audio_name).unlink(missing_ok=True)
        except OSError:
            pass
