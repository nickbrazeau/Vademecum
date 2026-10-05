"""The podcast generator (ADR 0025): a script from pages, audio made on this Mac.

The script is one model turn on the Mac's own connection: the chosen pages
go in, two hosts' lines come out, saying only what the pages say. The audio
is made on-device with the Mac's own speech synthesiser, one voice per host,
line by line, joined and encoded to a small AAC file beside the other stored
files. Nothing of the script leaves the machine to be spoken; no key, no
service. Anywhere else, the browser reads the script aloud itself.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..appserver.errors import BridgeError
from ..db import connect
from ..storage import encyclopedia as pages
from ..storage import podcasts as store
from . import kokoro, prompts, schemas
from .speakable import speakable
from .socratic import further_context, page_material

logger = logging.getLogger("vademecum.podcasts")

DEFAULT_VOICES = {"A": "Samantha", "B": "Daniel"}
SAMPLE_RATE = 24000  # Kokoro's own rate; `say` renders at any rate asked
PAUSE_SECONDS = 0.45
RENDER_TIMEOUT_SECONDS = 600
NEEDS_MODEL = "Writing a script needs the Mac's own model connection (codex or claude mode)."
NEEDS_MAC = "Rendering audio happens on the Mac, with its own speech voices; this Vademecum can read the script aloud in the browser instead."

Synth = Callable[[str, str, Path], None]
Encode = Callable[[Path, Path], None]


def _runner(turn_factory: Any, episode_id: str):
    scoped = getattr(turn_factory, "scoped", None)
    if callable(scoped):
        return scoped("podcast", episode_id)()
    return turn_factory()


# --- the script ----------------------------------------------------------------------


_HANDLES = re.compile(r"\s*\[(?:p|r)\d+(?:\s*,\s*(?:p|r)\d+)*\]")


def spoken(text: str) -> str:
    """Words to be spoken: the page's citation handles are for the page, not the ear."""
    return " ".join(_HANDLES.sub("", str(text)).split())


def check_script(payload: dict[str, Any]) -> dict[str, Any]:
    lines = [
        {"speaker": str(line.get("speaker") or "A"), "text": spoken(line.get("text") or "")}
        for line in (payload.get("lines") or [])[: schemas.MAX_PODCAST_LINES]
        if isinstance(line, dict) and spoken(line.get("text") or "")
    ]
    return {
        "title": spoken(payload.get("title") or "")[:120],
        "lines": lines,
        "takeaways": [spoken(t)[:200] for t in (payload.get("takeaways") or []) if isinstance(t, str) and spoken(t)][:5],
    }


async def write_script(database_path: Path, episode_id: str, turn_factory: Any) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        episode = store.get_episode(connection, episode_id)
        texts: list[str] = []
        sources: list[dict[str, str]] = []
        for entry_id in episode.entry_ids:
            try:
                entry = pages.get_entry(connection, entry_id)
            except Exception:  # noqa: BLE001 - a page removed since is simply not in the episode
                continue
            context = further_context(connection, entry)
            texts.append(page_material(connection, entry) + (f"\n\nFURTHER CONTEXT:\n{context}" if context else ""))
            sources.append({"kind": "page", "title": entry.title, "entry_id": entry.id})
            for record in pages.records_for_entry(connection, entry.id):
                if record["retracted"] or record["is_notice"]:
                    continue
                sources.append(
                    {"kind": "paper", "title": record["title"], "journal": record["journal"], "year": (record["published_on"] or "")[:4], "pmid": record["pmid"]}
                )
        # The sources are the server's own record of what went in, not the model's claim.
        store.set_sources(connection, episode_id, sources)
    finally:
        connection.close()
    if not texts:
        connection = connect(database_path)
        try:
            return store.set_failed(connection, episode_id, "None of the chosen pages exists any more.", keep_script=False).as_dict()
        finally:
            connection.close()
    try:
        runner = _runner(turn_factory, episode_id)
        reply = await runner.run(
            instructions=prompts.BASE_INSTRUCTIONS,
            developer_instructions=prompts.PODCAST_DEVELOPER,
            prompt=prompts.podcast_prompt(texts),
            output_schema=schemas.PODCAST_SCHEMA,
        )
    except BridgeError as exc:
        logger.info("podcast_script_failed category=%s", exc.category)
        connection = connect(database_path)
        try:
            return store.set_failed(connection, episode_id, f"The model connection failed ({exc.category}). Try again.", keep_script=False).as_dict()
        finally:
            connection.close()
    checked = check_script(reply.payload)
    connection = connect(database_path)
    try:
        if not checked["lines"]:
            return store.set_failed(connection, episode_id, "The model returned no lines.", keep_script=False).as_dict()
        episode = store.set_script(connection, episode_id, title=checked["title"] or episode.title, script=checked["lines"], takeaways=checked["takeaways"])
    finally:
        connection.close()
    logger.info("podcast_scripted lines=%d words=%d", len(checked["lines"]), sum(len(line["text"].split()) for line in checked["lines"]))
    return episode.as_dict()


# --- the audio -----------------------------------------------------------------------


def default_voices(voices_dir: Path | None = None) -> dict[str, str]:
    """Kokoro's two best voices once installed (ADR 0027); the Mac's own otherwise."""
    return dict(kokoro.DEFAULTS) if kokoro.ready(voices_dir) else dict(DEFAULT_VOICES)


def available_voices(voices_dir: Path | None = None) -> list[dict[str, str]]:
    """Kokoro's natural voices first, when installed, then the Mac's English
    voices by name; empty where there is no synthesiser."""
    natural = kokoro.listed(voices_dir)
    if shutil.which("say") is None:
        return natural
    return natural + _say_voices()


def _say_voices() -> list[dict[str, str]]:
    try:
        listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    voices: list[dict[str, str]] = []
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        locale_index = next((i for i, part in enumerate(parts) if part.startswith("en_")), None)
        if locale_index is None:
            continue
        name = " ".join(parts[:locale_index])
        voices.append({"name": name, "label": name, "locale": parts[locale_index]})
    return voices


def say_synth(text: str, voice: str, target: Path) -> None:
    subprocess.run(
        ["say", "-v", voice, "-o", str(target), "--file-format=WAVE", f"--data-format=LEI16@{SAMPLE_RATE}", text],
        check=True,
        timeout=RENDER_TIMEOUT_SECONDS,
        capture_output=True,
    )


def synth_for(voices_dir: Path | None) -> Synth:
    """Kokoro for a Kokoro voice, the Mac's `say` for any other."""

    def speak(text: str, voice: str, target: Path) -> None:
        if voice.startswith(kokoro.PREFIX) and voices_dir is not None and kokoro.ready(voices_dir):
            kokoro.synth(voices_dir, text, voice, target, SAMPLE_RATE)
        else:
            say_synth(text, DEFAULT_VOICES["A"] if voice.startswith(kokoro.PREFIX) else voice, target)

    return speak


def afconvert_encode(source: Path, target: Path) -> None:
    subprocess.run(["afconvert", str(source), str(target), "-f", "m4af", "-d", "aac", "-b", "64000"], check=True, timeout=RENDER_TIMEOUT_SECONDS, capture_output=True)


def render_lines(lines: list[dict[str, str]], voices: dict[str, str], target: Path, *, synth: Synth = say_synth, encode: Encode = afconvert_encode) -> tuple[int, int]:
    """Speak each line in its host's voice, join them with a breath between, encode. Returns bytes and seconds."""
    with tempfile.TemporaryDirectory(prefix="vademecum-podcast-") as scratch:
        folder = Path(scratch)
        joined = folder / "episode.wav"
        frames = 0
        with wave.open(str(joined), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            pause = b"\x00\x00" * int(SAMPLE_RATE * PAUSE_SECONDS)
            for index, line in enumerate(lines):
                piece = folder / f"line-{index}.wav"
                synth(speakable(line["text"]), voices.get(line["speaker"], DEFAULT_VOICES["A"]), piece)
                with wave.open(str(piece), "rb") as part:
                    if part.getnchannels() != 1 or part.getsampwidth() != 2 or part.getframerate() != SAMPLE_RATE:
                        raise RuntimeError("the synthesiser answered in an unexpected format")
                    out.writeframes(part.readframes(part.getnframes()))
                    frames += part.getnframes()
                out.writeframes(pause)
                frames += len(pause) // 2
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        encode(joined, target)
    return target.stat().st_size, int(round(frames / SAMPLE_RATE))


async def render(
    database_path: Path,
    episode_id: str,
    podcasts_dir: Path,
    voices: dict[str, str],
    *,
    synth: Synth | None = None,
    encode: Encode = afconvert_encode,
    voices_dir: Path | None = None,
) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        episode = store.get_episode(connection, episode_id)
    finally:
        connection.close()
    if not episode.script:
        return {"error": "This episode has no script yet."}
    fallback = default_voices(voices_dir)
    chosen = {"A": voices.get("A") or fallback["A"], "B": voices.get("B") or fallback["B"]}
    if synth is None:
        synth = synth_for(voices_dir)
    audio_name = f"{episode.id}.m4a"
    target = podcasts_dir / audio_name
    try:
        size, seconds = await asyncio.to_thread(render_lines, [dict(line) for line in episode.script], chosen, target, synth=synth, encode=encode)
    except Exception as exc:  # noqa: BLE001 - recorded on the episode
        logger.error("podcast_render_failed error=%s", type(exc).__name__)
        connection = connect(database_path)
        try:
            return store.set_failed(connection, episode_id, "Rendering the audio failed on this Mac. The script is unchanged.").as_dict()
        finally:
            connection.close()
    connection = connect(database_path)
    try:
        rendered = store.set_rendered(connection, episode_id, audio_name=audio_name, audio_bytes=size, duration_seconds=seconds, voices=chosen)
    finally:
        connection.close()
    logger.info("podcast_rendered seconds=%d bytes=%d", seconds, size)
    return rendered.as_dict()
