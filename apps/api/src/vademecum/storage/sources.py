"""Uploaded originals, their segments, and resumable batch coverage.

Three properties this module owns.

**Idempotent upload.** Files are content-addressed: the on-disk name is the
SHA-256 and nothing else, so the uploaded filename is never joined to a path.
Re-uploading identical bytes whose extraction is also identical is a true no-op
-- segment ids are PRESERVED, because questions and citations are anchored to
them. Only a genuinely different extraction replaces segments, and when it does
it invalidates everything downstream rather than leaving dangling anchors.

**Resumable coverage.** Coverage is a CHARACTER CURSOR per segment, advanced
only when a batch's output actually commits. A long PDF is walked to its end
over successive batches instead of its opening being resent forever, a failed
batch loses no ground, and `reopen_coverage` exists so a re-included source is
not a permanent dead end.

**Consent to exact ranges.** A preview records the ordered `(segment, start,
end)` ranges and hashes them together with every piece of metadata that is
transmitted alongside them -- the filename, the confidence label and the
locator. Sending quotes that hash back; if anything the owner was shown has
changed, the send is refused in favour of a fresh preview.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..db import transaction
from ..ingest import Extraction
from .common import NotFoundError, new_id, utc_now

CONFIDENCES: tuple[str, ...] = ("low", "mid", "high")

# What a confidence means. Note what none of them says: anything about whether a
# claim is true. Confidence is about the material, support is about the claim.
# Your judgement of the MATERIAL's accuracy and usefulness -- not how well you
# know it, not how urgent it is, and not whether anything in it has been checked.
CONFIDENCE_MEANING = {
    "low": (
        "You judge this material as of uncertain accuracy or limited usefulness — "
        "informal notes, an old handout, something you have not vetted."
    ),
    "mid": (
        "You judge this material as reasonably accurate and useful: a source you "
        "generally trust, without treating it as definitive."
    ),
    "high": (
        "You judge this material as accurate and highly useful — a current "
        "guideline or a reference you would quote."
    ),
}

# What coverage does and does not mean. This intake is TEXT-ONLY.
COVERAGE_MEANING = (
    "Coverage counts the readable text Vademecum extracted, not the whole "
    "document. Figures, diagrams, photographs and scanned pages are not "
    "interpreted — a slide that is mostly a flowchart contributes almost no text, "
    "and a page with no text layer contributes none at all."
)

# The stored value stays `mid` for compatibility with rows written before 0002.
CONFIDENCE_LABEL = {"low": "Low", "mid": "Medium", "high": "High"}

READABLE = "extracted"
ATTENTION_STATUSES = ("needs_ocr", "encrypted", "unreadable")


class ConflictError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


# --- shapes ------------------------------------------------------------------


@dataclass(frozen=True)
class SourceSegment:
    id: str
    source_id: str
    ordinal: int
    kind: str
    locator: str
    text: str
    char_count: int
    covered_upto: int = 0

    @property
    def fully_covered(self) -> bool:
        return self.covered_upto >= self.char_count

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fully_covered"] = self.fully_covered
        return data


@dataclass(frozen=True)
class Coverage:
    """How much extracted text has been through a committed build, in characters.

    Characters rather than segments: a segment can be longer than one batch may
    send, so counting segments would report a 12,000-character page as fully
    processed after 2,400 of it had been.
    """

    chars_total: int = 0
    chars_covered: int = 0

    @property
    def complete(self) -> bool:
        return self.chars_total > 0 and self.chars_covered >= self.chars_total

    @property
    def percent(self) -> int:
        # Floors below 100 so "100%" never appears next to "not complete".
        if self.chars_total <= 0:
            return 0
        if self.complete:
            return 100
        return min(99, int(100 * self.chars_covered / self.chars_total))

    def as_dict(self) -> dict[str, Any]:
        return {
            "chars_total": self.chars_total,
            "chars_covered": self.chars_covered,
            "percent": self.percent,
            "complete": self.complete,
        }


@dataclass(frozen=True)
class Source:
    id: str
    pile_id: str
    display_name: str
    media_type: str
    byte_size: int
    sha256: str
    confidence: str
    status: str
    status_detail: str
    unit_kind: str
    unit_count: int
    char_count: int
    excluded: bool
    created_at: str
    updated_at: str
    extracted_at: str | None
    extraction_coverage: dict[str, Any] = field(default_factory=dict)
    point_count: int = 0
    coverage: Coverage = field(default_factory=Coverage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "pile_id": self.pile_id,
            "display_name": self.display_name,
            "media_type": self.media_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
            "confidence": self.confidence,
            "confidence_label": CONFIDENCE_LABEL.get(self.confidence, self.confidence),
            "status": self.status,
            "status_detail": self.status_detail,
            "unit_kind": self.unit_kind,
            "unit_count": self.unit_count,
            "char_count": self.char_count,
            "excluded": self.excluded,
            "point_count": self.point_count,
            "coverage": self.coverage.as_dict(),
            "extraction_coverage": self.extraction_coverage,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "extracted_at": self.extracted_at,
        }


@dataclass(frozen=True)
class StoredUpload:
    source: Source
    outcome: str  # stored | duplicate | re_extracted
    segments: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    invalidated: dict[str, int] = field(default_factory=dict)


# --- digests -----------------------------------------------------------------


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extraction_hash(extraction: Extraction) -> str:
    """Digest of what was READ, not of the file.

    Identical bytes read by the same parser give the same value, which is what
    makes a duplicate upload a genuine no-op. A parser improvement changes it,
    which is what triggers revalidation downstream.
    """
    parts = hashlib.sha256()
    parts.update(extraction.status.encode("utf-8"))
    parts.update(b"\x1f")
    for segment in extraction.segments:
        parts.update(segment.locator.encode("utf-8"))
        parts.update(b"\x1e")
        parts.update(segment.text.encode("utf-8"))
        parts.update(b"\x1f")
    return parts.hexdigest()


def stored_name_for(sha256: str, media_kind: str, media_type: str = "") -> str:
    suffix = {
        "pdf": ".pdf",
        "pptx": ".pptx",
        "docx": ".docx",
        "text": ".txt",
        "markdown": ".md",
        "image": ".jpg" if media_type == "image/jpeg" else ".png",
    }.get(media_kind, ".bin")
    return f"{sha256}{suffix}"


def _kind_of(media_type: str) -> str:
    if media_type == "application/pdf":
        return "pdf"
    if media_type.endswith("presentationml.presentation"):
        return "pptx"
    if media_type.endswith("wordprocessingml.document"):
        return "docx"
    if media_type == "text/markdown":
        return "markdown"
    if media_type.startswith("image/"):
        return "image"
    return "text"


def file_digest(path: Path, *, chunk: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def write_original(directory: Path, stored_name: str, payload: bytes, sha256: str) -> None:
    """Write the canonical original atomically, verifying any existing copy.

    Two things this gets right that a size check does not. A file already at the
    target is trusted only if it HASHES to *sha256* -- a truncated or corrupted
    original of the right length would otherwise be kept forever. And the
    temporary file is unique per call (``mkstemp`` in the target directory), so
    two concurrent uploads of the same bytes cannot write through one another's
    partial file; both then ``os.replace`` the same content, and replace is
    atomic, so whichever lands second is a no-op in effect.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / stored_name
    if target.exists():
        try:
            if file_digest(target) == sha256:
                return
        except OSError:
            pass  # unreadable: fall through and rewrite it

    handle_fd, temporary_name = tempfile.mkstemp(
        dir=str(directory), prefix=f".{sha256[:16]}-", suffix=".partial"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


# --- reads -------------------------------------------------------------------


_SOURCE_SELECT = """
SELECT s.*,
       (SELECT COUNT(DISTINCT lps.learning_point_id)
          FROM learning_point_sources lps WHERE lps.source_id = s.id) AS point_count,
       (SELECT COALESCE(SUM(g.char_count), 0) FROM source_segments g
         WHERE g.source_id = s.id) AS chars_total,
       (SELECT COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0) FROM source_segments g
         WHERE g.source_id = s.id) AS chars_covered
FROM sources s
"""


def _source(row: sqlite3.Row) -> Source:
    keys = row.keys()
    try:
        extraction_coverage = json.loads(row["coverage_json"] or "{}")
    except ValueError:
        extraction_coverage = {}
    return Source(
        id=row["id"],
        pile_id=row["pile_id"],
        display_name=row["display_name"],
        media_type=row["media_type"],
        byte_size=row["byte_size"],
        sha256=row["sha256"],
        confidence=row["confidence"],
        status=row["status"],
        status_detail=row["status_detail"],
        unit_kind=row["unit_kind"],
        unit_count=row["unit_count"],
        char_count=row["char_count"],
        excluded=bool(row["excluded"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        extracted_at=row["extracted_at"],
        extraction_coverage=extraction_coverage,
        point_count=row["point_count"] if "point_count" in keys else 0,
        coverage=Coverage(
            chars_total=row["chars_total"] if "chars_total" in keys else 0,
            chars_covered=row["chars_covered"] if "chars_covered" in keys else 0,
        ),
    )


def _segment(row: sqlite3.Row) -> SourceSegment:
    keys = row.keys()
    return SourceSegment(
        id=row["id"],
        source_id=row["source_id"],
        ordinal=row["ordinal"],
        kind=row["kind"],
        locator=row["locator"],
        text=row["text"],
        char_count=row["char_count"],
        covered_upto=row["covered_upto"] if "covered_upto" in keys else 0,
    )


def list_sources(connection: sqlite3.Connection, *, pile_id: str | None = None) -> list[Source]:
    if pile_id is None:
        rows = connection.execute(_SOURCE_SELECT + " ORDER BY s.created_at DESC").fetchall()
    else:
        rows = connection.execute(
            _SOURCE_SELECT + " WHERE s.pile_id = ? ORDER BY s.created_at DESC", (pile_id,)
        ).fetchall()
    return [_source(row) for row in rows]


def get_source(connection: sqlite3.Connection, source_id: str) -> Source:
    row = connection.execute(_SOURCE_SELECT + " WHERE s.id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError("source", source_id)
    return _source(row)


def find_by_digest(
    connection: sqlite3.Connection, *, pile_id: str, sha256: str
) -> Source | None:
    row = connection.execute(
        _SOURCE_SELECT + " WHERE s.pile_id = ? AND s.sha256 = ?", (pile_id, sha256)
    ).fetchone()
    return None if row is None else _source(row)


def list_segments(
    connection: sqlite3.Connection,
    source_id: str,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[SourceSegment]:
    sql = "SELECT * FROM source_segments WHERE source_id = ? ORDER BY ordinal"
    params: list[Any] = [source_id]
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])
    return [_segment(row) for row in connection.execute(sql, params).fetchall()]


def get_segment(connection: sqlite3.Connection, segment_id: str) -> SourceSegment | None:
    row = connection.execute(
        "SELECT * FROM source_segments WHERE id = ?", (segment_id,)
    ).fetchone()
    return None if row is None else _segment(row)


def stored_name_of(connection: sqlite3.Connection, source_id: str) -> str | None:
    row = connection.execute(
        "SELECT stored_name FROM sources WHERE id = ?", (source_id,)
    ).fetchone()
    return None if row is None else row["stored_name"]


def has_source(connection: sqlite3.Connection, *, pile_id: str, sha256: str) -> bool:
    """Whether these exact bytes are already a source in this pile.

    What lets a folder scan skip a file without re-reading it: the unique
    index on (pile, digest) is the same fact `store_upload` relies on.
    """
    row = connection.execute(
        "SELECT 1 FROM sources WHERE pile_id = ? AND sha256 = ? LIMIT 1", (pile_id, sha256)
    ).fetchone()
    return row is not None


def digest_still_referenced(connection: sqlite3.Connection, sha256: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sources WHERE sha256 = ? LIMIT 1", (sha256,)
        ).fetchone()
        is not None
    )


# --- upload ------------------------------------------------------------------


def store_upload(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    display_name: str,
    media_type: str,
    sha256: str,
    byte_size: int,
    confidence: str,
    extraction: Extraction,
) -> StoredUpload:
    """Record an upload. Identical bytes AND identical extraction is a no-op.

    The three cases:

    * **New.** A row and its segments are written.
    * **Duplicate.** Same digest, same extraction hash. Segment ids, coverage
      and everything anchored to them are left completely alone; only the
      display name and confidence are refreshed. This is the case that was
      previously destroying anchors on every re-upload.
    * **Re-extracted.** Same digest, different extraction. Segments are rebuilt,
      preserving the id of any segment whose text is unchanged, and every point
      and question that cited this source is invalidated for revalidation rather
      than left pointing at ids that no longer exist.
    """
    if confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence {confidence!r}")

    now = utc_now()
    fingerprint = extraction_hash(extraction)
    existing = find_by_digest(connection, pile_id=pile_id, sha256=sha256)
    coverage_json = json.dumps(extraction.coverage.as_dict(), separators=(",", ":"))
    warnings = (extraction.detail,) if extraction.detail else ()

    if existing is None:
        source_id = new_id("src")
        with transaction(connection) as tx:
            tx.execute(
                "INSERT INTO sources (id, pile_id, display_name, stored_name, media_type,"
                " byte_size, sha256, confidence, status, status_detail, unit_kind,"
                " unit_count, char_count, extraction_hash, coverage_json, excluded,"
                " created_at, updated_at, extracted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    source_id,
                    pile_id,
                    display_name,
                    stored_name_for(sha256, _kind_of(media_type), media_type),
                    media_type,
                    byte_size,
                    sha256,
                    confidence,
                    extraction.status,
                    extraction.detail,
                    extraction.unit_kind,
                    extraction.unit_count,
                    extraction.char_count,
                    fingerprint,
                    coverage_json,
                    now,
                    now,
                    now if extraction.readable else None,
                ),
            )
            _write_segments(tx, source_id, extraction)
        return StoredUpload(
            source=get_source(connection, source_id),
            outcome="stored",
            segments=len(extraction.segments),
            warnings=warnings,
        )

    source_id = existing.id
    stored_fingerprint = connection.execute(
        "SELECT extraction_hash FROM sources WHERE id = ?", (source_id,)
    ).fetchone()["extraction_hash"]

    if stored_fingerprint == fingerprint:
        # A genuine duplicate. Touch nothing that anything else points at.
        with transaction(connection) as tx:
            tx.execute(
                "UPDATE sources SET display_name = ?, confidence = ?, updated_at = ?"
                " WHERE id = ?",
                (display_name, confidence, now, source_id),
            )
        return StoredUpload(
            source=get_source(connection, source_id),
            outcome="duplicate",
            segments=_segment_count(connection, source_id),
            warnings=warnings,
        )

    # The extraction changed. Reconcile IN PLACE: a segment whose text is
    # unchanged keeps its row, so the anchors pointing at it are never orphaned.
    # Deleting first and re-inserting the same id does not work -- the FK is
    # ON DELETE SET NULL, so the delete has already nulled the anchors by the
    # time the row comes back.
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE sources SET display_name = ?, confidence = ?, status = ?,"
            " status_detail = ?, unit_kind = ?, unit_count = ?, char_count = ?,"
            " extraction_hash = ?, coverage_json = ?, updated_at = ?, extracted_at = ?"
            " WHERE id = ?",
            (
                display_name,
                confidence,
                extraction.status,
                extraction.detail,
                extraction.unit_kind,
                extraction.unit_count,
                extraction.char_count,
                fingerprint,
                coverage_json,
                now,
                now if extraction.readable else None,
                source_id,
            ),
        )
        _reconcile_segments(tx, source_id, extraction)
        # Same transaction as the re-extraction: a source can never be left
        # with new text and stale eligibility, in either order.
        invalidated = _invalidate_dependents(
            tx,
            source_id,
            reason=(
                "The file this was taken from was re-read and its text changed. The "
                "claim and its questions are held until they are checked again."
            ),
        )

    return StoredUpload(
        source=get_source(connection, source_id),
        outcome="re_extracted",
        segments=len(extraction.segments),
        warnings=warnings,
        invalidated=invalidated,
    )


def _segment_count(connection: sqlite3.Connection, source_id: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) AS n FROM source_segments WHERE source_id = ?", (source_id,)
    ).fetchone()["n"]


def _write_segments(
    tx: sqlite3.Connection, source_id: str, extraction: Extraction
) -> None:
    """Insert a fresh source's segments."""
    for segment in extraction.segments:
        tx.execute(
            "INSERT INTO source_segments"
            " (id, source_id, ordinal, kind, locator, text, char_count, text_hash,"
            " covered_upto, covered_at, covered_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)",
            (
                new_id("seg"),
                source_id,
                segment.ordinal,
                segment.kind,
                segment.locator,
                segment.text,
                segment.char_count,
                _text_hash(segment.text),
            ),
        )


def _reconcile_segments(
    tx: sqlite3.Connection, source_id: str, extraction: Extraction
) -> None:
    """Bring stored segments in line with a new extraction, in place.

    A segment whose text is unchanged keeps its row and its id -- and therefore
    its coverage cursor and every anchor pointing at it -- even if it has moved
    to a different ordinal. Only genuinely changed or surplus segments are
    rewritten or removed, so a re-read that shifts one page does not orphan the
    citations on every other page.
    """
    existing = tx.execute(
        "SELECT id, ordinal, text_hash FROM source_segments WHERE source_id = ?"
        " ORDER BY ordinal",
        (source_id,),
    ).fetchall()

    # text_hash -> [ids], oldest ordinal first, so identical text matches the
    # earliest surviving row rather than an arbitrary one.
    by_hash: dict[str, list[str]] = {}
    for row in existing:
        by_hash.setdefault(row["text_hash"], []).append(row["id"])

    keep: set[str] = set()
    # Ordinals are unique per source, so a rewrite has to avoid colliding with a
    # row it has not moved yet. Park everything being rewritten on negative
    # ordinals first, then settle them.
    parked: list[tuple[str, int]] = []

    for segment in extraction.segments:
        text_hash = _text_hash(segment.text)
        pool = by_hash.get(text_hash)
        if pool:
            segment_id = pool.pop(0)
            keep.add(segment_id)
            parked.append((segment_id, segment.ordinal))
            tx.execute(
                "UPDATE source_segments SET ordinal = ?, kind = ?, locator = ?"
                " WHERE id = ?",
                (-(len(parked)), segment.kind, segment.locator, segment_id),
            )
        else:
            segment_id = new_id("seg")
            keep.add(segment_id)
            parked.append((segment_id, segment.ordinal))
            tx.execute(
                "INSERT INTO source_segments"
                " (id, source_id, ordinal, kind, locator, text, char_count, text_hash,"
                " covered_upto, covered_at, covered_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL, NULL)",
                (
                    segment_id,
                    source_id,
                    -(len(parked)),
                    segment.kind,
                    segment.locator,
                    segment.text,
                    segment.char_count,
                    text_hash,
                ),
            )

    surplus = [row["id"] for row in existing if row["id"] not in keep]
    for segment_id in surplus:
        tx.execute("DELETE FROM source_segments WHERE id = ?", (segment_id,))

    for segment_id, ordinal in parked:
        tx.execute(
            "UPDATE source_segments SET ordinal = ? WHERE id = ?", (ordinal, segment_id)
        )


def _invalidate_dependents(
    tx: sqlite3.Connection, source_id: str, *, reason: str
) -> dict[str, int]:
    """Hold every point and question depending on *source_id*. Caller owns the tx.

    Deletes nothing: material stays, visibly held, until a later run revalidates
    it. Anchors whose segment is gone are exactly what
    ``learning.release_question`` refuses to make eligible, so holding here and
    re-checking there are the same rule stated twice.
    """
    now = utc_now()
    points = tx.execute(
        "UPDATE learning_points SET held = 1, hold_reason = ?,"
        " review_state = 'needs_re_review', updated_at = ?"
        " WHERE id IN (SELECT learning_point_id FROM learning_point_sources"
        "              WHERE source_id = ?)",
        (reason, now, source_id),
    ).rowcount
    questions = tx.execute(
        "UPDATE tutor_questions SET status = 'held', hold_reason = ?,"
        " assessment = 'unassessed', assessed_at = NULL, updated_at = ?"
        " WHERE status = 'eligible' AND (id IN ("
        "   SELECT question_id FROM tutor_question_anchors WHERE source_id = ?)"
        " OR learning_point_id IN ("
        "   SELECT learning_point_id FROM learning_point_sources WHERE source_id = ?))",
        (reason, now, source_id, source_id),
    ).rowcount
    return {"points": max(points, 0), "questions": max(questions, 0)}


def invalidate_dependents(
    connection: sqlite3.Connection, source_id: str, *, reason: str
) -> dict[str, int]:
    """Transactional wrapper for callers that own no transaction."""
    with transaction(connection) as tx:
        return _invalidate_dependents(tx, source_id, reason=reason)


def set_excluded(connection: sqlite3.Connection, source_id: str, *, excluded: bool) -> Source:
    """Exclude or re-include a source.

    Excluding is not just a flag on future selection: everything already built
    from the source is held immediately, because material the owner has just
    said not to use should stop being asked the moment they say it. Re-including
    does NOT silently restore eligibility -- a later verification run has to
    put it back, so nothing returns to Tutor unchecked.
    """
    get_source(connection, source_id)
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE sources SET excluded = ?, updated_at = ? WHERE id = ?",
            (1 if excluded else 0, utc_now(), source_id),
        )
    if excluded:
        invalidate_dependents(
            connection,
            source_id,
            reason="You excluded the source this came from, so it is no longer asked.",
        )
    return get_source(connection, source_id)


def set_confidence(connection: sqlite3.Connection, source_id: str, *, confidence: str) -> Source:
    if confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence {confidence!r}")
    get_source(connection, source_id)
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE sources SET confidence = ?, updated_at = ? WHERE id = ?",
            (confidence, utc_now(), source_id),
        )
    return get_source(connection, source_id)


def delete_source(connection: sqlite3.Connection, source_id: str) -> Source:
    """Remove a source, refusing while anything cites it."""
    source = get_source(connection, source_id)
    if source.point_count:
        raise ConflictError(
            "source_in_use",
            f"{source.point_count} learning point(s) cite this source, and their "
            "provenance has to keep pointing at something real — retiring the "
            "material does not change that. Exclude the source instead: it stops "
            "being used for anything new, its questions leave Tutor immediately, "
            "and the history that cites it stays readable.",
        )
    with transaction(connection) as tx:
        tx.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return source


def remove_original(directory: Path, stored_name: str, sha256: str) -> None:
    """Delete a stored original. Callers check no row still shares the digest."""
    if not stored_name.startswith(sha256):
        return
    (directory / stored_name).unlink(missing_ok=True)


# --- batch selection: persisted character ranges ------------------------------
#
# Coverage is a character cursor per segment, not a boolean. A stored segment can
# be longer than one batch may transmit, so "this segment appeared in a batch" is
# not "all of its text was processed". Each batch takes the next unsent slice of
# each segment, and committing advances the cursor to exactly the end of what was
# sent. Repeated batches therefore walk a long document to its end, including the
# last short tail, and a failed batch advances nothing.

# What one batch may send.
MAX_EXCERPTS = 24
MAX_EXCERPT_CHARS = 2400
MAX_TOTAL_EXCERPT_CHARS = 40_000

# A trailing slice shorter than this is folded into the previous excerpt rather
# than becoming a batch of its own, so a 12,001-character segment does not cost
# an extra round trip for one character.
MIN_TAIL_CHARS = 200


@dataclass(frozen=True)
class Excerpt:
    """One bounded, consented slice of one segment.

    ``start``/``end`` are character offsets into the stored segment text, and
    ``text`` is exactly ``segment.text[start:end]``. Everything downstream --
    the preview, the transmitted prompt, the quote check, the passages handed to
    the assessment pass -- uses this object, so what was consented to, what was
    sent, and what a citation is checked against are the same characters.
    """

    source_id: str
    segment_id: str
    display_name: str
    confidence: str
    locator: str
    text: str
    start: int = 0
    end: int = 0

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def range_label(self) -> str:
        """The locator, made exact when the slice is only part of a page."""
        if self.start == 0 and self.end >= self.start + len(self.text):
            return self.locator
        return f"{self.locator} (characters {self.start}–{self.end})"

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "segment_id": self.segment_id,
            "display_name": self.display_name,
            "confidence": self.confidence,
            "confidence_label": CONFIDENCE_LABEL.get(self.confidence, self.confidence),
            "locator": self.locator,
            "range_label": self.range_label,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "char_count": self.char_count,
        }


@dataclass(frozen=True)
class Batch:
    """A proposed batch and the coverage, in characters, it would advance."""

    excerpts: tuple[Excerpt, ...]
    selection_hash: str
    chars_total: int
    chars_covered: int

    @property
    def excerpt_chars(self) -> int:
        return sum(excerpt.char_count for excerpt in self.excerpts)

    @property
    def chars_remaining_after(self) -> int:
        return max(0, self.chars_total - self.chars_covered - self.excerpt_chars)

    @property
    def complete_after(self) -> bool:
        return self.chars_total > 0 and self.chars_remaining_after == 0

    def coverage_dict(self) -> dict[str, Any]:
        return {
            "chars_total": self.chars_total,
            "chars_covered": self.chars_covered,
            "chars_in_batch": self.excerpt_chars,
            "chars_remaining_after": self.chars_remaining_after,
            "excerpts_in_batch": len(self.excerpts),
            "complete": self.chars_total > 0 and self.chars_covered >= self.chars_total,
            "complete_after": self.complete_after,
        }


def excerpt_identity(excerpt: "Excerpt") -> tuple[str, ...]:
    """Everything about one excerpt that is transmitted, in a stable order.

    The hash is built from this, so consent covers the WHOLE payload rather than
    just the passage text. The filename, the confidence label and the locator
    are all put into the prompt alongside the text (see ``model/prompts.py``);
    a source renamed or re-rated between the preview and the send would
    otherwise change what leaves the machine without changing the hash the owner
    approved.
    """
    return (
        excerpt.segment_id,
        str(excerpt.start),
        str(excerpt.end),
        _text_hash(excerpt.text),
        excerpt.source_id,
        excerpt.display_name,
        excerpt.confidence,
        excerpt.locator,
    )


def selection_hash(identities: list[tuple[str, ...]]) -> str:
    """Hash over the ordered identity of every excerpt in the batch."""
    hasher = hashlib.sha256()
    for identity in identities:
        hasher.update("\x1e".join(identity).encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()


def _slice_of(text: str, start: int, limit: int) -> tuple[str, int]:
    """The next slice from *start*, ending on a boundary where one is near.

    Returns ``(slice, end)``. Prefers to break at a paragraph, line or space so
    an excerpt does not end mid-word, and swallows a short remaining tail rather
    than leaving it for a batch of its own.
    """
    remaining = len(text) - start
    # Fold a short tail into this slice rather than leaving it for a batch of
    # its own -- but only when the whole tail fits inside `limit`, which the
    # caller has already clamped to the remaining total budget. Folding past
    # `limit` would overrun the batch ceiling the disclosure quotes.
    if remaining <= limit:
        return text[start:], len(text)
    if remaining <= limit + MIN_TAIL_CHARS and remaining <= limit:
        return text[start:], len(text)
    window = text[start : start + limit]
    cut = window.rfind("\n\n")
    if cut < limit // 2:
        cut = window.rfind("\n")
    if cut < limit // 2:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = limit
    return text[start : start + cut], start + cut


def next_batch(
    connection: sqlite3.Connection,
    pile_id: str,
    *,
    max_excerpts: int = MAX_EXCERPTS,
    max_excerpt_chars: int = MAX_EXCERPT_CHARS,
    max_total_chars: int = MAX_TOTAL_EXCERPT_CHARS,
) -> Batch:
    """The next unsent character ranges, in stable document order.

    Document order, not sampling: a batch is a contiguous walk forward through
    text that has not been processed. Higher-confidence sources are walked first,
    which is a reading order only -- excerpts carry their own confidence and the
    verifier is told plainly that confidence says nothing about whether a claim
    is true.
    """
    totals = connection.execute(
        """
        SELECT COALESCE(SUM(g.char_count), 0) AS total,
               COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0) AS covered
          FROM source_segments g JOIN sources s ON s.id = g.source_id
         WHERE s.pile_id = ? AND s.excluded = 0 AND s.status = ?
        """,
        (pile_id, READABLE),
    ).fetchone()

    rows = connection.execute(
        """
        SELECT g.id AS segment_id, g.locator, g.text, g.covered_upto,
               s.id AS source_id, s.display_name, s.confidence
          FROM source_segments g JOIN sources s ON s.id = g.source_id
         WHERE s.pile_id = ? AND s.excluded = 0 AND s.status = ?
           AND g.covered_upto < LENGTH(g.text)
         ORDER BY CASE s.confidence WHEN 'high' THEN 0 WHEN 'mid' THEN 1 ELSE 2 END,
                  s.created_at, s.id, g.ordinal
        """,
        (pile_id, READABLE),
    ).fetchall()

    chosen: list[Excerpt] = []
    identities: list[tuple[str, ...]] = []
    budget = max_total_chars

    for row in rows:
        if len(chosen) >= max_excerpts or budget <= 0:
            break
        text = row["text"]
        cursor = max(0, min(row["covered_upto"], len(text)))
        # Several slices may come from one long segment within a single batch.
        while cursor < len(text) and len(chosen) < max_excerpts and budget > 0:
            limit = min(max_excerpt_chars, budget)
            piece, end = _slice_of(text, cursor, limit)
            if not piece.strip():
                # Whitespace-only run: nothing to send, but it must still be
                # counted as covered or the cursor would never pass it.
                cursor = end
                continue
            excerpt = Excerpt(
                source_id=row["source_id"],
                segment_id=row["segment_id"],
                display_name=row["display_name"],
                confidence=row["confidence"],
                locator=row["locator"],
                text=piece,
                start=cursor,
                end=end,
            )
            chosen.append(excerpt)
            identities.append(excerpt_identity(excerpt))
            budget -= len(piece)
            cursor = end

    return Batch(
        excerpts=tuple(chosen),
        selection_hash=selection_hash(identities),
        chars_total=totals["total"] or 0,
        chars_covered=totals["covered"] or 0,
    )


def record_batch(connection: sqlite3.Connection, pile_id: str, batch: Batch) -> str:
    """Persist a previewed batch and return its id (the consent record)."""
    batch_id = new_id("bat")
    ranges = [
        {"segment_id": e.segment_id, "start": e.start, "end": e.end}
        for e in batch.excerpts
    ]
    with transaction(connection) as tx:
        tx.execute(
            "INSERT INTO build_batches (id, pile_id, run_id, selection_hash, ranges,"
            " excerpt_count, excerpt_chars, status, created_at, committed_at)"
            " VALUES (?, ?, NULL, ?, ?, ?, ?, 'previewed', ?, NULL)",
            (
                batch_id,
                pile_id,
                batch.selection_hash,
                json.dumps(ranges),
                len(batch.excerpts),
                batch.excerpt_chars,
                utc_now(),
            ),
        )
    return batch_id


def batch_hash(connection: sqlite3.Connection, batch_id: str) -> str:
    row = connection.execute(
        "SELECT selection_hash FROM build_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("build batch", batch_id)
    return row["selection_hash"]


def resolve_batch(
    connection: sqlite3.Connection, *, pile_id: str, batch_id: str, expected_hash: str
) -> list[Excerpt]:
    """Re-derive a previewed batch's exact slices, refusing a stale selection.

    Everything is re-read and re-hashed rather than trusted from the request:
    the batch belongs to this pile, has not already run, quotes the hash that was
    previewed, and every named range still resolves to the same characters --
    same text, same filename, same confidence, same locator -- in an included,
    readable source that has not already covered them.

    Read-only. :func:`claim_batch` is the same check plus the state change, so a
    caller can validate before starting a run without spending the batch.
    """
    row = connection.execute(
        "SELECT * FROM build_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("build batch", batch_id)
    if row["pile_id"] != pile_id:
        raise ConflictError("batch_mismatch", "That batch belongs to a different pile.")
    if row["status"] != "previewed":
        raise ConflictError(
            "batch_spent",
            "That batch has already been sent. Take a fresh preview of what is left.",
        )
    if row["selection_hash"] != expected_hash:
        raise _stale()

    excerpts: list[Excerpt] = []
    identities: list[tuple[str, ...]] = []
    for entry in json.loads(row["ranges"]):
        found = connection.execute(
            """
            SELECT g.id, g.locator, g.text, g.covered_upto, s.id AS source_id,
                   s.display_name, s.confidence
              FROM source_segments g JOIN sources s ON s.id = g.source_id
             WHERE g.id = ? AND s.excluded = 0 AND s.status = ?
            """,
            (entry["segment_id"], READABLE),
        ).fetchone()
        if found is None or found["covered_upto"] > entry["start"]:
            raise _stale()
        piece = found["text"][entry["start"] : entry["end"]]
        if not piece:
            raise _stale()
        excerpt = Excerpt(
            source_id=found["source_id"],
            segment_id=found["id"],
            display_name=found["display_name"],
            confidence=found["confidence"],
            locator=found["locator"],
            text=piece,
            start=entry["start"],
            end=entry["end"],
        )
        excerpts.append(excerpt)
        identities.append(excerpt_identity(excerpt))

    if selection_hash(identities) != expected_hash:
        raise _stale()
    return excerpts


def claim_batch(
    connection: sqlite3.Connection, *, pile_id: str, batch_id: str, expected_hash: str
) -> list[Excerpt]:
    """Validate a batch and mark it running. The send path."""
    excerpts = resolve_batch(
        connection, pile_id=pile_id, batch_id=batch_id, expected_hash=expected_hash
    )
    with transaction(connection) as tx:
        tx.execute("UPDATE build_batches SET status = 'running' WHERE id = ?", (batch_id,))
    return excerpts


def _stale() -> ConflictError:
    return ConflictError(
        "selection_changed",
        "The material changed since you looked at it. Take a fresh preview so you "
        "can see exactly what would be sent.",
    )


def attach_run(connection: sqlite3.Connection, batch_id: str, run_id: str) -> None:
    with transaction(connection) as tx:
        tx.execute("UPDATE build_batches SET run_id = ? WHERE id = ?", (run_id, batch_id))


def commit_batch(connection: sqlite3.Connection, batch_id: str) -> int:
    """Advance the character cursors. Called ONLY after validated output is stored.

    Runs inside the caller's transaction when there is one (``db.transaction``
    nests via SAVEPOINT), so coverage and the material it produced commit or roll
    back as one unit. A batch that failed leaves every cursor where it was.
    """
    row = connection.execute(
        "SELECT ranges, status FROM build_batches WHERE id = ?", (batch_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("build batch", batch_id)
    if row["status"] == "committed":
        return 0
    now = utc_now()
    advanced = 0
    with transaction(connection) as tx:
        for entry in json.loads(row["ranges"]):
            advanced += tx.execute(
                "UPDATE source_segments SET covered_upto = ?, covered_at = ?, covered_by = ?"
                " WHERE id = ? AND covered_upto < ?",
                (entry["end"], now, batch_id, entry["segment_id"], entry["end"]),
            ).rowcount
        tx.execute(
            "UPDATE build_batches SET status = 'committed', committed_at = ? WHERE id = ?",
            (now, batch_id),
        )
    return advanced


def reopen_coverage(
    connection: sqlite3.Connection,
    *,
    pile_id: str,
    source_id: str | None = None,
) -> dict[str, int]:
    """Rewind coverage so material can be sent through a build again.

    Without this, a pile whose passages have all been covered is a dead end:
    exclude a source, re-include it, re-upload the identical file, and there is
    nothing left to offer because every cursor is at the end. Coverage is a
    record of what has been *processed*, not a permanent ban.

    What this does NOT do is release anything. Held points and questions stay
    held; re-including a source does not restore eligibility. It only makes the
    text available to a fresh preview, which the owner still has to read and
    consent to, and whose output still has to earn its way back through
    verification and assessment.
    """
    now = utc_now()
    with transaction(connection) as tx:
        if source_id is None:
            reset = tx.execute(
                "UPDATE source_segments SET covered_upto = 0, covered_at = NULL,"
                " covered_by = NULL WHERE source_id IN ("
                "  SELECT id FROM sources WHERE pile_id = ? AND excluded = 0"
                "    AND status = ?) AND covered_upto > 0",
                (pile_id, READABLE),
            ).rowcount
        else:
            reset = tx.execute(
                "UPDATE source_segments SET covered_upto = 0, covered_at = NULL,"
                " covered_by = NULL WHERE source_id = ? AND covered_upto > 0",
                (source_id,),
            ).rowcount
        # Any batch still sitting in `previewed` refers to ranges that have just
        # moved. Retiring them means a stale preview cannot be sent.
        tx.execute(
            "UPDATE build_batches SET status = 'cancelled' WHERE pile_id = ?"
            " AND status = 'previewed'",
            (pile_id,),
        )
        del now
    return {"segments_reopened": max(reset, 0)}


def close_batch(connection: sqlite3.Connection, batch_id: str, *, status: str) -> None:
    """Mark a batch failed or cancelled, leaving every cursor untouched."""
    if status not in {"failed", "cancelled"}:
        raise ValueError(f"unknown batch status {status!r}")
    with transaction(connection) as tx:
        tx.execute(
            "UPDATE build_batches SET status = ? WHERE id = ? AND status != 'committed'",
            (status, batch_id),
        )


# --- summaries ---------------------------------------------------------------


def pile_coverage(connection: sqlite3.Connection, pile_id: str) -> Coverage:
    row = connection.execute(
        """
        SELECT COALESCE(SUM(g.char_count), 0) AS total,
               COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0) AS covered
          FROM source_segments g JOIN sources s ON s.id = g.source_id
         WHERE s.pile_id = ? AND s.excluded = 0 AND s.status = ?
        """,
        (pile_id, READABLE),
    ).fetchone()
    return Coverage(chars_total=row["total"] or 0, chars_covered=row["covered"] or 0)


def pile_source_summary(connection: sqlite3.Connection, pile_id: str) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT status, excluded, COUNT(*) AS n FROM sources WHERE pile_id = ?"
        " GROUP BY status, excluded",
        (pile_id,),
    ).fetchall()
    coverage = pile_coverage(connection, pile_id)
    return {
        "total": sum(row["n"] for row in rows),
        "usable": sum(
            row["n"] for row in rows if row["status"] == READABLE and not row["excluded"]
        ),
        "needs_attention": sum(
            row["n"] for row in rows if row["status"] in ATTENTION_STATUSES
        ),
        "excluded": sum(row["n"] for row in rows if row["excluded"]),
        "coverage": coverage.as_dict(),
    }


def sources_needing_attention(
    connection: sqlite3.Connection, pile_id: str
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT id, display_name, status, status_detail FROM sources"
        " WHERE pile_id = ? AND status IN (?, ?, ?) ORDER BY created_at DESC",
        (pile_id, *ATTENTION_STATUSES),
    ).fetchall()
    return [
        {
            "source_id": row["id"],
            "display_name": row["display_name"],
            "status": row["status"],
            "status_detail": row["status_detail"],
        }
        for row in rows
    ]


def library_summary(connection: sqlite3.Connection) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT status, excluded, COUNT(*) AS n FROM sources GROUP BY status, excluded"
    ).fetchall()
    totals = connection.execute(
        """
        SELECT COALESCE(SUM(g.char_count), 0) AS total,
               COALESCE(SUM(MIN(g.covered_upto, g.char_count)), 0) AS covered
          FROM source_segments g JOIN sources s ON s.id = g.source_id
         WHERE s.excluded = 0 AND s.status = ?
        """,
        (READABLE,),
    ).fetchone()
    return {
        "total": sum(row["n"] for row in rows),
        "usable": sum(
            row["n"] for row in rows if row["status"] == READABLE and not row["excluded"]
        ),
        "needs_attention": sum(
            row["n"] for row in rows if row["status"] in ATTENTION_STATUSES
        ),
        "coverage": Coverage(
            chars_total=totals["total"] or 0, chars_covered=totals["covered"] or 0
        ).as_dict(),
    }


def confidence_summary(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    piles = connection.execute(
        "SELECT tier, COUNT(*) AS n FROM piles GROUP BY tier"
    ).fetchall()
    sources = connection.execute(
        "SELECT confidence, COUNT(*) AS n FROM sources GROUP BY confidence"
    ).fetchall()
    pile_counts = {row["tier"]: row["n"] for row in piles}
    source_counts = {row["confidence"]: row["n"] for row in sources}
    return [
        {
            "confidence": confidence,
            "label": CONFIDENCE_LABEL[confidence],
            "meaning": CONFIDENCE_MEANING[confidence],
            "pile_count": pile_counts.get(confidence, 0),
            "source_count": source_counts.get(confidence, 0),
        }
        for confidence in CONFIDENCES
    ]
