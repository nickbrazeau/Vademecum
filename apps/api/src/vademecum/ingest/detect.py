"""What a file actually is, and what it may safely be called.

Two jobs, both of which exist because the browser's answer to either question is
a claim rather than a fact.

**Format.** ``Content-Type`` and the extension both come from the client. This
module reads the bytes: a PDF starts ``%PDF-``, an OOXML container is a ZIP whose
members say which OOXML it is. A ``.pdf`` that is really a ZIP is refused as a
mismatch rather than handed to a PDF parser, and a text file is only text if it
decodes as UTF-8.

**Filename.** The uploaded name is display text and nothing else. It never
becomes a path: stored files are named after their own SHA-256, so a name
containing ``../``, a NUL, a drive letter or 4 kB of Unicode cannot reach the
filesystem even in principle. It is still sanitised, because it is shown on
screen and written into an export.
"""

from __future__ import annotations

import io
import unicodedata
import zipfile
from dataclasses import dataclass

from .limits import MAX_FILENAME_CHARS

# What Vademecum can read, as a closed set.
KIND_PDF = "pdf"
KIND_PPTX = "pptx"
KIND_DOCX = "docx"
KIND_TEXT = "text"
KIND_MARKDOWN = "markdown"
# A standalone picture: a photographed page, a screenshot, a figure (ADR 0013).
KIND_IMAGE = "image"

MEDIA_TYPES = {
    KIND_PDF: "application/pdf",
    KIND_PPTX: (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    KIND_DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    KIND_TEXT: "text/plain",
    KIND_MARKDOWN: "text/markdown",
    KIND_IMAGE: "image/png",
}

# Extensions that are *offered*. The extension picks which check runs; the bytes
# decide whether it passes.
EXTENSIONS = {
    ".pdf": KIND_PDF,
    ".pptx": KIND_PPTX,
    ".docx": KIND_DOCX,
    ".txt": KIND_TEXT,
    ".text": KIND_TEXT,
    ".md": KIND_MARKDOWN,
    ".markdown": KIND_MARKDOWN,
    ".png": KIND_IMAGE,
    ".jpg": KIND_IMAGE,
    ".jpeg": KIND_IMAGE,
}

# Formats a person will reasonably try, with a sentence saying what to do
# instead. Refusing with advice is a different experience from refusing.
UNSUPPORTED_ADVICE = {
    ".ppt": (
        "Vademecum reads .pptx, not the older binary .ppt. Open the deck in "
        "Keynote or PowerPoint and use File → Save As (or Export) to make a "
        ".pptx, then upload that."
    ),
    ".doc": (
        "Vademecum reads .docx, not the older binary .doc. Open it in Word or "
        "Pages and save a .docx copy."
    ),
    ".pages": "Export the document as PDF or .docx and upload that.",
    ".key": "Export the deck as PDF or .pptx and upload that.",
    ".rtf": "Save the document as .docx, PDF, or plain text and upload that.",
    ".epub": "Export the chapter you want as PDF and upload that.",
}

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

# The member every OOXML container has, and the part name that says which one it
# is. Read from the central directory only -- nothing is decompressed here.
OOXML_CONTENT_TYPES = "[Content_Types].xml"
OOXML_MARKERS = {
    KIND_PPTX: "ppt/presentation.xml",
    KIND_DOCX: "word/document.xml",
}


class UnsupportedUpload(ValueError):
    """A file Vademecum will not accept, with a reason a person can act on."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class Detected:
    kind: str
    media_type: str


def safe_display_name(raw: str | None) -> str:
    """A filename that is safe to show, store as text, and put in an export.

    Not safe to use as a path -- nothing here makes it one, and nothing in
    Vademecum joins it to a directory. Path separators are replaced rather than
    stripped so that ``a/b.pdf`` reads as ``a_b.pdf`` instead of silently
    becoming ``b.pdf``, which would hide a traversal attempt rather than
    neutralise it visibly.
    """
    candidate = (raw or "").strip()
    if not candidate:
        return "upload"
    # NFC first: two different encodings of the same accented name should not
    # produce two different display names.
    candidate = unicodedata.normalize("NFC", candidate)
    cleaned: list[str] = []
    for character in candidate:
        category = unicodedata.category(character)
        if character in "/\\:":
            cleaned.append("_")
        elif category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            # Control, format (including the right-to-left override used to
            # disguise extensions), surrogate, private-use and unassigned.
            cleaned.append("_")
        else:
            cleaned.append(character)
    name = "".join(cleaned).strip(" .")
    # `..` cannot survive, even though it could not do anything here.
    while ".." in name:
        name = name.replace("..", ".")
    name = name.strip(" .")
    if not name:
        return "upload"
    if len(name) > MAX_FILENAME_CHARS:
        head, _, tail = name.rpartition(".")
        if head and 0 < len(tail) <= 12:
            keep = MAX_FILENAME_CHARS - len(tail) - 1
            name = f"{head[:keep]}.{tail}"
        else:
            name = name[:MAX_FILENAME_CHARS]
    return name


def extension_of(display_name: str) -> str:
    _, dot, tail = display_name.rpartition(".")
    return f".{tail.lower()}" if dot else ""


def _ooxml_kind(payload: bytes) -> str | None:
    """Which OOXML this container is, read from its member names.

    Only the central directory is parsed. ``namelist()`` does not decompress,
    so a container that would expand to gigabytes is identified without any of
    it being expanded.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
    except (zipfile.BadZipFile, OSError, ValueError):
        return None
    if OOXML_CONTENT_TYPES not in names:
        return None
    for kind, marker in OOXML_MARKERS.items():
        if marker in names:
            return kind
    return None


def detect(display_name: str, payload: bytes) -> Detected:
    """What *payload* is, refusing anything the bytes do not agree with.

    The extension chooses the expectation and the bytes have to meet it. A
    ``.pdf`` full of ZIP is not quietly re-read as a deck: a file whose name and
    contents disagree is a file the owner should look at, and saying so is more
    useful than guessing correctly.
    """
    extension = extension_of(display_name)

    if extension in UNSUPPORTED_ADVICE:
        raise UnsupportedUpload("unsupported_format", UNSUPPORTED_ADVICE[extension])

    kind = EXTENSIONS.get(extension)
    if kind is None:
        raise UnsupportedUpload(
            "unsupported_format",
            "Vademecum reads PDF, PowerPoint (.pptx), Word (.docx), plain text or "
            "Markdown, and pictures (.png, .jpg). Convert this file to one of those "
            "and try again.",
        )

    if not payload:
        raise UnsupportedUpload("empty_file", "That file is empty.")

    if kind == KIND_PDF:
        # The header is allowed a small offset: some producers prepend bytes and
        # every reader in the world tolerates it.
        if PDF_MAGIC not in payload[:1024]:
            raise UnsupportedUpload(
                "format_mismatch",
                "That file is named .pdf but does not contain a PDF. Check "
                "whether it was renamed rather than converted.",
            )
        return Detected(kind, MEDIA_TYPES[kind])

    if kind == KIND_IMAGE:
        if payload.startswith(PNG_MAGIC):
            return Detected(kind, "image/png")
        if payload.startswith(JPEG_MAGIC):
            return Detected(kind, "image/jpeg")
        raise UnsupportedUpload(
            "format_mismatch",
            f"That file is named {extension} but is not a PNG or JPEG picture. "
            "Check whether it was renamed rather than converted.",
        )

    if kind in {KIND_PPTX, KIND_DOCX}:
        if not payload.startswith(ZIP_MAGIC):
            raise UnsupportedUpload(
                "format_mismatch",
                f"That file is named {extension} but is not an Office document. "
                "Check whether it was renamed rather than converted.",
            )
        actual = _ooxml_kind(payload)
        if actual is None:
            raise UnsupportedUpload(
                "format_mismatch",
                f"That file is named {extension} but Vademecum could not find "
                "an Office document inside it.",
            )
        if actual != kind:
            raise UnsupportedUpload(
                "format_mismatch",
                "That file's contents do not match its name: it looks like a "
                f"{actual} rather than a {kind}. Rename it and try again.",
            )
        return Detected(kind, MEDIA_TYPES[kind])

    # Text and Markdown: it is text if it decodes.
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UnsupportedUpload(
            "format_mismatch",
            "That file is named as text but is not valid UTF-8. If it is a "
            "PDF or a deck, give it the right extension.",
        ) from exc
    if b"\x00" in payload:
        raise UnsupportedUpload(
            "format_mismatch",
            "That file is named as text but contains binary data.",
        )
    return Detected(kind, MEDIA_TYPES[kind])
