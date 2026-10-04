"""The learner's source folder as the way material comes in (ADR 0012).

One visible folder, chosen at install. Inside it, ``piles/`` holds three tier
folders, and the tier is the rating::

    <source folder>/piles/highconfidence/<pile>/<files>
    <source folder>/piles/mediumconfidence/<pile>/<files>
    <source folder>/piles/lowconfidence/<pile>/<files>

A subfolder inside a tier is a pile at that confidence, named after the
folder. Moving a pile's folder to another tier changes its rating on the next
scan: the folder is the rating, so there is nothing else to keep in step. A
loose file inside a tier folder goes into a pile named after the tier; a loose
file directly under ``piles/`` goes into Unsorted, at Medium. Nothing outside
``piles/`` is read.

A file becomes a source through exactly the intake the web app's upload uses:
the same detection, extraction, content-addressed storage and idempotence.

What a scan does not do: delete. A file removed from the folder leaves its
source in place; removing a source is an explicit act with a confirmation,
because losing learning material because a file was tidied away is the
failure this rule prevents.

Nothing here logs a filename. The report names files; the log counts them.
"""

from __future__ import annotations

import re
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..storage import images as image_store
from ..storage import piles as pile_store
from ..storage import sources as store
from . import UnsupportedUpload, detect, extract_all, safe_display_name
from .limits import MAX_UPLOAD_BYTES


PILES_DIRNAME = "piles"
UNSORTED = "Unsorted"
DEFAULT_TIER = "mid"

# The folder names the installer creates, and what each means.
TIER_FOLDERS = {"high": "highconfidence", "mid": "mediumconfidence", "low": "lowconfidence"}
TIER_TITLES = {"high": "High confidence", "mid": "Medium confidence", "low": "Low confidence"}

# Spellings a person might use for the same thing, letters only, lowercase.
_TIER_SPELLINGS = {
    "highconfidence": "high",
    "high": "high",
    "mediumconfidence": "mid",
    "medium": "mid",
    "midconfidence": "mid",
    "mid": "mid",
    "lowconfidence": "low",
    "low": "low",
}

FOLDER_DESCRIPTION = "Made from a folder in your Vademecum source folder."

# Files the Finder and Office leave behind, and anything a person hid.
SKIP_PREFIXES = (".", "~$")

# A file still being written -- a download in progress, a save half done --
# is left for the next scan rather than read half-way.
SETTLE_SECONDS = 2.0

README_NAME = "README.txt"
README = """This is your Vademecum source folder.

Put the material you learn from inside "piles". Each tier folder is a
confidence rating -- how much you trust the material, not how well you know
it -- and each folder inside a tier is a pile:

  piles/highconfidence/<pile name>/     e.g. a guideline you rely on
  piles/mediumconfidence/<pile name>/   e.g. a lecture from a course
  piles/lowconfidence/<pile name>/      e.g. notes you are unsure about

Vademecum reads PDF, PowerPoint (.pptx), Word (.docx), text, Markdown and
pictures (.png, .jpg). Pictures inside files are kept too, and pages with no
text layer are read on this Mac. Schematics your assistant draws are filed
under "schematics" here. Open this folder as a project in Codex and ask for
Vademecum: the dashboard opens in the conversation (AGENTS.md says so).

Encyclopedia pages are kept as Markdown files under "encyclopedia" here.
Edit one in any editor and the change comes back into Vademecum.
It reads the folder every few seconds while it is running, and whenever you
ask your assistant to sync. Move a pile's folder to another tier to change
its rating. Removing a file here does not remove it from Vademecum; ask your
assistant to remove the source.

Vademecum is educational. Never put anything with a patient's identifiers in
this folder.
"""


# Codex reads AGENTS.md from the folder a project is opened in. A learner who
# opens their source folder as a Codex project gets the dashboard first.
AGENTS_NAME = "AGENTS.md"
AGENTS = """# Vademecum

This folder is a Vademecum source folder (see README.txt). Vademecum is the
owner's private clinical-learning workspace; its tools are the `vademecum`
MCP server.

At the start of a conversation here, call `open_vademecum` first so the
dashboard is in view, then answer in a sentence or two. Material comes in
through the `piles` folders; never put a patient's identifiers in them.
Everything is educational, never clinical advice.
"""


def tier_of(name: str) -> str | None:
    """The tier a folder name means, or None for a folder that is a pile."""
    return _TIER_SPELLINGS.get(re.sub(r"[^a-z]", "", name.lower()))


def scaffold(folder: Path) -> list[str]:
    """Create the layout a learner is meant to see. Returns what was created."""
    created: list[str] = []
    piles = folder / PILES_DIRNAME
    for tier in ("high", "mid", "low"):
        directory = piles / TIER_FOLDERS[tier]
        if not directory.is_dir():
            directory.mkdir(parents=True, exist_ok=True)
            created.append(f"{PILES_DIRNAME}/{TIER_FOLDERS[tier]}")
    readme = folder / README_NAME
    if not readme.exists():
        readme.write_text(README, encoding="utf-8")
        created.append(README_NAME)
    agents = folder / AGENTS_NAME
    if not agents.exists():
        agents.write_text(AGENTS, encoding="utf-8")
        created.append(AGENTS_NAME)
    return created


def scan_folder(
    connection: sqlite3.Connection,
    *,
    source_dir: Path,
    folder: Path,
    now: Callable[[], float] = time.time,
    settle_seconds: float = SETTLE_SECONDS,
) -> dict[str, Any]:
    """Bring the folder's files into their piles. Idempotent; returns a report."""
    piles_dir = folder / PILES_DIRNAME
    report: dict[str, Any] = {
        "folder_present": piles_dir.is_dir(),
        "piles_created": [],
        "retiered": [],
        "stored": [],
        "already_present": 0,
        "waiting": 0,
        "rejected": [],
    }
    if not piles_dir.is_dir():
        return report

    known = {pile.title.casefold(): pile for pile in pile_store.list_piles(connection)}

    def pile_for(title: str, tier: str) -> str:
        title = title.strip()[:200] or UNSORTED
        existing = known.get(title.casefold())
        if existing is not None:
            if existing.tier != tier:
                updated = pile_store.update_pile(connection, existing.id, tier=tier)
                known[title.casefold()] = updated
                report["retiered"].append({"pile": updated.title, "confidence": tier})
            return existing.id
        created = pile_store.create_pile(connection, title=title, tier=tier, description=FOLDER_DESCRIPTION)
        known[title.casefold()] = created
        report["piles_created"].append({"pile": title, "confidence": tier})
        return created.id

    def take_all(paths: list[Path], pile_id: str, title: str) -> None:
        for path in sorted(paths, key=lambda item: item.name.casefold()):
            _take(connection, report, source_dir=source_dir, path=path, pile_id=pile_id, pile_title=title, now=now, settle_seconds=settle_seconds)

    for entry in sorted(piles_dir.iterdir(), key=lambda path: path.name.casefold()):
        if entry.name.startswith(SKIP_PREFIXES):
            continue
        if entry.is_file():
            take_all([entry], pile_for(UNSORTED, DEFAULT_TIER), UNSORTED)
            continue
        if not entry.is_dir():
            continue
        tier = tier_of(entry.name)
        if tier is None:
            # A pile folder placed directly under piles/: kept, at Medium, so
            # nothing a person dropped in the wrong place is silently ignored.
            take_all([p for p in entry.iterdir() if p.is_file()], pile_for(entry.name, DEFAULT_TIER), entry.name.strip())
            continue
        loose = [p for p in entry.iterdir() if p.is_file() and not p.name.startswith(SKIP_PREFIXES)]
        if loose:
            take_all(loose, pile_for(TIER_TITLES[tier], tier), TIER_TITLES[tier])
        for pile_dir in sorted((p for p in entry.iterdir() if p.is_dir()), key=lambda p: p.name.casefold()):
            if pile_dir.name.startswith(SKIP_PREFIXES):
                continue
            take_all([p for p in pile_dir.iterdir() if p.is_file()], pile_for(pile_dir.name, tier), pile_dir.name.strip())
    return report


def _take(
    connection: sqlite3.Connection,
    report: dict[str, Any],
    *,
    source_dir: Path,
    path: Path,
    pile_id: str,
    pile_title: str,
    now: Callable[[], float],
    settle_seconds: float,
) -> None:
    if path.name.startswith(SKIP_PREFIXES):
        return
    try:
        stat = path.stat()
    except OSError:
        return
    if now() - stat.st_mtime < settle_seconds:
        report["waiting"] += 1
        return
    display_name = safe_display_name(path.name)
    if stat.st_size > MAX_UPLOAD_BYTES:
        report["rejected"].append(
            {
                "filename": display_name,
                "pile": pile_title,
                "message": (
                    f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, which is "
                    "more than Vademecum stores. Split it and try again."
                ),
            }
        )
        return
    try:
        payload = path.read_bytes()
    except OSError:
        return
    try:
        detected = detect(display_name, payload)
    except UnsupportedUpload as exc:
        report["rejected"].append({"filename": display_name, "pile": pile_title, "message": exc.message})
        return
    sha256 = store.digest(payload)
    if store.has_source(connection, pile_id=pile_id, sha256=sha256):
        report["already_present"] += 1
        return
    extraction, images = extract_all(detected.kind, payload)
    store.write_original(source_dir, store.stored_name_for(sha256, detected.kind, detected.media_type), payload, sha256)
    pile = pile_store.get_pile(connection, pile_id)
    stored = store.store_upload(
        connection,
        pile_id=pile_id,
        display_name=display_name,
        media_type=detected.media_type,
        sha256=sha256,
        byte_size=len(payload),
        confidence=pile.tier,
        extraction=extraction,
    )
    if stored.outcome != "duplicate":
        image_store.replace_images(
            connection, source_id=stored.source.id, images=images, directory=source_dir.parent / "images"
        )
    report["stored"].append(
        {
            "filename": display_name,
            "pile": pile_title,
            "confidence": pile.tier,
            "outcome": stored.outcome,
            "status": stored.source.status,
            "status_detail": stored.source.status_detail,
            "warnings": list(stored.warnings),
            "images": len(images) if stored.outcome != "duplicate" else 0,
        }
    )
