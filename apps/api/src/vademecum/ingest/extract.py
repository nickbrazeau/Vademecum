"""Locating text inside an upload, with the location kept.

Everything produced is a :class:`Segment`: text plus where it came from
("page 4", "slide 12", "slide 12 (speaker notes)"). That locator becomes the
citation later, so it is produced here, where the mapping is actually known.

Guarantees:

* **No fetching.** XML is parsed through ``defusedxml``, which refuses DTDs and
  entity declarations outright; OOXML relationships with ``TargetMode="External"``
  are skipped. No code path here can open a socket.
* **No execution.** ``.pptm``/``.docm`` are not accepted, and ``vbaProject.bin``
  inside an accepted container is simply never opened.
* **No silent loss.** Over-long units become continuation segments, not
  truncations. Pages that failed and pages that were image-only are counted
  separately and reported. A whole-document cap, if reached, is stated.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from xml.etree.ElementTree import Element, ParseError

from .limits import (
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_MEMBER_BYTES,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MAX_DOCUMENT_CHARS,
    MAX_SEGMENT_CHARS,
    MAX_UNITS,
    MIN_UNIT_CHARS_FOR_TEXT,
)

STATUS_EXTRACTED = "extracted"
STATUS_NEEDS_OCR = "needs_ocr"
STATUS_ENCRYPTED = "encrypted"
STATUS_UNREADABLE = "unreadable"

A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P_NS = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

REL_SLIDE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
REL_NOTES = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
)

_WHITESPACE = re.compile(r"[ \t ]+")
_BLANK_LINES = re.compile(r"\n{3,}")
# Zero-width, soft-hyphen and bidi-control characters converters leave behind.
# Written as escapes: a literal invisible character in a regex is unreviewable.
_INVISIBLE = re.compile(
    "[\x00­​‌‍‎‏‪-‮⁦-⁩﻿]"
)


@dataclass(frozen=True)
class Segment:
    ordinal: int
    kind: str
    locator: str
    text: str
    # Which visible unit this belongs to (1-based). Continuation segments of one
    # page share a unit index, so coverage is counted per page, not per segment.
    unit_index: int = 0

    @property
    def char_count(self) -> int:
        return len(self.text)


@dataclass(frozen=True)
class Coverage:
    """How much of the document was actually read, honestly counted."""

    units_total: int = 0
    units_with_text: int = 0
    units_image_only: int = 0
    units_failed: int = 0
    units_dropped: int = 0
    document_truncated: bool = False
    # Units whose text came from on-device recognition of a picture (ADR
    # 0013). Counted inside `units_with_text` as well; said separately so a
    # reader knows which pages were read off an image.
    units_ocr: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "units_total": self.units_total,
            "units_with_text": self.units_with_text,
            "units_image_only": self.units_image_only,
            "units_failed": self.units_failed,
            "units_dropped": self.units_dropped,
            "document_truncated": self.document_truncated,
            "units_ocr": self.units_ocr,
        }

    @property
    def complete(self) -> bool:
        return (
            self.units_image_only == 0
            and self.units_failed == 0
            and self.units_dropped == 0
            and not self.document_truncated
        )


@dataclass(frozen=True)
class Extraction:
    status: str
    detail: str
    unit_kind: str
    unit_count: int
    segments: tuple[Segment, ...] = field(default_factory=tuple)
    coverage: Coverage = field(default_factory=Coverage)

    @property
    def char_count(self) -> int:
        return sum(segment.char_count for segment in self.segments)

    @property
    def readable(self) -> bool:
        return self.status == STATUS_EXTRACTED


# --- text shaping ------------------------------------------------------------


def tidy(text: str) -> str:
    """Collapse converter whitespace, keep line structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _INVISIBLE.sub("", text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANK_LINES.sub("\n\n", text).strip()


def _split(text: str, limit: int = MAX_SEGMENT_CHARS) -> list[str]:
    """Break *text* into <= *limit* chunks, preferring paragraph then line breaks.

    Nothing is dropped. This is what replaces the old truncating ``_clip``.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = window.rfind("\n\n")
        if cut < limit // 2:
            cut = window.rfind("\n")
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return [chunk for chunk in chunks if chunk]


# One unit of a document, before it becomes segments.
@dataclass(frozen=True)
class Unit:
    index: int          # 1-based visible position; notes share their slide's
    kind: str           # page | slide | notes | section
    locator: str
    text: str
    failed: bool = False
    counts_as_unit: bool = True   # speaker notes do not
    # Whether "almost no text here" could mean "this was a picture". True of a
    # PDF page and a slide, false of flowing text, which is never a scan.
    image_candidate: bool = False


def _is_content(unit: Unit, cleaned: str) -> bool:
    """Whether *cleaned* counts as this unit's text.

    ``MIN_UNIT_CHARS_FOR_TEXT`` is a scan heuristic: a page yielding "3" is a
    page number bleeding off an image, not a page with three characters on it.
    That only holds where the unit could have been an image to begin with. A
    `.docx` section, a plain-text file, or the five-character remainder of a
    long paragraph is content however short it is -- discarding it loses text
    that is genuinely there and calls a readable file a scan.
    """
    if not cleaned:
        return False
    return not unit.image_candidate or len(cleaned) >= MIN_UNIT_CHARS_FOR_TEXT


OCR_SUFFIX = " (OCR)"


def _recognised(units: list[Unit], read: Callable[[Unit], str] | None) -> tuple[list[Unit], set[int]]:
    """Replace units that have no readable text with what recognition finds.

    A unit that still yields nothing stays as it was, so the coverage record
    reports it image-only rather than pretending. The locator of a recognised
    unit says so, and that suffix travels into every citation of it.
    """
    if read is None:
        return units, set()
    replaced: list[Unit] = []
    recognised: set[int] = set()
    for unit in units:
        if unit.failed or not unit.image_candidate or _is_content(unit, tidy(unit.text)):
            replaced.append(unit)
            continue
        text = tidy(read(unit))
        if _is_content(unit, text):
            replaced.append(
                Unit(unit.index, unit.kind, f"{unit.locator}{OCR_SUFFIX}", text, image_candidate=True)
            )
            recognised.add(unit.index)
        else:
            replaced.append(unit)
    return replaced, recognised


def _assemble(
    units: list[Unit],
    *,
    unit_kind: str,
    dropped_units: int = 0,
    extra_notes: tuple[str, ...] = (),
    ocr_units: set[int] | frozenset[int] = frozenset(),
) -> Extraction:
    """Turn units into bounded segments and an honest coverage record.

    Coverage counts *visible* units only. Speaker notes still become segments,
    but they are not evidence about the slide they hang off: a slide whose own
    part is missing or is a bare image stays failed/image-only even when its
    notes read perfectly, because none of the content the reader can see was
    actually read.
    """
    segments: list[Segment] = []
    total_chars = 0
    truncated = False
    with_text: set[int] = set()
    image_only: set[int] = set()
    failed: set[int] = set()
    visible: set[int] = set()

    for unit in units:
        if unit.counts_as_unit:
            visible.add(unit.index)
        if unit.failed:
            if unit.counts_as_unit:
                failed.add(unit.index)
            continue
        cleaned = tidy(unit.text)
        if not _is_content(unit, cleaned):
            if unit.counts_as_unit:
                image_only.add(unit.index)
            continue
        if truncated:
            continue
        parts = _split(cleaned)
        for number, part in enumerate(parts, start=1):
            if total_chars + len(part) > MAX_DOCUMENT_CHARS:
                truncated = True
                break
            total_chars += len(part)
            locator = unit.locator if number == 1 else f"{unit.locator} (cont. {number})"
            segments.append(Segment(len(segments), unit.kind, locator, part, unit.index))
        if not truncated and unit.counts_as_unit:
            with_text.add(unit.index)

    image_only -= with_text
    failed -= with_text

    coverage = Coverage(
        units_total=len(visible),
        units_with_text=len(with_text),
        units_image_only=len(image_only),
        units_failed=len(failed),
        units_dropped=dropped_units,
        document_truncated=truncated,
        units_ocr=len(set(ocr_units) & with_text),
    )

    notes = list(extra_notes)
    if coverage.units_ocr:
        notes.append(
            f"{coverage.units_ocr} {unit_kind}(s) had no text layer and were read with "
            "on-device text recognition; their locators say so."
        )
    if coverage.units_image_only:
        # Only say "images or scans" where that is a possible explanation.
        because = (
            " (most likely images or scans)"
            if any(unit.image_candidate for unit in units)
            else ""
        )
        notes.append(
            f"{coverage.units_image_only} of {coverage.units_total} {unit_kind}s had no "
            f"readable text{because} and contributed nothing."
        )
    if coverage.units_failed:
        notes.append(
            f"{coverage.units_failed} {unit_kind}(s) could not be read and were skipped. "
            "The original is stored unchanged."
        )
    if dropped_units:
        notes.append(
            f"Only the first {MAX_UNITS} {unit_kind}s were read; {dropped_units} more "
            "are in the stored file."
        )
    if truncated:
        notes.append(
            "This document is larger than Vademecum stores as text; the tail was not "
            "extracted. The original is stored in full."
        )

    if not segments:
        return Extraction(
            status=STATUS_NEEDS_OCR,
            detail=(
                "No readable text was found. This is what a scan or an image export "
                "looks like. The original is stored; OCR it and upload the searchable "
                "version to build material from it."
            ),
            unit_kind=unit_kind,
            unit_count=len(visible),
            segments=(),
            coverage=coverage,
        )

    return Extraction(
        status=STATUS_EXTRACTED,
        detail=" ".join(notes),
        unit_kind=unit_kind,
        unit_count=len(visible),
        segments=tuple(segments),
        coverage=coverage,
    )


# --- XML -----------------------------------------------------------------


def _parse_xml(data: bytes) -> Element | None:
    """Parse an OOXML part with DTDs and entities refused.

    ``defusedxml`` raises on any DTD, entity declaration or external reference,
    which is the whole XXE and billion-laughs surface. A refused part
    contributes no text rather than reading a file or expanding.
    """
    try:
        return DefusedElementTree.fromstring(data)
    except (DefusedXmlException, ParseError, ValueError, TypeError):
        return None


def _text_of(root: Element, tag: str) -> str:
    """Visible text of one part. Tables are included: their cells are `a:t` too."""
    pieces: list[str] = []
    for element in root.iter():
        if element.tag == tag:
            if element.text:
                pieces.append(element.text)
        elif element.tag in {f"{A_NS}br", f"{W_NS}br", f"{A_NS}p", f"{W_NS}p", f"{A_NS}tr", f"{W_NS}tr"}:
            pieces.append("\n")
        elif element.tag in {f"{W_NS}tab", f"{A_NS}tc", f"{W_NS}tc"}:
            pieces.append("\t")
    return "".join(pieces)


# --- archives ----------------------------------------------------------------


class ArchiveRefused(ValueError):
    """A container that broke one of the archive bounds."""


def _safe_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    """Members, after checking declared sizes -- before anything is expanded."""
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveRefused(
            f"This file contains {len(infos)} internal parts, more than Vademecum "
            f"will open ({MAX_ARCHIVE_ENTRIES})."
        )
    total = 0
    members: dict[str, zipfile.ZipInfo] = {}
    for info in infos:
        name = info.filename
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            raise ArchiveRefused(
                "This file contains an internal part whose path tries to escape the "
                "document. Vademecum will not read it."
            )
        if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
            raise ArchiveRefused(
                "This file contains an internal part far larger than any document part "
                "should be. Vademecum will not expand it."
            )
        total += info.file_size
        if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ArchiveRefused(
                "This file expands to more than Vademecum will read. If it is genuinely "
                "this large, split it and upload the parts."
            )
        members[name] = info
    return members


def _read_part(archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], name: str) -> Element | None:
    info = members.get(name)
    if info is None:
        return None
    try:
        with archive.open(info) as handle:
            data = handle.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return None
    if len(data) > MAX_ARCHIVE_MEMBER_BYTES:
        return None
    return _parse_xml(data)


def _relationships(
    archive: zipfile.ZipFile, members: dict[str, zipfile.ZipInfo], part_name: str
) -> dict[str, tuple[str, str]]:
    """``rId -> (type, resolved target)`` for *part_name*, external ones dropped."""
    directory, _, base = part_name.rpartition("/")
    rels_name = f"{directory}/_rels/{base}.rels" if directory else f"_rels/{base}.rels"
    root = _read_part(archive, members, rels_name)
    if root is None:
        return {}
    resolved: dict[str, tuple[str, str]] = {}
    for element in root.iter(f"{PKG_REL_NS}Relationship"):
        if element.get("TargetMode") == "External":
            continue  # never resolve a resource outside the document
        rid = element.get("Id")
        target = element.get("Target")
        kind = element.get("Type") or ""
        if not rid or not target or target.startswith(("http://", "https://", "//")):
            continue
        resolved[rid] = (kind, _resolve(directory, target))
    return resolved


def _resolve(directory: str, target: str) -> str:
    """Resolve a relationship target against its part's directory."""
    if target.startswith("/"):
        return target.lstrip("/")
    parts = [segment for segment in directory.split("/") if segment]
    for segment in target.replace("\\", "/").split("/"):
        if segment in {"", "."}:
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


# --- PDF ---------------------------------------------------------------------


def extract_pdf(payload: bytes, *, ocr: Callable[[int], str] | None = None) -> Extraction:
    """Text per page. Encrypted, damaged and image-only PDFs each get a name.

    ``ocr``, when given, reads a page number off a picture of the page; it is
    consulted only for pages the text layer left empty.

    Only ``PdfReader`` is imported. pypdf's exception classes are not: the
    previous ``from pypdf.errors import PdfError`` raised ``ImportError`` against
    the installed pypdf, where the class is ``PyPdfError``, and because
    :func:`extract` catches everything, that turned *every* PDF -- the format
    most of this workspace's material arrives in -- into "could not read this
    file" with no hint that the reader had never run. Catching ``Exception``
    around the reader loses nothing: the clause was already
    ``except (PdfError, Exception)``, which is ``except Exception``, and a
    caller cannot act differently on one pypdf error than another anyway.
    """
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(payload))
    except Exception:
        return Extraction(
            STATUS_UNREADABLE,
            "Vademecum could not open this PDF. The file is stored unchanged; it may "
            "be damaged or only partly downloaded.",
            "page",
            0,
        )

    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            return Extraction(
                STATUS_ENCRYPTED,
                "This PDF is password-protected. The file is stored unchanged. Save an "
                "unlocked copy and upload that to build material from it.",
                "page",
                0,
            )

    try:
        pages = list(reader.pages)
    except Exception:
        return Extraction(
            STATUS_UNREADABLE,
            "Vademecum could not read this PDF's page tree. The file is stored unchanged.",
            "page",
            0,
        )

    dropped = max(0, len(pages) - MAX_UNITS)
    units: list[Unit] = []
    for index, page in enumerate(pages[:MAX_UNITS], start=1):
        try:
            text = page.extract_text() or ""
            failed = False
        except Exception:
            # One bad page is not a bad document -- but it is not an empty page
            # either, and the difference is recorded.
            text, failed = "", True
        units.append(
            Unit(index, "page", f"page {index}", text, failed=failed, image_candidate=True)
        )

    units, recognised = _recognised(units, (lambda unit: ocr(unit.index)) if ocr else None)
    extraction = _assemble(units, unit_kind="page", dropped_units=dropped, ocr_units=recognised)
    if extraction.status == STATUS_NEEDS_OCR and pages:
        tried = (
            " On-device OCR found nothing readable either."
            if ocr is not None
            else " On-device OCR is not available on this machine."
        )
        return Extraction(
            STATUS_NEEDS_OCR,
            f"This PDF has {len(pages)} pages and no text layer, which is what a scan "
            f"looks like.{tried} The original is stored; its pages can still be viewed.",
            "page",
            len(pages),
            coverage=extraction.coverage,
        )
    return extraction


# --- PPTX --------------------------------------------------------------------


def extract_pptx(payload: bytes, *, ocr: Callable[[int], str] | None = None) -> Extraction:
    """Slide text in presentation order, with each slide's own speaker notes.

    ``ocr``, when given, reads a slide position off the pictures on that slide;
    it is consulted only for slides whose own text is empty.

    Order comes from ``p:sldIdLst`` in ``ppt/presentation.xml`` resolved through
    the presentation's relationships -- not from the digits in a filename, which
    reflect creation order and are wrong for any deck whose slides were moved.
    Notes come from each slide's own ``notesSlide`` relationship, not from a
    same-numbered part. Notes are a separate segment, are not counted as a
    slide, and are not taken as evidence that the slide itself was read.

    A listed slide whose relationship or part cannot be resolved keeps its
    position and is reported as a failed slide. Numbering follows what the
    reader sees, so a citation of "slide 7" means the seventh slide of the deck
    even when an earlier one is missing.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = _safe_members(archive)
            presentation = _read_part(archive, members, "ppt/presentation.xml")
            if presentation is None:
                return Extraction(
                    STATUS_UNREADABLE,
                    "This presentation has no readable presentation part. The file is "
                    "stored unchanged.",
                    "slide",
                    0,
                )
            rels = _relationships(archive, members, "ppt/presentation.xml")
            # Every `p:sldId` is a slide the reader can see, whether or not its
            # part can be found. Dropping the unresolvable ones here would
            # renumber the rest -- slide 2 would be presented as "slide 1", and
            # a deck missing a slide would report itself completely read. So the
            # position is kept and the failure is carried with it. ``None`` is a
            # relationship that could not be resolved at all; a name that is not
            # in *members* is a target that resolved to a part which is absent.
            ordered: list[str | None] = []
            for slide_id in presentation.iter(f"{P_NS}sldId"):
                rid = slide_id.get(f"{R_NS}id")
                relationship = rels.get(rid) if rid else None
                if relationship is None or relationship[0] != REL_SLIDE:
                    ordered.append(None)
                    continue
                ordered.append(relationship[1])

            if not ordered:
                # A deck with no slide list is malformed rather than empty; say so
                # instead of reporting a successful read of nothing.
                return Extraction(
                    STATUS_UNREADABLE,
                    "This presentation lists no slides. The file is stored unchanged.",
                    "slide",
                    0,
                )

            dropped = max(0, len(ordered) - MAX_UNITS)
            units: list[Unit] = []
            for position, part_name in enumerate(ordered[:MAX_UNITS], start=1):
                root = (
                    _read_part(archive, members, part_name)
                    if part_name is not None and part_name in members
                    else None
                )
                units.append(
                    Unit(
                        position,
                        "slide",
                        f"slide {position}",
                        "" if root is None else _text_of(root, f"{A_NS}t"),
                        failed=root is None,
                        image_candidate=True,
                    )
                )
                if part_name is None:
                    # No slide relationship, so no relationship part to ask for
                    # notes: nothing can be attributed to this position.
                    continue
                # Notes follow the owning slide's own relationship, which exists
                # independently of the slide part -- a missing or damaged slide
                # can still have readable notes, and they belong to this
                # position, not to the next slide that happens to be readable.
                notes_name = next(
                    (
                        target
                        for kind, target in _relationships(archive, members, part_name).values()
                        if kind == REL_NOTES
                    ),
                    None,
                )
                if notes_name and notes_name in members:
                    notes_root = _read_part(archive, members, notes_name)
                    if notes_root is not None:
                        units.append(
                            Unit(
                                position,
                                "notes",
                                f"slide {position} (speaker notes)",
                                _text_of(notes_root, f"{A_NS}t"),
                                counts_as_unit=False,
                            )
                        )
    except ArchiveRefused as exc:
        return Extraction(STATUS_UNREADABLE, str(exc), "slide", 0)
    except (zipfile.BadZipFile, OSError, ValueError):
        return Extraction(
            STATUS_UNREADABLE,
            "Vademecum could not open this presentation. The file is stored unchanged; "
            "it may be damaged.",
            "slide",
            0,
        )

    units, recognised = _recognised(
        units, (lambda unit: ocr(unit.index) if unit.kind == "slide" else "") if ocr else None
    )
    extraction = _assemble(units, unit_kind="slide", dropped_units=dropped, ocr_units=recognised)
    if extraction.status == STATUS_NEEDS_OCR and ordered:
        coverage = extraction.coverage
        if coverage.units_failed and coverage.units_failed == coverage.units_total:
            # Nothing was read because nothing could be opened. That is a broken
            # deck, not a deck of pictures, and OCR would not help.
            return Extraction(
                STATUS_UNREADABLE,
                f"None of this deck's {len(ordered)} slides could be read: the slide "
                "parts are missing or damaged. The file is stored unchanged.",
                "slide",
                extraction.unit_count,
                coverage=coverage,
            )
        return Extraction(
            STATUS_NEEDS_OCR,
            f"This deck has {len(ordered)} slides and no readable text, which is what a "
            "deck of exported images looks like. The original is stored.",
            "slide",
            extraction.unit_count,
            coverage=coverage,
        )
    return extraction


# --- DOCX and plain text -----------------------------------------------------

# A `.docx` has no pages: pagination is a rendering property. Paragraphs are
# grouped into bounded sections and the locator says "section", not "page".
SECTION_CHARS = 3000


def _sectionise(text: str, unit_kind: str) -> list[Unit]:
    """Group flowing text into bounded numbered sections.

    Splits on blank lines where they exist and on single newlines or raw length
    where they do not -- a normal `.docx` is one paragraph per line, so relying
    on blank lines alone silently loses everything after the first section.
    """
    if not text:
        return []
    blocks: list[str] = []
    for paragraph in text.split("\n\n"):
        for line in paragraph.split("\n") if len(paragraph) > SECTION_CHARS else [paragraph]:
            blocks.extend(_split(line, SECTION_CHARS) if len(line) > SECTION_CHARS else [line])

    units: list[Unit] = []
    buffer: list[str] = []
    size = 0
    for block in blocks:
        if not block.strip():
            continue
        if size and size + len(block) > SECTION_CHARS:
            index = len(units) + 1
            units.append(Unit(index, unit_kind, f"{unit_kind} {index}", "\n\n".join(buffer)))
            buffer, size = [], 0
        buffer.append(block)
        size += len(block) + 2
    if buffer:
        index = len(units) + 1
        units.append(Unit(index, unit_kind, f"{unit_kind} {index}", "\n\n".join(buffer)))
    return units


def extract_docx(payload: bytes) -> Extraction:
    """Body text of the main document part, grouped into numbered sections."""
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            members = _safe_members(archive)
            root = _read_part(archive, members, "word/document.xml")
            if root is None:
                return Extraction(
                    STATUS_UNREADABLE,
                    "This .docx has no readable main document part. The file is stored "
                    "unchanged.",
                    "section",
                    0,
                )
            text = tidy(_text_of(root, f"{W_NS}t"))
    except ArchiveRefused as exc:
        return Extraction(STATUS_UNREADABLE, str(exc), "section", 0)
    except (zipfile.BadZipFile, OSError, ValueError):
        return Extraction(
            STATUS_UNREADABLE,
            "Vademecum could not open this document. The file is stored unchanged; it "
            "may be damaged.",
            "section",
            0,
        )

    units = _sectionise(text, "section")
    dropped = max(0, len(units) - MAX_UNITS)
    extraction = _assemble(units[:MAX_UNITS], unit_kind="section", dropped_units=dropped)
    if extraction.status == STATUS_NEEDS_OCR:
        return Extraction(
            STATUS_UNREADABLE,
            "This document contains no body text. It is stored unchanged.",
            "section",
            0,
            coverage=extraction.coverage,
        )
    return extraction


def extract_text(payload: bytes) -> Extraction:
    """UTF-8 text or Markdown, grouped the same way a .docx is."""
    text = tidy(payload.decode("utf-8", errors="replace"))
    units = _sectionise(text, "section")
    dropped = max(0, len(units) - MAX_UNITS)
    extraction = _assemble(units[:MAX_UNITS], unit_kind="section", dropped_units=dropped)
    if extraction.status == STATUS_NEEDS_OCR:
        # A text file with no text is empty, not scanned.
        return Extraction(
            STATUS_UNREADABLE,
            "That file contains no text. It is stored unchanged.",
            "section",
            0,
            coverage=extraction.coverage,
        )
    return extraction


def extract_image(payload: bytes, *, ocr: Callable[[int], str] | None = None) -> Extraction:
    """A standalone picture: one unit, whose text is whatever recognition reads.

    A photographed handout or a screenshot of a table is text once it has been
    read; a diagram with three labels is a picture that is kept and shown but
    builds nothing. Either way the picture itself is stored beside the source.
    """
    units = [Unit(1, "image", "image", "", image_candidate=True)]
    units, recognised = _recognised(units, (lambda unit: ocr(unit.index)) if ocr else None)
    extraction = _assemble(units, unit_kind="image", ocr_units=recognised)
    if extraction.status == STATUS_NEEDS_OCR:
        tried = (
            "On-device OCR found no readable text in it."
            if ocr is not None
            else "On-device OCR is not available on this machine."
        )
        return Extraction(
            STATUS_NEEDS_OCR,
            f"This is a picture. {tried} It is kept and can be viewed; nothing is built from it.",
            "image",
            1,
            coverage=extraction.coverage,
        )
    return extraction


EXTRACTORS = {
    "pdf": extract_pdf,
    "pptx": extract_pptx,
    "docx": extract_docx,
    "text": extract_text,
    "markdown": extract_text,
    "image": extract_image,
}


def extract(kind: str, payload: bytes, *, ocr: Any = None) -> Extraction:
    """Read *payload* as *kind*. Never raises for a bad file.

    ``ocr`` is a per-unit reader for PDFs and decks (see ``vision``); absent,
    pages and slides without a text layer stay image-only.
    """
    extractor = EXTRACTORS.get(kind)
    if extractor is None:  # pragma: no cover - detect() has already refused
        return Extraction(STATUS_UNREADABLE, "Unsupported format.", "section", 0)
    try:
        if ocr is not None and kind in ("pdf", "pptx", "image"):
            return extractor(payload, ocr=ocr)
        return extractor(payload)
    except Exception:
        # The exception is not carried forward: a parser error can quote the
        # bytes it choked on, and those bytes are the owner's document.
        return Extraction(
            STATUS_UNREADABLE,
            "Vademecum could not read this file. It is stored unchanged, so nothing has "
            "been lost.",
            "section",
            0,
        )
