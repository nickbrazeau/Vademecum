"""Every bound an upload is held to, in one place.

Each closes a way a file can become a problem for the process reading it:
a byte ceiling, archive entry/expansion ceilings (OOXML files are ZIPs), a
page/slide ceiling, and per-segment/per-document character ceilings.

Exceeding a bound is a refusal or a *recorded* truncation, never a silent one.
"""

from __future__ import annotations

# Raised from 40 MB on 2026-10-01: a recorded lecture deck with its pictures
# and clips is routinely over 100 MB, and the owner's folder held one.
MAX_UPLOAD_BYTES = 250 * 1024 * 1024

# OOXML containers are ZIPs. python-pptx/python-docx apply none of these.
# Kept in proportion to the upload ceiling: ten times it for the whole
# expansion, a little under it for one part (an embedded clip is one part).
MAX_ARCHIVE_ENTRIES = 4000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2500 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 200 * 1024 * 1024

# Visible units: pages, slides, sections. Speaker notes are not counted here.
MAX_UNITS = 2000

# A single stored segment. A unit longer than this becomes several continuation
# segments rather than being cut: coverage is the point (see extract._split).
MAX_SEGMENT_CHARS = 12_000

# All extracted text for one document. Generous because this is local storage,
# not transmission -- what a model sees is bounded separately, per batch, by
# storage/sources.py.
MAX_DOCUMENT_CHARS = 8_000_000

# Below this a unit's "text" is noise (a page number, a stray ligature) rather
# than content. Used to tell an image-only page from one that genuinely has
# little on it, and reported either way.
MIN_UNIT_CHARS_FOR_TEXT = 12

MAX_FILENAME_CHARS = 180
