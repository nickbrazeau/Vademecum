"""On-device page rendering and text recognition (ADR 0013).

macOS ships both: Quartz draws a PDF page into a bitmap, and the Vision
framework reads text out of a bitmap. Nothing leaves the machine, no model is
called, no key exists; this is the operating system doing what Preview does.
The bindings are ``pyobjc-framework-Quartz`` and ``pyobjc-framework-Vision``,
imported lazily and guarded, so a machine without them (or a non-Mac) simply
has no OCR and says so in the coverage record.

What recognised text is, and is not: it is what the OS read off a picture,
good enough to build from and to cite by page, and it is labelled as OCR in
every locator so a citation never claims a text layer that was not there.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache

logger = logging.getLogger("vademecum.ingest")

RENDER_SCALE = 2.0
# A rendered page much larger than this is a poster; capped so one page cannot
# take hundreds of megabytes of bitmap.
MAX_RENDER_PIXELS = 12_000_000


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        import Quartz  # noqa: F401
        import Vision  # noqa: F401
    except Exception:  # noqa: BLE001 - absent on other platforms, or not installed
        return False
    return True


def render_pdf_page(payload: bytes, page_number: int, *, scale: float = RENDER_SCALE) -> bytes | None:
    """One PDF page as PNG bytes, or None if it cannot be drawn."""
    if not available():
        return None
    import Quartz
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(payload, len(payload))
    provider = Quartz.CGDataProviderCreateWithCFData(data)
    document = Quartz.CGPDFDocumentCreateWithProvider(provider)
    if document is None:
        return None
    page = Quartz.CGPDFDocumentGetPage(document, page_number)
    if page is None:
        return None
    box = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
    width, height = int(box.size.width * scale), int(box.size.height * scale)
    if width <= 0 or height <= 0:
        return None
    if width * height > MAX_RENDER_PIXELS:
        factor = (MAX_RENDER_PIXELS / (width * height)) ** 0.5
        width, height, scale = int(width * factor), int(height * factor), scale * factor
    image = _draw(page, width, height, scale)
    return None if image is None else _png(image)


def recognise(png: bytes) -> str:
    """Text in a PNG, top to bottom, one line per recognised line."""
    if not available():
        return ""
    import Quartz
    import Vision
    from Foundation import NSData

    data = NSData.dataWithBytes_length_(png, len(png))
    source = Quartz.CGImageSourceCreateWithData(data, None)
    if source is None:
        return ""
    image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if image is None:
        return ""
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(image, None)
    ok, _error = handler.performRequests_error_([request], None)
    if not ok:
        return ""
    lines: list[str] = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            lines.append(str(candidates[0].string()))
    return "\n".join(lines)


class PdfPageReader:
    """Reads text-less PDF pages by drawing them, and keeps the drawings.

    Called once per page the text layer left empty. The bitmaps are kept so
    the same pages can be stored as rendered images without drawing twice.
    """

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.rendered: dict[int, bytes] = {}

    def __call__(self, page_number: int) -> str:
        try:
            png = render_pdf_page(self._payload, page_number)
        except Exception:  # noqa: BLE001 - one bad page is not a bad document
            logger.info("ocr_page_failed")
            return ""
        if not png:
            return ""
        self.rendered[page_number] = png
        try:
            return recognise(png)
        except Exception:  # noqa: BLE001
            logger.info("ocr_page_failed")
            return ""

    def rendered_text_less(self, extraction) -> dict[int, bytes]:
        """The drawings of pages that ended up with no text, OCR or otherwise.

        A page that recognition read becomes text and is cited by locator; a
        page that stayed image-only is kept as a picture, so it can at least
        be looked at.
        """
        with_text = {segment.unit_index for segment in extraction.segments}
        return {page: png for page, png in self.rendered.items() if page not in with_text}


def images_ocr() -> Callable[[list[bytes]], str] | None:
    """A reader for the pictures on one slide, or None when OCR is unavailable."""
    if not available():
        return None

    def read(pictures: list[bytes]) -> str:
        texts: list[str] = []
        for picture in pictures:
            try:
                text = recognise(picture)
            except Exception:  # noqa: BLE001
                logger.info("ocr_image_failed")
                continue
            if text:
                texts.append(text)
        return "\n\n".join(texts)

    return read


# --- Quartz details -----------------------------------------------------------


def _draw(page, width: int, height: int, scale: float):
    import Quartz

    colour_space = Quartz.CGColorSpaceCreateDeviceRGB()
    context = Quartz.CGBitmapContextCreate(
        None, width, height, 8, 0, colour_space, Quartz.kCGImageAlphaPremultipliedLast
    )
    if context is None:
        return None
    Quartz.CGContextSetRGBFillColor(context, 1, 1, 1, 1)
    Quartz.CGContextFillRect(context, Quartz.CGRectMake(0, 0, width, height))
    Quartz.CGContextScaleCTM(context, scale, scale)
    Quartz.CGContextDrawPDFPage(context, page)
    return Quartz.CGBitmapContextCreateImage(context)


def _png(image) -> bytes | None:
    import Quartz

    buffer = Quartz.CFDataCreateMutable(None, 0)
    destination = Quartz.CGImageDestinationCreateWithData(buffer, "public.png", 1, None)
    if destination is None:
        return None
    Quartz.CGImageDestinationAddImage(destination, image, None)
    if not Quartz.CGImageDestinationFinalize(destination):
        return None
    return bytes(buffer)
