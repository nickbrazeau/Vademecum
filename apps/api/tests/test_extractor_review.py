"""Regression tests for three ways the extractor lied about what it had read.

Every document here is built in memory from XML strings, so a failure points at
the extractor and not at a fixture. Nothing in this module opens a socket, a
model, or a file on disk.

The three defects, each of which produced a *confident* wrong answer rather than
an error:

1. A unit shorter than ``MIN_UNIT_CHARS_FOR_TEXT`` was discarded as an image
   wherever it appeared. The five-character tail of a 30,005-character paragraph
   was silently dropped, and an eleven-character text file was reported as a
   scan.
2. Slides whose parts could not be resolved were filtered out *before* the
   positions were numbered, so a deck missing slide 1 presented slide 2 as
   "slide 1" and reported itself completely read.
3. Speaker notes shared their slide's unit index and were counted as text for
   it, so readable notes erased the failed/image-only state of a slide nobody
   could actually read.
4. ``extract_pdf`` imported ``pypdf.errors.PdfError``, which does not exist in
   the installed pypdf (it is ``PyPdfError``). The ``ImportError`` fired before
   the reader ran and ``extract`` swallowed it, so every PDF ever uploaded came
   back "Vademecum could not read this file". The PDFs here are built with the
   installed pypdf and read back through the real code path, because source
   inspection is exactly what missed this.
"""

from __future__ import annotations

import io
import zipfile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from vademecum.ingest import (
    STATUS_ENCRYPTED,
    STATUS_EXTRACTED,
    STATUS_NEEDS_OCR,
    STATUS_UNREADABLE,
    extract,
)
from vademecum.ingest.extract import extract_pdf
from vademecum.ingest.limits import MAX_SEGMENT_CHARS, MIN_UNIT_CHARS_FOR_TEXT

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_SLIDE = f"{R}/slide"
REL_NOTES = f"{R}/notesSlide"


# --- in-memory documents -----------------------------------------------------


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def _rels(*entries: tuple[str, ...]) -> str:
    """``(rId, type, target[, TargetMode])`` tuples as a relationships part."""
    items = []
    for entry in entries:
        rid, kind, target = entry[0], entry[1], entry[2]
        mode = f' TargetMode="{entry[3]}"' if len(entry) > 3 else ""
        items.append(f'<Relationship Id="{rid}" Type="{kind}" Target="{target}"{mode}/>')
    return (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(items)
        + "</Relationships>"
    )


def _presentation(*rids: str) -> str:
    slides = "".join(
        f'<p:sldId id="{256 + number}" r:id="{rid}"/>' for number, rid in enumerate(rids)
    )
    return (
        '<?xml version="1.0"?>'
        f'<p:presentation xmlns:p="{P}" xmlns:r="{R}">'
        f"<p:sldIdLst>{slides}</p:sldIdLst>"
        "</p:presentation>"
    )


def _slide(*texts: str) -> str:
    body = "".join(f"<a:t>{text}</a:t>" for text in texts)
    return (
        '<?xml version="1.0"?>'
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>'
    )


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    return _zip(
        {
            "[Content_Types].xml": '<?xml version="1.0"?><Types/>',
            "word/document.xml": (
                f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
            ),
        }
    )


def _pdf(*pages: str | None, password: str | None = None) -> bytes:
    """A real PDF with a real text layer, built by the installed pypdf.

    ``None`` is a page with no content stream at all -- the shape a scanned or
    image-exported page has once its image is stripped, and the thing an
    image-only page must be distinguished from. No new dependency: the writer
    is the same library the extractor reads with.
    """
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        if text is None:
            continue
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
    if password is not None:
        writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _locators(extraction) -> list[str]:
    return [segment.locator for segment in extraction.segments]


def _joined(extraction) -> str:
    return "".join(segment.text for segment in extraction.segments)


# --- 0. the PDF path actually runs -------------------------------------------

PAGE_ONE = "Alpha beta gamma delta epsilon zeta eta theta"
PAGE_TWO = "Iota kappa lambda mu nu xi omicron pi rho"


def test_extract_pdf_runs_at_all() -> None:
    """Called directly, so `extract`'s blanket except cannot hide an ImportError.

    This is the whole defect: every failure mode below still *looked* handled
    because the generic handler produced a polite message about a stored file.
    """
    extraction = extract_pdf(_pdf(PAGE_ONE))

    assert extraction.status == STATUS_EXTRACTED
    assert extraction.segments  # the reader ran; it did not raise on import


def test_pdf_text_layer_is_read_with_the_right_page_locator() -> None:
    extraction = extract("pdf", _pdf(PAGE_ONE, PAGE_TWO))

    assert extraction.status == STATUS_EXTRACTED
    assert extraction.unit_kind == "page"
    assert _locators(extraction) == ["page 1", "page 2"]
    assert PAGE_ONE in extraction.segments[0].text
    assert PAGE_TWO in extraction.segments[1].text
    assert extraction.segments[0].unit_index == 1
    assert extraction.segments[1].unit_index == 2
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_with_text == 2
    assert extraction.coverage.complete


def test_pdf_blank_page_is_counted_as_unread_not_as_read() -> None:
    """Page 1 has a text layer, page 2 is the shape a scanned page has."""
    extraction = extract("pdf", _pdf(PAGE_ONE, None))

    assert extraction.status == STATUS_EXTRACTED
    assert _locators(extraction) == ["page 1"]
    assert extraction.unit_count == 2
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_with_text == 1
    assert extraction.coverage.units_image_only == 1
    assert extraction.coverage.units_failed == 0
    assert not extraction.coverage.complete
    assert "images or scans" in extraction.detail


def test_a_pdf_with_no_text_layer_at_all_asks_for_ocr() -> None:
    extraction = extract("pdf", _pdf(None, None))

    assert extraction.status == STATUS_NEEDS_OCR
    assert extraction.segments == ()
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_image_only == 2
    assert "OCR" in extraction.detail


def test_password_protected_pdf_is_named_encrypted_and_not_guessed_at() -> None:
    extraction = extract("pdf", _pdf(PAGE_ONE, password="hunter2"))

    assert extraction.status == STATUS_ENCRYPTED
    assert extraction.segments == ()
    assert "password" in extraction.detail
    assert PAGE_ONE not in extraction.detail


def test_a_malformed_pdf_fails_by_name_rather_than_raising() -> None:
    extraction = extract_pdf(b"%PDF-1.7\nthis is not a cross-reference table\n%%EOF\n")

    assert extraction.status == STATUS_UNREADABLE
    assert "PDF" in extraction.detail
    assert extraction.segments == ()


def test_a_truncated_pdf_fails_by_name_too() -> None:
    """Half a real PDF: the reader raises, and it must be caught by class, not name."""
    payload = _pdf(PAGE_ONE, PAGE_TWO)
    extraction = extract_pdf(payload[: len(payload) // 2])

    assert extraction.status == STATUS_UNREADABLE
    assert extraction.segments == ()


def test_the_pdf_path_never_returns_the_generic_unreadable_message() -> None:
    """The message the ImportError produced. Seeing it again means it is back."""
    generic = "Vademecum could not read this file"
    for payload in (_pdf(PAGE_ONE), _pdf(None), _pdf(PAGE_ONE, password="hunter2")):
        assert generic not in extract("pdf", payload).detail


# --- 1. short units are not all scans ----------------------------------------


def test_thirty_thousand_character_paragraph_keeps_its_five_character_tail() -> None:
    """The remainder of a long paragraph is text, not an image of one."""
    body = "a" * 30_005
    extraction = extract("text", body.encode())

    assert extraction.status == STATUS_EXTRACTED
    assert extraction.char_count == 30_005
    assert _joined(extraction) == body
    assert extraction.coverage.units_image_only == 0
    assert extraction.coverage.units_with_text == extraction.coverage.units_total
    assert extraction.coverage.complete


def test_docx_paragraph_tail_is_kept_too() -> None:
    body = "b" * 30_005
    extraction = extract("docx", _docx(body))

    assert extraction.status == STATUS_EXTRACTED
    assert _joined(extraction) == body
    assert extraction.coverage.complete


def test_short_text_file_is_not_called_a_scan() -> None:
    body = "Short note."  # deliberately below MIN_UNIT_CHARS_FOR_TEXT
    assert len(body) < MIN_UNIT_CHARS_FOR_TEXT

    extraction = extract("text", body.encode())

    assert extraction.status == STATUS_EXTRACTED
    assert _joined(extraction) == body
    assert extraction.coverage.units_with_text == 1
    assert extraction.coverage.units_image_only == 0
    assert extraction.coverage.complete


def test_short_docx_section_is_not_called_a_scan() -> None:
    extraction = extract("docx", _docx("Fine."))

    assert extraction.status == STATUS_EXTRACTED
    assert _joined(extraction) == "Fine."
    assert extraction.coverage.complete


def test_a_genuinely_empty_text_file_is_still_unreadable() -> None:
    """Loosening the threshold must not turn whitespace into content."""
    extraction = extract("text", b"   \n\n \t \n")

    assert extraction.status == STATUS_UNREADABLE
    assert extraction.segments == ()
    assert extraction.coverage.units_total == 0


def test_a_slide_with_only_a_page_number_is_still_image_only() -> None:
    """The threshold still applies where "almost no text" can mean a picture."""
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide1.xml"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
            "ppt/slides/slide1.xml": _slide("p. 3"),
            "ppt/slides/slide2.xml": _slide("Readable slide content."),
        }
    )
    extraction = extract("pptx", deck)

    assert extraction.status == STATUS_EXTRACTED
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_image_only == 1
    assert extraction.coverage.units_with_text == 1
    assert not extraction.coverage.complete
    assert "images or scans" in extraction.detail


def test_long_slide_becomes_continuation_segments_without_loss() -> None:
    body = "c" * (MAX_SEGMENT_CHARS + 5)
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1"),
            "ppt/_rels/presentation.xml.rels": _rels(("rId1", REL_SLIDE, "slides/slide1.xml")),
            "ppt/slides/slide1.xml": _slide(body),
        }
    )
    extraction = extract("pptx", deck)

    assert _locators(extraction) == ["slide 1", "slide 1 (cont. 2)"]
    assert {segment.unit_index for segment in extraction.segments} == {1}
    assert _joined(extraction) == body
    assert extraction.coverage.units_total == 1
    assert extraction.coverage.complete


# --- 2. slide positions survive an unresolvable slide -------------------------


def test_missing_slide_part_does_not_renumber_the_slides_after_it() -> None:
    """slide1 is listed but absent: slide 2 must not be presented as slide 1."""
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide1.xml"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
            # slides/slide1.xml deliberately absent from the container.
            "ppt/slides/slide2.xml": _slide("The second slide, and it is readable."),
        }
    )
    extraction = extract("pptx", deck)

    assert extraction.status == STATUS_EXTRACTED
    assert _locators(extraction) == ["slide 2"]
    assert extraction.unit_count == 2
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_failed == 1
    assert extraction.coverage.units_with_text == 1
    assert not extraction.coverage.complete
    assert "could not be read" in extraction.detail


def test_unresolvable_slide_relationship_is_recorded_as_a_failed_slide() -> None:
    """A `p:sldId` whose rId is not in the relationships part still holds a place."""
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId9", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(("rId2", REL_SLIDE, "slides/slide2.xml")),
            "ppt/slides/slide2.xml": _slide("The second slide, and it is readable."),
        }
    )
    extraction = extract("pptx", deck)

    assert _locators(extraction) == ["slide 2"]
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_failed == 1
    assert not extraction.coverage.complete


def test_external_relationship_is_refused_and_leaves_a_failed_slide() -> None:
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "http://example.invalid/slide1.xml", "External"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
            "ppt/slides/slide2.xml": _slide("The second slide, and it is readable."),
        }
    )
    extraction = extract("pptx", deck)

    assert _locators(extraction) == ["slide 2"]
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_failed == 1


def test_entity_declaration_in_a_slide_is_refused_not_expanded() -> None:
    poisoned = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE sld [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        f'<p:sld xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        "<a:t>&xxe;</a:t>"
        "</p:spTree></p:cSld></p:sld>"
    )
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide1.xml"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
            "ppt/slides/slide1.xml": poisoned,
            "ppt/slides/slide2.xml": _slide("The second slide, and it is readable."),
        }
    )
    extraction = extract("pptx", deck)

    assert "root:" not in _joined(extraction)
    assert "passwd" not in _joined(extraction)
    assert _locators(extraction) == ["slide 2"]
    assert extraction.coverage.units_failed == 1
    assert extraction.coverage.units_total == 2


def test_presentation_order_wins_over_slide_file_numbers() -> None:
    """Order comes from `p:sldIdLst`, and notes follow their owning slide."""
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide2.xml"),
                ("rId2", REL_SLIDE, "slides/slide1.xml"),
            ),
            "ppt/slides/slide1.xml": _slide("Content of the slide numbered one."),
            "ppt/slides/slide2.xml": _slide("Content of the slide numbered two."),
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", REL_NOTES, "../notesSlides/notesSlide1.xml")
            ),
            "ppt/slides/_rels/slide2.xml.rels": _rels(
                ("rId1", REL_NOTES, "../notesSlides/notesSlide2.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": _slide("Notes belonging to slide file one."),
            "ppt/notesSlides/notesSlide2.xml": _slide("Notes belonging to slide file two."),
        }
    )
    extraction = extract("pptx", deck)

    assert _locators(extraction) == [
        "slide 1",
        "slide 1 (speaker notes)",
        "slide 2",
        "slide 2 (speaker notes)",
    ]
    texts = [segment.text for segment in extraction.segments]
    assert texts[0] == "Content of the slide numbered two."
    assert texts[1] == "Notes belonging to slide file two."
    assert texts[2] == "Content of the slide numbered one."
    assert texts[3] == "Notes belonging to slide file one."
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.complete


def test_a_deck_whose_slides_are_all_missing_is_unreadable_not_a_scan() -> None:
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide1.xml"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
        }
    )
    extraction = extract("pptx", deck)

    assert extraction.status == STATUS_UNREADABLE
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_failed == 2
    assert not extraction.coverage.complete


# --- 3. notes are not evidence that the slide was read ------------------------


def test_readable_notes_do_not_mark_a_missing_slide_as_read() -> None:
    """The slide part is gone; its notes part is not. Only the notes were read."""
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1", "rId2"),
            "ppt/_rels/presentation.xml.rels": _rels(
                ("rId1", REL_SLIDE, "slides/slide1.xml"),
                ("rId2", REL_SLIDE, "slides/slide2.xml"),
            ),
            # slides/slide1.xml is absent; its relationships part is not.
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", REL_NOTES, "../notesSlides/notesSlide1.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": _slide("Notes for the slide that went missing."),
            "ppt/slides/slide2.xml": _slide("The second slide, and it is readable."),
        }
    )
    extraction = extract("pptx", deck)

    # The notes survive as a usable segment, attributed to slide 1.
    assert _locators(extraction) == ["slide 1 (speaker notes)", "slide 2"]
    assert extraction.segments[0].text == "Notes for the slide that went missing."
    assert extraction.segments[0].unit_index == 1
    # ...but slide 1 itself was never read.
    assert extraction.coverage.units_total == 2
    assert extraction.coverage.units_with_text == 1
    assert extraction.coverage.units_failed == 1
    assert not extraction.coverage.complete


def test_readable_notes_do_not_mark_an_image_only_slide_as_read() -> None:
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1"),
            "ppt/_rels/presentation.xml.rels": _rels(("rId1", REL_SLIDE, "slides/slide1.xml")),
            "ppt/slides/slide1.xml": _slide(),  # a picture, no text runs at all
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", REL_NOTES, "../notesSlides/notesSlide1.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": _slide("Spoken commentary for a picture slide."),
        }
    )
    extraction = extract("pptx", deck)

    assert extraction.status == STATUS_EXTRACTED
    assert _locators(extraction) == ["slide 1 (speaker notes)"]
    assert extraction.coverage.units_total == 1
    assert extraction.coverage.units_with_text == 0
    assert extraction.coverage.units_image_only == 1
    assert not extraction.coverage.complete
    assert "1 of 1 slides had no readable text" in extraction.detail


def test_notes_are_never_counted_as_slides() -> None:
    deck = _zip(
        {
            "ppt/presentation.xml": _presentation("rId1"),
            "ppt/_rels/presentation.xml.rels": _rels(("rId1", REL_SLIDE, "slides/slide1.xml")),
            "ppt/slides/slide1.xml": _slide("Visible content on the only slide."),
            "ppt/slides/_rels/slide1.xml.rels": _rels(
                ("rId1", REL_NOTES, "../notesSlides/notesSlide1.xml")
            ),
            "ppt/notesSlides/notesSlide1.xml": _slide("Speaker notes for the only slide."),
        }
    )
    extraction = extract("pptx", deck)

    assert extraction.unit_count == 1
    assert extraction.coverage.units_total == 1
    assert extraction.coverage.units_with_text == 1
    assert extraction.coverage.complete
    assert len(extraction.segments) == 2
