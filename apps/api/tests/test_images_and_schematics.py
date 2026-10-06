"""Pictures in sources, on-device reading of text-less pages, and schematics
(ADR 0013).

Documents are built in memory; the on-device reader is replaced with a fake so
the tests are the same on a machine without Vision. A separate test runs the
real reader when it is there, because the bridge to Quartz is exactly what a
fake cannot check.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image as Picture
from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

from conftest import LOCAL_ORIGIN, refusing_factory
from test_end_to_end import LECTURE, build, provider, turns, upload  # noqa: F401 - fixtures
from test_extractor_review import _pdf, _presentation, _rels, _slide, _zip, REL_SLIDE, R
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.ingest import extract_all, vision
from vademecum.ingest.images import REL_IMAGE, extract_images
from vademecum.storage import schematics as schematic_store

SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    '<rect x="10" y="10" width="80" height="30" fill="none" stroke="black"/>'
    "<text x=\"20\" y=\"30\">Lactate</text>"
    '<path d="M90 25 L150 25" stroke="black"/>'
    "</svg>"
)


# --- documents with pictures in them ----------------------------------------


def png(width: int = 120, height: int = 120, colour=(200, 30, 30)) -> bytes:
    buffer = io.BytesIO()
    Picture.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg(width: int = 160, height: int = 120) -> bytes:
    buffer = io.BytesIO()
    Picture.new("RGB", (width, height), (20, 60, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


def pdf_with_picture(text: str | None, width: int = 120, height: int = 120) -> bytes:
    """One page carrying a raw RGB image object, with or without a text layer."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    image = DecodedStreamObject()
    image.set_data(bytes([200, 30, 30]) * (width * height))
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(width),
            NameObject("/Height"): NumberObject(height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(image)}),
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        }
    )
    drawing = "q 200 0 0 200 100 400 cm /Im1 Do Q"
    if text is not None:
        drawing += f" BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    stream = DecodedStreamObject()
    stream.set_data(drawing.encode("latin-1"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def deck_with_picture(*, slide_text: str = "", picture: bytes | None = None) -> bytes:
    picture = png() if picture is None else picture
    parts = {
        "[Content_Types].xml": '<?xml version="1.0"?><Types/>',
        "ppt/presentation.xml": _presentation("rId1"),
        "ppt/_rels/presentation.xml.rels": _rels(("rId1", REL_SLIDE, "slides/slide1.xml")),
        "ppt/slides/slide1.xml": _slide(*([slide_text] if slide_text else [])),
        "ppt/slides/_rels/slide1.xml.rels": _rels(("rId2", REL_IMAGE, "../media/image1.png")),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
        archive.writestr("ppt/media/image1.png", picture)
    return buffer.getvalue()


class FakeReader(vision.PdfPageReader):
    """Reads every text-less page as the same sentence and remembers a drawing."""

    def __init__(self, payload: bytes, text: str = "") -> None:
        super().__init__(payload)
        self.text = text
        self.calls: list[int] = []

    def __call__(self, page_number: int) -> str:
        self.calls.append(page_number)
        self.rendered[page_number] = png(200, 300, (page_number, 0, 0))
        return self.text


@pytest.fixture()
def fake_vision(monkeypatch: pytest.MonkeyPatch):
    """On-device reading replaced with a scripted reader; the bridge is not run."""
    made: list[FakeReader] = []
    text = {"value": "Serial lactate measurement guides resuscitation in septic shock, read off a scan."}

    def make(payload: bytes) -> FakeReader:
        reader = FakeReader(payload, text["value"])
        made.append(reader)
        return reader

    monkeypatch.setattr(vision, "available", lambda: True)
    monkeypatch.setattr(vision, "PdfPageReader", make)
    monkeypatch.setattr(vision, "images_ocr", lambda: (lambda pictures: text["value"] if pictures else ""))
    yield made, text


# --- extraction ------------------------------------------------------------------


def test_pictures_come_out_of_pdfs_and_decks_with_their_locator() -> None:
    from_pdf = extract_images("pdf", pdf_with_picture("A page with a figure on it, and enough words."))
    assert [(image.locator, image.origin, image.media_type) for image in from_pdf] == [
        ("page 1", "embedded", "image/png")
    ]
    assert (from_pdf[0].width, from_pdf[0].height) == (120, 120)

    from_deck = extract_images("pptx", deck_with_picture(slide_text="A slide with a photograph"))
    assert [(image.locator, image.unit_index) for image in from_deck] == [("slide 1", 1)]


def test_icons_and_duplicates_are_not_kept() -> None:
    tiny = deck_with_picture(picture=png(16, 16))
    assert extract_images("pptx", tiny) == []
    twice = _pdf("Text") if False else pdf_with_picture("Text and more text, plenty of it here.")
    found = extract_images("pdf", twice + b"")
    assert len({image.sha256 for image in found}) == len(found)


def test_text_less_pages_are_read_on_device_and_say_so(fake_vision) -> None:
    readers, _ = fake_vision
    extraction, images = extract_all("pdf", _pdf("A page with its own text layer and enough of it.", None))

    assert extraction.status == "extracted"
    assert readers[0].calls == [2]
    locators = [segment.locator for segment in extraction.segments]
    assert "page 1" in locators and "page 2 (OCR)" in locators
    assert extraction.coverage.units_ocr == 1
    assert extraction.coverage.units_with_text == 2
    assert "on-device" in extraction.detail
    # A page that was read as text is cited by locator, not kept as a picture.
    assert [image.origin for image in images] == []


def test_a_page_recognition_cannot_read_is_kept_as_a_rendered_picture(fake_vision) -> None:
    readers, text = fake_vision
    text["value"] = ""
    extraction, images = extract_all("pdf", _pdf(None, None))

    assert extraction.status == "needs_ocr"
    assert "OCR found nothing" in extraction.detail
    assert [(image.locator, image.origin) for image in images] == [
        ("page 1 (rendered)", "rendered"),
        ("page 2 (rendered)", "rendered"),
    ]


def test_a_picture_slide_is_read_from_its_pictures(fake_vision) -> None:
    extraction, images = extract_all("pptx", deck_with_picture())
    assert extraction.status == "extracted"
    assert [segment.locator for segment in extraction.segments] == ["slide 1 (OCR)"]
    assert [image.locator for image in images] == ["slide 1"]


def test_without_recognition_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vision, "available", lambda: False)
    extraction, images = extract_all("pdf", _pdf(None, None))
    assert extraction.status == "needs_ocr"
    assert "not available" in extraction.detail
    assert images == []


@pytest.mark.skipif(not vision.available(), reason="Quartz and Vision are macOS frameworks")
def test_the_real_reader_renders_and_recognises_a_page() -> None:
    reader = vision.PdfPageReader(_pdf("Serial lactate measurement guides resuscitation in septic shock"))
    text = reader(1)
    assert "lactate" in text.lower()
    assert reader.rendered[1][:8] == b"\x89PNG\r\n\x1a\n"


# --- through the API ---------------------------------------------------------------


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    return tmp_path / "Vademecum"


@pytest.fixture()
def client(tmp_path: Path, folder: Path, turns, provider):  # noqa: F811 - the end-to-end seams
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)
    app = create_app(settings, transport_factory=refusing_factory(), provider_factory=lambda: provider)
    with TestClient(app, base_url=LOCAL_ORIGIN) as test_client:
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        yield test_client


def pile(client: TestClient) -> str:
    return client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()["id"]


def test_uploaded_pictures_are_listed_and_served(client: TestClient, tmp_path: Path) -> None:
    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("figure.pdf", pdf_with_picture("A figure and its caption, in words."), "application/pdf"))],
        data={"confidence": "mid"},
    )
    assert uploaded.status_code == 201, uploaded.text
    source = uploaded.json()["results"][0]["source"]

    detail = client.get(f"/api/sources/{source['id']}").json()
    assert detail["image_count"] == 1

    images = client.get(f"/api/sources/{source['id']}/images").json()
    assert len(images) == 1
    image = images[0]
    assert image["locator"] == "page 1" and image["origin"] == "embedded"
    assert "stored_name" not in image and "sha256" not in image
    assert not any(str(value).startswith("/") or str(tmp_path) in str(value) for value in image.values())

    served = client.get(f"/api/images/{image['id']}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.headers["cache-control"] == "no-store"
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert list((tmp_path / "data" / "attachments" / "images").glob("*.png"))

    # The picture goes when its source goes, and the file with it.
    assert client.delete(f"/api/sources/{source['id']}").status_code == 200
    assert client.get(f"/api/images/{image['id']}").status_code == 404
    assert not list((tmp_path / "data" / "attachments" / "images").glob("*.png"))


def test_a_scan_is_read_on_device_and_cited_as_such(client: TestClient, fake_vision) -> None:
    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("scan.pdf", _pdf(None), "application/pdf"))],
        data={"confidence": "mid"},
    )
    assert uploaded.status_code == 201, uploaded.text
    source = uploaded.json()["results"][0]["source"]
    assert source["status"] == "extracted"
    assert source["extraction_coverage"]["units_ocr"] == 1
    preview = client.get(f"/api/sources/{source['id']}").json()["segments_preview"]
    assert preview[0]["locator"] == "page 1 (OCR)"


def test_the_folder_keeps_pictures_too(tmp_path: Path, folder: Path) -> None:
    from test_folder_intake import touch

    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)
    touch(folder / "piles" / "highconfidence" / "Rounds" / "deck.pptx", deck_with_picture(slide_text="A slide with words on it"))
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        # The background scan takes the deck from the start; an on-request scan then finds it present.
        import time

        deadline = time.time() + 60
        while time.time() < deadline and not any(p["title"] == "Rounds" and p.get("source_count") for p in client.get("/api/piles").json()):
            time.sleep(0.25)
        report = client.post("/api/sources/scan").json()
        assert report["already_present"] == 1 and report["rejected"] == []
        pile_id = next(p["id"] for p in client.get("/api/piles").json() if p["title"] == "Rounds")
        source = client.get(f"/api/piles/{pile_id}/sources").json()[0]
        assert client.get(f"/api/sources/{source['id']}/images").json()[0]["locator"] == "slide 1"


def test_a_picture_file_is_a_source_of_its_own(client: TestClient, fake_vision) -> None:
    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("handout.jpg", jpeg(), "image/jpeg")), ("files", ("diagram.png", png(), "image/png"))],
        data={"confidence": "high"},
    )
    assert uploaded.status_code == 201, uploaded.text
    results = uploaded.json()["results"]
    assert [r["source"]["status"] for r in results] == ["extracted", "extracted"]
    handout = results[0]["source"]
    assert handout["media_type"] == "image/jpeg" and handout["unit_kind"] == "image"
    detail = client.get(f"/api/sources/{handout['id']}").json()
    assert detail["segments_preview"][0]["locator"] == "image (OCR)"
    assert detail["image_count"] == 1
    images = client.get(f"/api/sources/{handout['id']}/images").json()
    assert images[0]["media_type"] == "image/jpeg" and images[0]["locator"] == "image"
    served = client.get(f"/api/images/{images[0]['id']}")
    assert served.headers["content-type"].startswith("image/jpeg")


def test_a_picture_with_no_readable_text_is_kept_but_builds_nothing(client: TestClient, fake_vision) -> None:
    _, text = fake_vision
    text["value"] = ""
    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("diagram.png", png(), "image/png"))],
        data={"confidence": "high"},
    )
    source = uploaded.json()["results"][0]["source"]
    assert source["status"] == "needs_ocr"
    assert "kept and can be viewed" in source["status_detail"]
    assert client.get(f"/api/sources/{source['id']}/images").json()[0]["locator"] == "image"


def test_a_file_named_as_a_picture_must_be_one(client: TestClient) -> None:
    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("notes.jpg", b"not a picture at all", "image/jpeg"))],
        data={"confidence": "high"},
    )
    body = uploaded.json()
    assert body["rejected"] == 1
    assert "not a PNG or JPEG" in body["results"][0]["message"]


# --- schematics ------------------------------------------------------------------


def test_svg_validation_refuses_what_could_run_or_fetch() -> None:
    assert schematic_store.validate_svg(SVG)
    bad = {
        "script": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        "handler": '<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()"/></svg>',
        "remote": '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="http://x/y#z"/></svg>',
        "raster": '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,AAAA"/></svg>',
        "foreign": '<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        "style url": '<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(http://x)"/></svg>',
        "not svg": "<html><body/></html>",
        "broken": "<svg",
    }
    for name, svg in bad.items():
        with pytest.raises(schematic_store.InvalidSchematic):
            schematic_store.validate_svg(svg)
            pytest.fail(name)


def learning_point(client: TestClient) -> dict:
    pile_id = pile(client)
    assert upload(client, pile_id, "lecture.txt", LECTURE.encode()).status_code == 201
    build(client, pile_id)
    status = client.get(f"/api/piles/{pile_id}/build/status").json()
    assert status["run"]["status"] == "succeeded", status["run"]
    return client.get("/api/points").json()[0]


def test_a_schematic_is_kept_for_a_point_and_copied_into_the_folder(client: TestClient, folder: Path) -> None:
    point = learning_point(client)
    saved = client.post(
        f"/api/points/{point['id']}/schematics", json={"title": "Lactate clearance / pathway", "svg": SVG}
    )
    assert saved.status_code == 201, saved.text
    body = saved.json()
    assert body["learning_point_id"] == point["id"]
    assert body["support"] == point["support"] and body["support_label"] == point["support_label"]
    assert body["saved_to_folder"] is True
    assert "stored_name" not in body and "sha256" not in body
    assert not any(str(value).startswith("/") or str(folder) in str(value) for value in body.values())

    copies = list((folder / "schematics").rglob("*.svg"))
    assert len(copies) == 1
    assert copies[0].parent.name == "Sepsis"
    assert copies[0].name.startswith("Lactate clearance pathway")
    assert copies[0].read_bytes() == schematic_store.validate_svg(SVG)

    listed = client.get(f"/api/points/{point['id']}/schematics").json()
    assert [item["id"] for item in listed] == [body["id"]]
    served = client.get(f"/api/schematics/{body['id']}")
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/svg+xml")
    assert b"<script" not in served.content


def test_an_unsafe_schematic_is_refused_without_echoing_it(client: TestClient) -> None:
    point = learning_point(client)
    refused = client.post(
        f"/api/points/{point['id']}/schematics",
        json={"title": "Bad", "svg": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert("secret-token")</script></svg>'},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_schematic"
    assert "secret-token" not in refused.text
    assert client.get(f"/api/points/{point['id']}/schematics").json() == []


def test_a_schematic_for_an_unknown_point_is_not_found(client: TestClient) -> None:
    assert client.post("/api/points/lpt_nope/schematics", json={"title": "x", "svg": SVG}).status_code == 404
    assert client.get("/api/schematics/sch_nope").status_code == 404
    assert client.get("/api/images/img_nope").status_code == 404


# --- sources stored before pictures were kept -------------------------------------


def test_older_sources_get_their_pictures_and_a_second_reading(client: TestClient, tmp_path: Path, fake_vision) -> None:
    from vademecum.db import connect
    from vademecum.ingest.backfill import backfill_pictures, pending

    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[
            ("files", ("figure.pdf", pdf_with_picture("A figure and its caption, in words."), "application/pdf")),
            ("files", ("scan.pdf", _pdf(None), "application/pdf")),
        ],
        data={"confidence": "mid"},
    )
    assert uploaded.status_code == 201, uploaded.text
    figure, scan = (r["source"] for r in uploaded.json()["results"])

    # Make them look like rows from before ADR 0013: no pictures, never visited,
    # and the scan still image-only.
    database = tmp_path / "data" / "vademecum.sqlite3"
    connection = connect(database)
    connection.execute("DELETE FROM source_images")
    connection.execute("UPDATE sources SET images_at = NULL")
    connection.execute(
        "UPDATE sources SET status = 'needs_ocr', extraction_hash = 'stale', coverage_json = '{}' WHERE id = ?",
        (scan["id"],),
    )
    connection.execute("DELETE FROM source_segments WHERE source_id = ?", (scan["id"],))
    connection.commit()
    assert pending(connection) == 2

    report = backfill_pictures(connection, source_dir=tmp_path / "data" / "attachments" / "sources")
    assert report == {"visited": 2, "reread": 1, "missing": 0, "pictures": 1}
    assert pending(connection) == 0
    # A second pass finds nothing to do.
    assert backfill_pictures(connection, source_dir=tmp_path / "data" / "attachments" / "sources")["visited"] == 0
    connection.close()

    assert client.get(f"/api/sources/{figure['id']}").json()["image_count"] == 1
    reread = client.get(f"/api/sources/{scan['id']}").json()
    assert reread["status"] == "extracted"
    assert reread["segments_preview"][0]["locator"] == "page 1 (OCR)"


def test_a_source_that_reached_the_old_cap_gets_its_later_pictures(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """Feedback of 5 October: the cap of 200 kept only a long book's first chapters of
    figures. Such a source is read again for pictures only, once; its text is untouched."""
    import dataclasses

    from vademecum.db import connect
    from vademecum.ingest import images as image_module
    from vademecum.ingest.backfill import regather_capped_images

    pile_id = pile(client)
    uploaded = client.post(
        f"/api/piles/{pile_id}/sources",
        files=[("files", ("figure.pdf", pdf_with_picture("A figure and its caption, in words."), "application/pdf"))],
        data={"confidence": "mid"},
    )
    source = uploaded.json()["results"][0]["source"]
    connection = connect(tmp_path / "data" / "vademecum.sqlite3")
    try:
        segments_before = connection.execute("SELECT id, text FROM source_segments WHERE source_id = ? ORDER BY ordinal", (source["id"],)).fetchall()
        assert connection.execute("SELECT COUNT(*) FROM source_images WHERE source_id = ?", (source["id"],)).fetchone()[0] == 1
        monkeypatch.setattr(image_module, "OLD_MAX_PER_DOCUMENT", 1)
        real = image_module.extract_images

        def more(kind, payload, **kwargs):
            found = real(kind, payload, **kwargs)
            return found + [dataclasses.replace(found[0], sha256="f" * 64, unit_index=found[0].unit_index)]

        monkeypatch.setattr(image_module, "extract_images", more)
        report = regather_capped_images(connection, source_dir=tmp_path / "data" / "attachments" / "sources")
        assert report == {"sources": 1, "pictures": 2}
        assert connection.execute("SELECT COUNT(*) FROM source_images WHERE source_id = ?", (source["id"],)).fetchone()[0] == 2
        segments_after = connection.execute("SELECT id, text FROM source_segments WHERE source_id = ? ORDER BY ordinal", (source["id"],)).fetchall()
        assert [tuple(r) for r in segments_after] == [tuple(r) for r in segments_before], "the text is untouched"
        assert regather_capped_images(connection, source_dir=tmp_path / "data" / "attachments" / "sources") == {"sources": 0, "pictures": 0}, "once"
    finally:
        connection.close()
