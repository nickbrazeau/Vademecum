"""Pictures in a document, kept beside its text (ADR 0013).

A figure in a PDF, a photograph on a slide, a diagram in a Word file: text
extraction loses all of them, and a scanned page has nothing else. This module
pulls them out so the learner's assistant can look at them -- it is the
assistant that learns from a picture; Vademecum only keeps it and says where
it came from.

Two kinds of picture are kept:

* **embedded** -- an image object inside the file, with the page, slide or
  section it belongs to;
* **rendered** -- a whole PDF page drawn as a bitmap, for pages that had no
  text layer, so a scan or an image export can still be read and shown.

Everything is normalised through Pillow into PNG or JPEG, bounded in count,
pixels and bytes, and deduplicated by digest within one document. Icons and
rules (tiny images) are dropped: a 12-pixel bullet is not a figure.
"""

from __future__ import annotations

import hashlib
import io
import logging
import zipfile
from dataclasses import dataclass

from .extract import P_NS, R_NS, REL_SLIDE, _read_part, _relationships, _safe_members
from .limits import MAX_ARCHIVE_MEMBER_BYTES

logger = logging.getLogger("vademecum.ingest")

MIN_SIDE = 64
MIN_PIXELS = 12_000
MAX_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_PER_UNIT = 8
MAX_PER_DOCUMENT = 200

REL_IMAGE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"

ORIGIN_EMBEDDED = "embedded"
ORIGIN_RENDERED = "rendered"


@dataclass(frozen=True)
class ExtractedImage:
    unit_index: int
    locator: str
    media_type: str
    data: bytes
    width: int
    height: int
    sha256: str
    origin: str = ORIGIN_EMBEDDED


def extract_images(
    kind: str,
    payload: bytes,
    *,
    rendered_pages: dict[int, bytes] | None = None,
) -> list[ExtractedImage]:
    """Every picture worth keeping, in document order.

    ``rendered_pages`` are page bitmaps the OCR step already produced for
    text-less pages; they are kept as rendered images without drawing again.
    """
    try:
        if kind == "pdf":
            found = _pdf_images(payload)
        elif kind == "pptx":
            found = _ooxml_images(payload, "pptx")
        elif kind == "docx":
            found = _ooxml_images(payload, "docx")
        elif kind == "image":
            normalised = _normalise(payload)
            found = (
                []
                if normalised is None
                else [
                    ExtractedImage(
                        1, "image", normalised[0], normalised[1], normalised[2], normalised[3],
                        hashlib.sha256(normalised[1]).hexdigest(),
                    )
                ]
            )
        else:
            found = []
    except Exception:  # noqa: BLE001 - pictures are a bonus; text extraction already ran
        logger.info("image_extraction_failed kind=%s", kind)
        found = []
    for page_number, png in sorted((rendered_pages or {}).items()):
        normalised = _normalise(png)
        if normalised is not None:
            found.append(
                ExtractedImage(
                    page_number,
                    f"page {page_number} (rendered)",
                    normalised[0],
                    normalised[1],
                    normalised[2],
                    normalised[3],
                    hashlib.sha256(normalised[1]).hexdigest(),
                    ORIGIN_RENDERED,
                )
            )
    seen: set[str] = set()
    kept: list[ExtractedImage] = []
    for image in found:
        if image.sha256 in seen:
            continue
        seen.add(image.sha256)
        kept.append(image)
        if len(kept) >= MAX_PER_DOCUMENT:
            break
    return kept


# --- PDF --------------------------------------------------------------------------


def _pdf_images(payload: bytes) -> list[ExtractedImage]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    if reader.is_encrypted and not reader.decrypt(""):
        return []
    found: list[ExtractedImage] = []
    for index, page in enumerate(reader.pages, start=1):
        per_page = 0
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 - a page whose images cannot be listed has none for us
            continue
        for image in images:
            if per_page >= MAX_PER_UNIT:
                break
            try:
                raw = image.data
            except Exception:  # noqa: BLE001
                continue
            normalised = _normalise(raw)
            if normalised is None:
                continue
            media_type, data, width, height = normalised
            found.append(
                ExtractedImage(index, f"page {index}", media_type, data, width, height, hashlib.sha256(data).hexdigest())
            )
            per_page += 1
    return found


# --- PPTX and DOCX ------------------------------------------------------------------


def _ooxml_images(payload: bytes, kind: str) -> list[ExtractedImage]:
    found: list[ExtractedImage] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        members = _safe_members(archive)
        if kind == "pptx":
            parts = _slide_parts(archive, members)
            label = "slide"
        else:
            parts = [(1, "word/document.xml")] if "word/document.xml" in members else []
            label = "document"
        for position, part_name in parts:
            per_unit = 0
            for rel_kind, target in _relationships(archive, members, part_name).values():
                if rel_kind != REL_IMAGE or target not in members:
                    continue
                if per_unit >= MAX_PER_UNIT:
                    break
                info = members[target]
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    continue
                normalised = _normalise(archive.read(info))
                if normalised is None:
                    continue
                media_type, data, width, height = normalised
                locator = f"{label} {position}" if label == "slide" else "document"
                found.append(
                    ExtractedImage(position, locator, media_type, data, width, height, hashlib.sha256(data).hexdigest())
                )
                per_unit += 1
    return found


def slide_pictures(payload: bytes) -> dict[int, list[bytes]]:
    """The raw pictures on each slide, by position, for OCR of picture slides."""
    pictures: dict[int, list[bytes]] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = _safe_members(archive)
            for position, part_name in _slide_parts(archive, members):
                for rel_kind, target in _relationships(archive, members, part_name).values():
                    if rel_kind == REL_IMAGE and target in members and members[target].file_size <= MAX_ARCHIVE_MEMBER_BYTES:
                        normalised = _normalise(archive.read(members[target]))
                        if normalised is not None:
                            pictures.setdefault(position, []).append(normalised[1])
    except Exception:  # noqa: BLE001
        logger.info("slide_pictures_failed")
    return pictures


def _slide_parts(archive: zipfile.ZipFile, members: dict) -> list[tuple[int, str]]:
    presentation = _read_part(archive, members, "ppt/presentation.xml")
    if presentation is None:
        return []
    rels = _relationships(archive, members, "ppt/presentation.xml")
    parts: list[tuple[int, str]] = []
    position = 0
    for slide_id in presentation.iter(f"{P_NS}sldId"):
        position += 1
        rid = slide_id.get(f"{R_NS}id")
        relationship = rels.get(rid) if rid else None
        if relationship is None or relationship[0] != REL_SLIDE:
            continue
        if relationship[1] in members:
            parts.append((position, relationship[1]))
    return parts


# --- normalisation ------------------------------------------------------------------


def _normalise(raw: bytes) -> tuple[str, bytes, int, int] | None:
    """(media type, bytes, width, height) as PNG or JPEG, or None to drop it."""
    from PIL import Image, ImageFile

    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    ImageFile.LOAD_TRUNCATED_IMAGES = False
    try:
        with Image.open(io.BytesIO(raw)) as picture:
            picture.load()
            width, height = picture.size
            if width < MIN_SIDE or height < MIN_SIDE or width * height < MIN_PIXELS:
                return None
            if picture.format == "JPEG":
                return ("image/jpeg", raw, width, height) if len(raw) <= MAX_IMAGE_BYTES else None
            converted = picture.convert("RGBA") if picture.mode in ("P", "LA", "RGBA", "1", "L") and "transparency" in picture.info else picture.convert("RGB") if picture.mode not in ("RGB", "RGBA", "L") else picture
            buffer = io.BytesIO()
            converted.save(buffer, format="PNG", optimize=True)
            data = buffer.getvalue()
    except Exception:  # noqa: BLE001 - not a picture Pillow can read, or a bomb
        return None
    if len(data) > MAX_IMAGE_BYTES:
        return None
    return ("image/png", data, width, height)
