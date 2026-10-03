"""Exam reports (ADR 0020): upload, list, read again, remove.

A report is taken in like a source -- detected, its text extracted on this
Mac, the original kept -- and then read by the Mac's own model connection
into content areas the Improvement Map draws. In host mode the report waits
and says so. Nothing here names a path.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request, UploadFile, status

from ..ingest import UnsupportedUpload, detect, extract_all, safe_display_name
from ..ingest.limits import MAX_UPLOAD_BYTES
from ..model import reports as service
from ..storage import reports as store
from ..storage import sources as source_store
from ..storage.sources import ConflictError
from .deps import get_connection, get_source_dir

router = APIRouter(prefix="/improvement-map/reports", tags=["reports"])

# Said before the upload button and returned with the upload: uploading is the
# explicit act that sends (ADR 0002, 0020).
DISCLOSURE = (
    "Uploading a score report sends its text -- the whole report, as read on this Mac -- to the "
    "Mac's own model connection (Codex or Claude, on your sign-in; no API key), once, to read out "
    "the content areas and your standing in each. Remove anything that identifies you first; "
    "the model is told to omit identifiers, and the areas it returns are kept only where they "
    "quote the report."
)


def _reports_dir(source_dir: Path) -> Path:
    return source_dir.parent / "reports"


def _can_parse(request: Request) -> bool:
    return request.app.state.model_mode in ("codex", "claude")


def _start_parse(request: Request, report_id: str) -> None:
    workspace = request.state.workspace
    task = asyncio.create_task(service.parse_report(workspace.database_path, report_id, workspace.turn_factory))
    request.app.state.report_tasks = getattr(request.app.state, "report_tasks", [])
    request.app.state.report_tasks = [t for t in request.app.state.report_tasks if not t.done()] + [task]


@router.get("")
def list_reports(connection: sqlite3.Connection = Depends(get_connection)) -> list[dict]:
    return [report.as_dict() for report in store.list_reports(connection)]


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_report(
    request: Request,
    file: UploadFile,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    display_name = safe_display_name(file.filename)
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise ConflictError("too_large", f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        detected = detect(display_name, payload)
    except UnsupportedUpload as exc:
        raise ConflictError(exc.code, exc.message) from None
    extraction, _pictures = await asyncio.to_thread(extract_all, detected.kind, payload)
    text = "\n\n".join(segment.text for segment in extraction.segments)
    if not text.strip():
        raise ConflictError(
            "no_text",
            "Nothing readable was found in that file. A score report as a PDF with a text layer, "
            "a picture of it, or a text file all work.",
        )
    sha256 = source_store.digest(payload)
    stored_name = source_store.stored_name_for(sha256, detected.kind, detected.media_type)
    source_store.write_original(_reports_dir(source_dir), stored_name, payload, sha256)
    report = store.create_report(
        connection,
        display_name=display_name,
        media_type=detected.media_type,
        sha256=sha256,
        byte_size=len(payload),
        stored_name=stored_name,
        text=text,
    )
    note = ""
    if _can_parse(request):
        _start_parse(request, report.id)
        note = "Reading it now; the map updates when it is done."
    else:
        note = service.WAITING
    return {**report.as_dict(), "note": note, "disclosure": DISCLOSURE}


@router.post("/{report_id}/parse", status_code=status.HTTP_202_ACCEPTED)
def parse_report(
    report_id: str,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    report = store.get_report(connection, report_id)
    if not _can_parse(request):
        raise ConflictError("needs_model", service.WAITING)
    _start_parse(request, report.id)
    return {"started": True, **report.as_dict()}


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> None:
    report = store.get_report(connection, report_id)
    stored_name = store.stored_name_of(connection, report_id)
    store.delete_report(connection, report_id)
    if stored_name:
        still = connection.execute("SELECT 1 FROM exam_reports WHERE sha256 = ? LIMIT 1", (report.sha256,)).fetchone()
        if still is None:
            source_store.remove_original(_reports_dir(source_dir), stored_name, report.sha256)
