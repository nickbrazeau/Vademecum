"""Health. Touches the database on purpose.

A health check that answers 200 while the database is unreachable is worse than
no health check: it is what lets a broken deployment look fine.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request

from .. import __version__
from ..config import Settings, is_loopback
from ..db import connect, migrations_dir
from ..db.migrate import MIGRATION_NAME
from ..tenancy import LEARNERS_DIRNAME
from . import schemas
from .deps import get_model_mode, get_settings_dep, get_workspace

router = APIRouter(tags=["health"])


def _latest_migration_version() -> int:
    versions = [
        int(match.group(1))
        for path in migrations_dir().iterdir()
        if (match := MIGRATION_NAME.match(path.name))
    ]
    return max(versions, default=0)


@router.get("/health", response_model=schemas.Health)
async def health(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    model_mode: str = Depends(get_model_mode),
) -> schemas.Health:
    if settings.tenancy == "single":
        # The owner's database, on purpose: a health check that answers 200
        # while it is unreachable is what lets a broken deployment look fine.
        workspace = await get_workspace(request)
        connection = connect(workspace.database_path)
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
        finally:
            connection.close()
        schema_version = int(row["version"])
    else:
        # No learner is implied by a health check, so no database is opened.
        # What can be checked is that workspaces can be created at all.
        learners = settings.resolve_data_dir() / LEARNERS_DIRNAME
        learners.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not os.access(learners, os.W_OK):
            raise RuntimeError("the learners directory is not writable")
        schema_version = _latest_migration_version()
    return schemas.Health(
        status="ok",
        version=__version__,
        schema_version=schema_version,
        database="ok",
        loopback_only=is_loopback(settings.host),
        # Both facts are reported separately: a bridge existing and a bridge
        # being used for content are different claims.
        model_bridge_configured=model_mode == "codex",
        model_calls_configured=model_mode == "codex",
        literature_configured=settings.literature_enabled,
        model_mode=model_mode,  # type: ignore[arg-type]
        tenancy=settings.tenancy,  # type: ignore[arg-type]
    )
