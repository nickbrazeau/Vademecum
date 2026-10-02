"""Source intake: what a file is, what text is inside it, and what pictures.

Nothing in this package touches the database, the network or a model. It takes
bytes and a claimed filename and returns located text, the pictures worth
keeping, or a named reason it could not. That separation is what lets the
extractors be tested with fixture files and no application around them.

``extract_all`` is the whole read: text, with on-device recognition for pages
and slides that have no text layer when the machine can do it (ADR 0013), and
pictures, with text-less PDF pages kept as rendered images so they can still
be looked at.
"""

from __future__ import annotations

from .detect import UnsupportedUpload, detect, safe_display_name
from .extract import (
    STATUS_ENCRYPTED,
    STATUS_EXTRACTED,
    STATUS_NEEDS_OCR,
    STATUS_UNREADABLE,
    Extraction,
    Segment,
    extract,
)
from .images import ExtractedImage, extract_images, slide_pictures


def extract_all(kind: str, payload: bytes, *, recognise: bool = True) -> tuple[Extraction, list[ExtractedImage]]:
    """Text and pictures for one file. ``recognise=False`` skips on-device OCR."""
    from . import vision

    reader = None
    rendered: dict[int, bytes] = {}
    if recognise and vision.available():
        if kind == "pdf":
            reader = vision.PdfPageReader(payload)
        elif kind == "pptx":
            pictures = slide_pictures(payload)
            read_images = vision.images_ocr()
            if read_images is not None:
                reader = lambda position: read_images(pictures.get(position, []))  # noqa: E731
        elif kind == "image":
            read_images = vision.images_ocr()
            if read_images is not None:
                reader = lambda _position: read_images([payload])  # noqa: E731
    extraction = extract(kind, payload, ocr=reader)
    if reader is not None and hasattr(reader, "rendered_text_less"):
        rendered = reader.rendered_text_less(extraction)
    images = extract_images(kind, payload, rendered_pages=rendered)
    return extraction, images


__all__ = [
    "Extraction",
    "ExtractedImage",
    "STATUS_ENCRYPTED",
    "STATUS_EXTRACTED",
    "STATUS_NEEDS_OCR",
    "STATUS_UNREADABLE",
    "Segment",
    "UnsupportedUpload",
    "detect",
    "extract",
    "extract_all",
    "extract_images",
    "safe_display_name",
]
