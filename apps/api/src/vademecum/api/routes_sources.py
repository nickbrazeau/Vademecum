"""Source intake and the Build learning material flow."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status

from ..ingest import UnsupportedUpload, detect, extract_all, safe_display_name
from ..ingest.limits import MAX_UPLOAD_BYTES
from ..model import BuildService
from ..storage import images as image_store
from ..storage import jobs, learning
from ..storage import piles as pile_store
from ..storage import sources as store
from . import schemas
from .deps import get_build_service, get_connection, get_host_turns, get_model_mode, get_source_dir

router = APIRouter(tags=["sources"])

# One request may carry several files, but not a library.
MAX_FILES_PER_REQUEST = 20


@router.get("/piles/{pile_id}/sources")
def list_sources(
    pile_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> list[dict]:
    pile_store.get_pile(connection, pile_id)
    return [source.as_dict() for source in store.list_sources(connection, pile_id=pile_id)]


@router.post("/piles/{pile_id}/sources", status_code=status.HTTP_201_CREATED)
async def upload_sources(
    pile_id: str,
    files: list[UploadFile] = File(...),
    confidence: schemas.Confidence = Form(...),
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    """Store uploads. Each file succeeds or fails on its own.

    One bad file in a drag-and-drop of twelve must not lose the other eleven, so
    the response is per-file rather than all-or-nothing. The original is written
    before extraction is attempted: a parser that cannot read a file is not a
    reason for the owner to lose it.
    """
    pile_store.get_pile(connection, pile_id)
    if len(files) > MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"Upload at most {MAX_FILES_PER_REQUEST} files at a time.",
        )

    results: list[dict] = []
    accepted = 0
    rejected = 0

    for upload in files:
        display_name = safe_display_name(upload.filename)
        payload = await upload.read(MAX_UPLOAD_BYTES + 1)
        await upload.close()

        if len(payload) > MAX_UPLOAD_BYTES:
            rejected += 1
            results.append(
                _result(
                    display_name,
                    "rejected",
                    f"That file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB, "
                    "which is more than Vademecum stores. Split it and try again.",
                )
            )
            continue

        try:
            detected = detect(display_name, payload)
        except UnsupportedUpload as exc:
            rejected += 1
            results.append(_result(display_name, "rejected", exc.message))
            continue

        sha256 = store.digest(payload)
        extraction, images = await asyncio.to_thread(extract_all, detected.kind, payload)
        # Written first, and content-addressed, so the bytes survive whatever
        # the parser made of them.
        store.write_original(
            source_dir, store.stored_name_for(sha256, detected.kind, detected.media_type), payload, sha256
        )
        stored = store.store_upload(
            connection,
            pile_id=pile_id,
            display_name=display_name,
            media_type=detected.media_type,
            sha256=sha256,
            byte_size=len(payload),
            confidence=confidence,
            extraction=extraction,
        )
        if stored.outcome != "duplicate":
            image_store.replace_images(
                connection,
                source_id=stored.source.id,
                images=images,
                directory=source_dir.parent / "images",
            )
        accepted += 1
        outcome = "duplicate" if stored.outcome == "duplicate" else "stored"
        results.append(
            {
                "filename": display_name,
                "outcome": outcome,
                "source": stored.source.as_dict(),
                "message": _message_for(stored),
                "warnings": list(stored.warnings),
            }
        )

    return {"accepted": accepted, "rejected": rejected, "results": results}


def _result(filename: str, outcome: str, message: str) -> dict:
    return {
        "filename": filename,
        "outcome": outcome,
        "source": None,
        "message": message,
        "warnings": [],
    }


def _message_for(stored: store.StoredUpload) -> str:
    source = stored.source
    if stored.outcome == "duplicate":
        return "Already in this pile — nothing changed."
    if stored.outcome == "re_extracted":
        counts = stored.invalidated
        note = (
            f" {counts.get('points', 0)} learning point(s) and "
            f"{counts.get('questions', 0)} question(s) were put on hold for re-checking."
            if counts.get("points") or counts.get("questions")
            else ""
        )
        return f"Re-read with a different result.{note}"
    if source.status != "extracted":
        return source.status_detail
    return source.status_detail or (
        f"Read {source.unit_count} {source.unit_kind}(s)."
    )


@router.post("/sources/scan")
async def scan_folder_now(
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    """Bring the learner's folder in now (ADR 0012). The report names files, never paths."""
    from ..app import current_sources_dir, scan_sources_folder

    settings = request.app.state.settings
    folder = current_sources_dir(settings) if settings.tenancy == "single" else None
    if folder is None:
        raise store.ConflictError(
            "not_in_this_mode",
            "This Vademecum has no folder to scan; files come in through the web app.",
        )
    request.app.state.sources_folder = folder
    database_path = request.state.workspace.database_path
    del connection  # the scan opens its own connection, on a worker thread
    return await asyncio.to_thread(scan_sources_folder, database_path, source_dir, folder)


@router.get("/sources/{source_id}")
def get_source(
    source_id: str,
    limit: int = 8,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    source = store.get_source(connection, source_id)
    data = source.as_dict()
    data["segments_preview"] = [
        segment.as_dict()
        for segment in store.list_segments(connection, source_id, limit=max(1, min(limit, 50)))
    ]
    data["image_count"] = image_store.count_images(connection, source_id)
    return data


@router.patch("/sources/{source_id}")
def update_source(
    source_id: str,
    payload: schemas.SourceUpdate,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    source = store.get_source(connection, source_id)
    if payload.confidence is not None:
        source = store.set_confidence(connection, source_id, confidence=payload.confidence)
    if payload.excluded is not None:
        source = store.set_excluded(connection, source_id, excluded=payload.excluded)
    return source.as_dict()


@router.delete("/sources/{source_id}", status_code=status.HTTP_200_OK)
def delete_source(
    source_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    source = store.get_source(connection, source_id)
    stored_name = store.stored_name_of(connection, source_id)
    pictures = image_store.stored_names_of(connection, source_id)
    store.delete_source(connection, source_id)
    # Files are content-addressed and shared between piles, so the bytes go only
    # when the last row referencing that digest has gone.
    if stored_name and not store.digest_still_referenced(connection, source.sha256):
        store.remove_original(source_dir, stored_name, source.sha256)
    image_store.remove_unreferenced(connection, source_dir.parent / "images", pictures)
    return {"deleted": source_id}


@router.get("/sources/{source_id}/segments")
def list_segments(
    source_id: str,
    offset: int = 0,
    limit: int = 25,
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    store.get_source(connection, source_id)
    return [
        segment.as_dict()
        for segment in store.list_segments(
            connection, source_id, limit=max(1, min(limit, 100)), offset=max(0, offset)
        )
    ]


# --- build -------------------------------------------------------------------


@router.get("/piles/{pile_id}/build/preview")
def build_preview(
    pile_id: str,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
) -> dict:
    """What the next batch would send, and the consent record for it.

    Creating the batch row here is what makes consent specific: the owner reads
    these excerpts, and the send quotes back the id and the hash of exactly
    them. Nothing is transmitted by this route.
    """
    pile_store.get_pile(connection, pile_id)
    batch = store.next_batch(connection, pile_id)
    summary = store.pile_source_summary(connection, pile_id)
    service: BuildService = request.state.workspace.build_service

    blocked = ""
    if service.is_running(pile_id):
        blocked = "A build is already running for this pile."
    elif not batch.excerpts:
        blocked = (
            "Every passage in this pile has already been through a build."
            if summary["usable"]
            else "This pile has no readable, included sources to build from."
        )

    batch_id = store.record_batch(connection, pile_id, batch) if batch.excerpts else ""
    return {
        "batch_id": batch_id,
        "selection_hash": batch.selection_hash,
        "excerpts": [excerpt.as_dict() for excerpt in batch.excerpts],
        "excerpt_count": len(batch.excerpts),
        "excerpt_chars": batch.excerpt_chars,
        "coverage": batch.coverage_dict(),
        "sources": summary,
        "disclosure": _disclosure(batch, request.app.state.model_mode),
        "blocked_reason": blocked,
        "attention": store.sources_needing_attention(connection, pile_id),
        "coverage_meaning": store.COVERAGE_MEANING,
    }


HOST_DESTINATION = (
    "Handed to your own ChatGPT, inside your conversation, on your ChatGPT "
    "license. Vademecum sends nothing to any model provider and holds no API key. "
    "Short public topic words still go from Vademecum to PubMed."
)
CODEX_DESTINATION = (
    "Sent to OpenAI through the Codex process on this Mac, using your "
    "ChatGPT sign-in. No API key is used."
)
CLAUDE_DESTINATION = (
    "Sent to Claude through the Claude Code CLI on this Mac, using your "
    "Claude sign-in. No API key is used."
)


def _disclosure(batch: store.Batch, mode: str = "codex") -> dict:
    names = sorted({excerpt.display_name for excerpt in batch.excerpts})
    return {
        "headline": (
            f"This sends {len(batch.excerpts)} excerpt(s) — "
            f"{batch.excerpt_chars:,} characters — from {len(names)} file(s)."
        ),
        "bullets": [
            "The source excerpts listed below, with each filename, source-confidence "
            "label and page, slide or section location shown in the preview.",
            "Nothing else from these files, and nothing from any other pile.",
            "Follow-up model checks within this build send each generated claim "
            "and its context alongside a retrieved abstract; question checks send "
            "the generated question, reference answer and rubric alongside the "
            "selected source passages and retrieved abstracts. No learner answers "
            "or unrelated notes are included.",
            "Short public topic words derived during this build may be sent to "
            "PubMed to look for supporting literature; public PubMed record "
            "identifiers are sent to retrieve and refresh papers. Source excerpts, filenames "
            "and learner answers are not sent to PubMed.",
        ],
        "destination": {"host": HOST_DESTINATION, "claude": CLAUDE_DESTINATION}.get(mode, CODEX_DESTINATION),
    }


@router.post("/piles/{pile_id}/build", status_code=status.HTTP_202_ACCEPTED)
async def start_build(
    pile_id: str,
    payload: schemas.BuildStart,
    request: Request,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
) -> dict:
    pile_store.get_pile(connection, pile_id)
    if service.is_running(pile_id):
        raise store.ConflictError(
            "build_running", "A build is already running for this pile."
        )
    # Re-derive the consented ranges from the live database before a run row
    # exists, so a stale selection is a refusal rather than a failed run in the
    # history. Comparing the client's hash to the STORED hash would only prove
    # the client echoed the preview back -- it says nothing about whether the
    # material still matches what the owner read.
    store.resolve_batch(
        connection,
        pile_id=pile_id,
        batch_id=payload.batch_id,
        expected_hash=payload.selection_hash,
    )
    batch = connection.execute(
        "SELECT excerpt_count, excerpt_chars FROM build_batches WHERE id = ?",
        (payload.batch_id,),
    ).fetchone()

    run = jobs.start_run(
        connection,
        pile_id=pile_id,
        source_count=store.pile_source_summary(connection, pile_id)["usable"],
        excerpt_count=batch["excerpt_count"],
        excerpt_chars=batch["excerpt_chars"],
    )
    await service.start(pile_id=pile_id, run_id=run.id, batch_id=payload.batch_id)

    # Host mode (ADR 0009): the pipeline files its first pending turn almost
    # at once. Waiting briefly for it means the caller -- the learner's
    # ChatGPT -- gets the work to do in the same reply as the run.
    pending = []
    host_turns = get_host_turns(request)
    if host_turns is not None:
        pending = await host_turns.wait_for_change(
            "run", run.id, after="", timeout=5.0, still_running=lambda: service.is_running(pile_id)
        )
    return {
        "run": jobs.get_run(connection, run.id).as_dict(),
        "pending": [turn.as_dict() for turn in pending],
    }


@router.get("/piles/{pile_id}/build/status")
def build_status(
    pile_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
    mode: str = Depends(get_model_mode),
    host_turns=Depends(get_host_turns),
) -> dict:
    pile_store.get_pile(connection, pile_id)
    run = jobs.latest_run(connection, pile_id)
    pending = []
    if host_turns is not None and run is not None and run.status == jobs.RUNNING:
        pending = host_turns.pending("run", run.id)
    return {
        "run": None if run is None else run.as_dict(),
        "running": service.is_running(pile_id),
        "coverage": store.pile_coverage(connection, pile_id).as_dict(),
        "bank": learning.bank_summary(connection),
        "mode": mode,
        # True while the run is parked on work only the learner's ChatGPT can
        # do. The desk shows this rather than a spinner that never stops.
        "awaiting_host": bool(pending),
        "pending_kinds": [turn.kind for turn in pending],
    }


@router.get("/piles/{pile_id}/build/pending")
def build_pending(
    pile_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
    mode: str = Depends(get_model_mode),
    host_turns=Depends(get_host_turns),
) -> dict:
    """The model work waiting on the learner's ChatGPT for this pile (ADR 0009).

    Each turn carries exactly what the model may look at and the schema its
    answer must match. In Codex mode there is never anything here.
    """
    pile_store.get_pile(connection, pile_id)
    run = jobs.running_run(connection, pile_id)
    turns = []
    if host_turns is not None and run is not None:
        turns = host_turns.pending("run", run.id)
    return {
        "mode": mode,
        "turns": [turn.as_dict() for turn in turns],
        "run": None if run is None else run.as_dict(),
        "running": service.is_running(pile_id),
    }


@router.post("/piles/{pile_id}/build/submit")
async def build_submit(
    pile_id: str,
    payload: schemas.TurnSubmission,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
    host_turns=Depends(get_host_turns),
) -> dict:
    """Accept ChatGPT's result for one pending turn and hand back the next.

    The result is validated against the turn's own schema before anything
    moves; a refusal leaves the turn pending so it can be resubmitted. After a
    good submission the pipeline continues on its own, and this waits briefly
    for either the next turn or the run's end, so one call answers "what now".
    """
    pile_store.get_pile(connection, pile_id)
    if host_turns is None:
        raise store.ConflictError(
            "host_mode_only",
            "This Vademecum runs its own model turns; there is nothing to submit.",
        )
    run = jobs.running_run(connection, pile_id)
    turn = host_turns.get(payload.turn_id)
    if run is None or turn is None or turn.scope_kind != "run" or turn.scope_id != run.id:
        from ..model.host import REFUSALS, SubmissionRefused

        raise SubmissionRefused("unknown_turn", REFUSALS["unknown_turn"])
    host_turns.submit(payload.turn_id, payload.result)
    following = await host_turns.wait_for_change(
        "run", run.id, after=payload.turn_id, timeout=15.0, still_running=lambda: service.is_running(pile_id)
    )
    latest = jobs.get_run(connection, run.id)
    return {
        "accepted": True,
        "turn_id": payload.turn_id,
        "next": following[0].as_dict() if following else None,
        "remaining": len(following),
        "run": latest.as_dict(),
        "running": service.is_running(pile_id),
    }


@router.post("/piles/{pile_id}/build/cancel")
async def cancel_build(
    pile_id: str,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
) -> dict:
    pile_store.get_pile(connection, pile_id)
    stopped = await service.cancel(pile_id)
    run = jobs.latest_run(connection, pile_id)
    return {"stopped": stopped, "run": None if run is None else run.as_dict()}


@router.post("/piles/{pile_id}/recheck")
def reopen_for_recheck(
    pile_id: str,
    source_id: str | None = None,
    connection: sqlite3.Connection = Depends(get_connection),
    service: BuildService = Depends(get_build_service),
) -> dict:
    """Offer already-processed material to a build again.

    The recovery path for a pile whose passages are all covered: a source
    excluded and re-included, a question held after a correction, a file
    re-uploaded unchanged. Without it those are dead ends.

    It rewinds coverage and nothing else. Held points and questions stay held,
    a re-included source does not regain eligibility, and the owner still has to
    read and consent to a fresh preview before anything is sent.
    """
    pile_store.get_pile(connection, pile_id)
    if service.is_running(pile_id):
        raise store.ConflictError(
            "build_running", "A build is running for this pile. Wait for it to finish."
        )
    if source_id is not None:
        store.get_source(connection, source_id)
    reopened = store.reopen_coverage(connection, pile_id=pile_id, source_id=source_id)
    return {
        **reopened,
        "coverage": store.pile_coverage(connection, pile_id).as_dict(),
        "note": (
            "This material can be sent through a build again. Nothing was "
            "released: anything on hold stays on hold until a build re-checks it."
        ),
    }


@router.get("/runs")
def list_runs(connection: sqlite3.Connection = Depends(get_connection)) -> list[dict]:
    return [run.as_dict() for run in jobs.list_runs(connection)]


# --- generated material ------------------------------------------------------


@router.get("/points")
def list_points(
    pile_id: str | None = None,
    held: bool | None = None,
    limit: int = 100,
    connection: sqlite3.Connection = Depends(get_connection),
) -> list[dict]:
    return [
        point.as_dict()
        for point in learning.list_points(
            connection, pile_id=pile_id, held=held, limit=max(1, min(limit, 500))
        )
    ]


@router.get("/points/{point_id}")
def get_point(
    point_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    return learning.get_point(connection, point_id).as_dict()


@router.delete("/piles/{pile_id}/material")
def retire_material(
    pile_id: str, connection: sqlite3.Connection = Depends(get_connection)
) -> dict:
    """Retire a pile's generated material. Answer history is kept."""
    pile_store.get_pile(connection, pile_id)
    return {"retired": learning.retire_pile_material(connection, pile_id)}
