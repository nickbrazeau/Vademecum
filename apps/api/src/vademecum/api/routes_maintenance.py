"""Export, backup and restore-verification endpoints.

Responses name a filename and a directory label. The absolute data directory is
printed by ``scripts/dev.sh`` in the terminal, where it is useful, and appears
in no HTTP response (ADR 0002, rule 6).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status

from ..storage import maintenance as store
from . import schemas
from .deps import get_connection, get_data_dir, get_database_path, get_source_dir

router = APIRouter(tags=["maintenance"])


@router.post(
    "/export", response_model=schemas.WrittenFile, status_code=status.HTTP_201_CREATED
)
def create_export(
    connection: sqlite3.Connection = Depends(get_connection),
    data_dir: Path = Depends(get_data_dir),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    return store.write_export(connection, data_dir / "exports", source_dir).as_dict()


@router.get("/export", response_model=list[schemas.StoredFile])
def list_exports(data_dir: Path = Depends(get_data_dir)) -> list[dict]:
    return store.list_written(data_dir / "exports", ".json")


@router.post(
    "/backup", response_model=schemas.WrittenFile, status_code=status.HTTP_201_CREATED
)
def create_backup(
    database_path: Path = Depends(get_database_path),
    data_dir: Path = Depends(get_data_dir),
    source_dir: Path = Depends(get_source_dir),
) -> dict:
    """A restorable bundle: the database plus every uploaded original."""
    return store.write_backup(database_path, data_dir / "backups", source_dir).as_dict()


@router.get("/backup", response_model=list[schemas.StoredFile])
def list_backups(data_dir: Path = Depends(get_data_dir)) -> list[dict]:
    return store.list_written(data_dir / "backups", ".zip")


@router.post("/backup/{filename}/verify")
def verify_backup(filename: str, data_dir: Path = Depends(get_data_dir)) -> dict:
    """Check a bundle could actually be restored. Writes nothing.

    ``filename`` is resolved inside ``backups/`` and refused if it escapes:
    the name comes from a request, so it is treated as one.
    """
    backups = data_dir / "backups"
    candidate = (backups / filename).resolve()
    if candidate.parent != backups.resolve() or not candidate.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such backup.")
    return store.verify_backup(candidate)
