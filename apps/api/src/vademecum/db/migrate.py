"""Forward-only migration runner (ADR 0005).

Migrations are ``NNNN_slug.sql`` files applied in numeric order, one
transaction each. ``schema_migrations`` records a SHA-256 of every file that
ran; the runner refuses to start if an applied migration's bytes have changed,
which is the failure this table exists to catch.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MIGRATION_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


class MigrationError(RuntimeError):
    """A migration is missing, malformed, or has changed after being applied."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def migrations_dir() -> Path:
    return Path(__file__).parent / "migrations"


def discover(directory: Path | None = None) -> list[Migration]:
    """Every migration on disk, in numeric order."""
    directory = directory or migrations_dir()
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_NAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"{path.name} is not a valid migration filename; "
                "expected NNNN_slug.sql"
            )
        found.append(Migration(int(match.group(1)), match.group(2), path))
    versions = [migration.version for migration in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"duplicate migration version in {directory}")
    return found


def applied_versions(connection: sqlite3.Connection) -> dict[int, str]:
    """Version -> checksum for everything already applied."""
    connection.execute(_SCHEMA_MIGRATIONS)
    rows = connection.execute(
        "SELECT version, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {row["version"]: row["checksum"] for row in rows}


def apply_migrations(
    connection: sqlite3.Connection, directory: Path | None = None
) -> list[str]:
    """Apply anything outstanding. Returns the names applied, in order."""
    already = applied_versions(connection)
    available = discover(directory)

    for migration in available:
        recorded = already.get(migration.version)
        if recorded is not None and recorded != migration.checksum:
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name}.sql has "
                "changed since it was applied. Migrations are forward-only; "
                "restore a backup rather than editing history."
            )

    known = {migration.version for migration in available}
    for version in sorted(set(already) - known):
        raise MigrationError(
            f"the database has migration {version:04d} applied but no such file "
            "exists. This checkout is older than the database."
        )

    applied: list[str] = []
    for migration in available:
        if migration.version in already:
            continue
        try:
            # BEGIN lives inside the script so executescript() cannot commit it
            # out from under us; the transaction stays open for the bookkeeping
            # insert. A statement failing part-way through the script leaves
            # that transaction open, so the rollback has to cover the script
            # too -- otherwise a half-applied schema survives the failure.
            connection.executescript(f"BEGIN;\n{migration.sql}\n")
            connection.execute(
                "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.checksum,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ),
            )
        except BaseException:
            # The script may have failed before BEGIN ran; rolling back what
            # never started would replace the real error with a useless one.
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")
        applied.append(f"{migration.version:04d}_{migration.name}")
    return applied
