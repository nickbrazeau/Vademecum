"""Export, backup and restore.

Three different promises:

* **Export** is human-readable JSON: every table, plus a manifest of the
  uploaded originals with their digests. It is readable without Vademecum.
* **Backup** is a restorable bundle: a consistent copy of the database *and*
  every uploaded original, in one zip, with a manifest. A backup that contained
  only the database would restore a library whose every source file was missing.
* **Restore verification** re-reads a bundle and reports whether it is complete
  and internally consistent. It uses a temporary extracted database, never
  changes the canonical database, and does not itself restore the bundle.

Both write inside the configured data directory (ADR 0003). Neither returns a
filesystem path to the HTTP layer (ADR 0002).

Verification's authority is the database *inside* the bundle, never the bundle's
own manifest. A manifest is a claim; `sources` is the record of what the owner
uploaded. Checking the manifest against itself is why a bundle listing no
attachments at all could report itself restorable while every original was
absent.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import connect, connect_reader
from ..db.migrate import discover
from .common import utc_now

EXPORT_FORMAT_VERSION = 2
BACKUP_FORMAT_VERSION = 1

# Every table the owner would miss. Derived from the live schema at runtime as
# well (see `_all_tables`), so a future migration cannot silently leave a new
# table out of the export -- the mismatch is reported instead.
# This node's own bookkeeping: never exported, never synced (ADR 0015).
INTERNAL_TABLES: tuple[str, ...] = ("schema_migrations", "sync_state", "sync_log")

EXPORTED_TABLES: tuple[str, ...] = (
    "piles",
    "learning_items",
    "knowledge_gap_flags",
    "curated_articles",
    "sources",
    "source_segments",
    "generation_runs",
    "build_batches",
    "learning_points",
    "learning_point_sources",
    "learning_point_topics",
    "tutor_questions",
    "tutor_question_anchors",
    "tutor_cycle_entries",
    "tutor_attempts",
    "literature_topics",
    "literature_checks",
    "literature_records",
    "literature_topic_records",
    "evidence_links",
    # The Improvement Map's own state (migration 0004): the owner's specialty
    # assignments and the remembered layout. The seeded specialty list rides
    # along so an export reads without the schema beside it.
    "specialties",
    "topic_specialties",
    "map_positions",
    "app_state",
    # Host-mode turns (migration 0005): transient, but they are the learner's
    # own material and an in-flight answer, so an export carries them too.
    "pending_turns",
    # Pictures kept beside a source, and schematics drawn for a point
    # (migration 0006). Rows only; the files are in the backup bundle.
    "source_images",
    "schematics",
    # Exam reports and their content areas (ADR 0020).
    "exam_reports",
    "exam_areas",
)

DATABASE_MEMBER = "vademecum.sqlite3"
MANIFEST_MEMBER = "manifest.json"
ATTACHMENT_PREFIX = "attachments/sources/"


@dataclass(frozen=True)
class WrittenFile:
    """A file written into the data directory. ``as_dict`` omits the path."""

    path: Path
    directory: str
    created_at: str
    byte_size: int
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.path.name,
            "directory": self.directory,
            "created_at": self.created_at,
            "byte_size": self.byte_size,
            **self.detail,
        }


def _timestamp_slug(now: str) -> str:
    return now.replace("-", "").replace(":", "").replace(".", "")


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def _all_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row["name"] for row in rows]


def missing_from_export(connection: sqlite3.Connection) -> list[str]:
    """Tables in the schema that the export does not cover.

    Asserted by a test. A new migration that adds a table and forgets to add it
    here fails the suite rather than producing a quietly partial export.
    """
    known = set(EXPORTED_TABLES) | set(INTERNAL_TABLES)
    return [name for name in _all_tables(connection) if name not in known]


def file_digest(path: Path, *, chunk: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            hasher.update(block)
    return hasher.hexdigest()


def attachment_manifest(
    connection: sqlite3.Connection, source_dir: Path
) -> list[dict[str, Any]]:
    """Every uploaded original a row refers to, with its digest and presence.

    ``present`` and ``digest_matches`` are recorded rather than assumed: an
    export that lists a file which is not there, or whose bytes have changed,
    should say so, and a restore check reads exactly these fields.
    """
    rows = connection.execute(
        "SELECT DISTINCT stored_name, sha256, byte_size, media_type FROM sources"
        " ORDER BY stored_name"
    ).fetchall()
    manifest: list[dict[str, Any]] = []
    for row in rows:
        path = source_dir / row["stored_name"]
        present = path.is_file()
        actual = file_digest(path) if present else None
        manifest.append(
            {
                "stored_name": row["stored_name"],
                "sha256": row["sha256"],
                "byte_size": row["byte_size"],
                "media_type": row["media_type"],
                "present": present,
                "digest_matches": bool(actual and actual == row["sha256"]),
            }
        )
    return manifest


def export_payload(
    connection: sqlite3.Connection, source_dir: Path | None = None
) -> dict[str, Any]:
    """The whole database as plain JSON-compatible data, plus attachments."""
    schema_version = connection.execute(
        "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
    ).fetchone()["version"]
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in EXPORTED_TABLES:
        # Table names come from the module-level tuple above, never from input.
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        tables[table] = [dict(row) for row in rows]
    manifest = attachment_manifest(connection, source_dir) if source_dir else []
    return {
        "vademecum_export": {
            "format_version": EXPORT_FORMAT_VERSION,
            "schema_version": schema_version,
            "exported_at": utc_now(),
            "tables_omitted": missing_from_export(connection),
        },
        "tables": tables,
        # The bytes are not in the JSON: an export is meant to be readable, and
        # base64 PDFs would defeat that. The manifest is what makes the pairing
        # checkable, and the backup bundle is what carries the bytes.
        "attachments": manifest,
    }


def write_export(
    connection: sqlite3.Connection, exports_dir: Path, source_dir: Path | None = None
) -> WrittenFile:
    exports_dir.mkdir(parents=True, exist_ok=True)
    payload = export_payload(connection, source_dir)
    now = payload["vademecum_export"]["exported_at"]
    path = _unique_path(exports_dir, f"vademecum-export-{_timestamp_slug(now)}", ".json")
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)
    return WrittenFile(
        path=path,
        directory=exports_dir.name,
        created_at=now,
        byte_size=path.stat().st_size,
        detail={
            "format_version": EXPORT_FORMAT_VERSION,
            "counts": {table: len(rows) for table, rows in payload["tables"].items()},
            "attachments": len(payload["attachments"]),
        },
    )


def write_backup(
    database_path: Path, backups_dir: Path, source_dir: Path | None = None
) -> WrittenFile:
    """A restorable bundle: the database and every uploaded original, zipped.

    The database is copied through SQLite's own backup API from a separate
    reader, so it is consistent even while the app is writing -- copying the
    file would not be. The originals go in beside it, and a manifest records
    what should be there, so a restore can be checked rather than hoped for.

    ``restorable`` is the result of actually verifying the finished bundle, not
    an inference from what the writer thinks it did. It used to be
    ``not missing``, which called a bundle restorable while carrying originals
    whose bytes no longer matched the digest the database recorded -- the one
    case where the owner most needs to be told. Re-reading the bundle costs a
    second pass over it; a claim of restorability that was never tested is worth
    less than the pass costs.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    bundle = _unique_path(
        backups_dir, f"vademecum-backup-{_timestamp_slug(now)}", ".zip"
    )
    staging = bundle.with_suffix(".sqlite3.partial")

    source = connect_reader(database_path)
    destination = connect(staging)
    try:
        source.backup(destination)
        integrity = destination.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"backup failed its integrity check: {integrity}")
        destination.row_factory = sqlite3.Row
        counts = {
            table: destination.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in EXPORTED_TABLES
        }
        manifest = (
            attachment_manifest(destination, source_dir) if source_dir else []
        )
        schema_version = destination.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]
    finally:
        destination.close()
        source.close()

    # A WAL sidecar next to a copied database is a restore waiting to go wrong.
    for sidecar in ("-wal", "-shm"):
        staging.with_name(staging.name + sidecar).unlink(missing_ok=True)

    included = 0
    missing: list[str] = []
    corrupt: list[str] = []
    try:
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(staging, DATABASE_MEMBER)
            for entry in manifest:
                origin = (source_dir / entry["stored_name"]) if source_dir else None
                if origin is not None and origin.is_file():
                    archive.write(origin, ATTACHMENT_PREFIX + entry["stored_name"])
                    included += 1
                    if not entry["digest_matches"]:
                        # Kept anyway -- the bytes that exist are the owner's and
                        # are worth storing -- but a bundle carrying them cannot
                        # be called restorable.
                        corrupt.append(entry["stored_name"])
                else:
                    missing.append(entry["stored_name"])
            archive.writestr(
                MANIFEST_MEMBER,
                json.dumps(
                    {
                        "format_version": BACKUP_FORMAT_VERSION,
                        "created_at": now,
                        "schema_version": schema_version,
                        "database": DATABASE_MEMBER,
                        "counts": counts,
                        "attachments": manifest,
                        "attachments_included": included,
                        "attachments_missing": missing,
                    },
                    indent=2,
                )
                + "\n",
            )
    finally:
        staging.unlink(missing_ok=True)

    bundle.chmod(0o600)
    verification = verify_backup(bundle)
    return WrittenFile(
        path=bundle,
        directory=backups_dir.name,
        created_at=now,
        byte_size=bundle.stat().st_size,
        detail={
            "format_version": BACKUP_FORMAT_VERSION,
            "integrity_check": "ok",
            "counts": counts,
            "attachments_included": included,
            # Named, not hidden: a source file that was deleted from disk should
            # be visible in the backup that could not include it.
            "attachments_missing": len(missing),
            "attachments_corrupt": len(corrupt),
            "verified": verification["ok"],
            "problems": verification["problems"],
            "restorable": verification["ok"] and not missing and not corrupt,
        },
    )


def verify_backup(bundle_path: Path) -> dict[str, Any]:
    """Read a bundle and report whether it could actually be restored.

    Writes nothing and restores nothing. It opens the contained database
    read-only, checks it is internally sound, and then reconciles three
    descriptions of the same set of uploaded originals against each other:

    * what the contained ``sources`` rows say must exist -- the authority;
    * what the manifest lists; and
    * what the archive actually carries.

    Any disagreement fails. Only the third-versus-first comparison catches a
    corrupted or swapped original, and only the first-versus-second catches a
    manifest that simply omits one.
    """
    problems: list[str] = []
    verified = 0
    expected = 0
    manifest: dict[str, Any] = {}
    database = _BundleDatabase()
    try:
        with zipfile.ZipFile(bundle_path) as archive:
            names = archive.namelist()
            problems.extend(_unsafe_members(names))
            present = set(names)

            if MANIFEST_MEMBER not in present:
                return {"ok": False, "problems": problems + ["the bundle has no manifest"]}
            manifest = json.loads(archive.read(MANIFEST_MEMBER))
            if not isinstance(manifest, dict):
                return {"ok": False, "problems": ["the bundle's manifest is not an object"]}

            if DATABASE_MEMBER not in present:
                problems.append("the bundle contains no database")
            else:
                database = _check_database(archive, bundle_path)
                problems.extend(database.problems)

            problems.extend(_version_problems(manifest, database))
            if database.counts is not None:
                problems.extend(_count_problems(manifest, database.counts))

            listed, listing_problems = _listed_attachments(manifest)
            problems.extend(listing_problems)
            expected = len(set(listed) | set(database.required))
            verified, attachment_problems = _reconcile_attachments(
                archive, present, listed, database.required
            )
            problems.extend(attachment_problems)
    except (zipfile.BadZipFile, OSError, ValueError, KeyError) as exc:
        # Whatever was already found still holds -- an unsafe path found before
        # the read failed is the more useful half of the answer.
        return {
            "ok": False,
            "problems": problems + [f"unreadable bundle ({type(exc).__name__})"],
        }

    return {
        "ok": database.ok and not problems,
        "format_version": manifest.get("format_version"),
        "created_at": manifest.get("created_at"),
        "schema_version": manifest.get("schema_version"),
        "counts": database.counts or {},
        "attachments_expected": expected,
        "attachments_verified": verified,
        "problems": problems,
    }


@dataclass(frozen=True)
class _BundleDatabase:
    """What the database inside a bundle says about itself."""

    # None means it could not be read at all; {} would claim an empty schema.
    counts: dict[str, int] | None = None
    schema_version: int | None = None
    # stored_name -> the sha256 and byte_size `sources` recorded for it.
    required: dict[str, dict[str, Any]] = field(default_factory=dict)
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.counts is not None and not self.problems


def _unsafe_members(names: list[str]) -> list[str]:
    """Archive paths refused before anything is read out of them.

    A member that escapes the extraction directory, and a name carried twice --
    where a checker reads one member and an extractor writes the other -- are
    both refused rather than reported alongside a passing verdict.
    """
    problems = [
        f"the archive carries {name} more than once"
        for name, count in sorted(Counter(names).items())
        if count > 1
    ]
    for name in names:
        normalised = name.replace("\\", "/")
        escapes = (
            normalised.startswith("/")
            or ".." in normalised.split("/")
            or (len(name) > 1 and name[1] == ":")
        )
        if escapes:
            problems.append(f"unsafe archive path: {name}")
    return problems


def _listed_attachments(
    manifest: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The manifest's attachment entries, keyed by stored name."""
    entries = manifest.get("attachments", [])
    if not isinstance(entries, list):
        return {}, ["the manifest's attachment list is not a list"]
    listed: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("stored_name"):
            problems.append("the manifest has an attachment entry with no stored name")
            continue
        name = entry["stored_name"]
        if name in listed:
            problems.append(f"the manifest lists {name} twice")
        listed[name] = entry
    return listed, problems


def _member_digest(archive: zipfile.ZipFile, member: str) -> tuple[int, str]:
    """Size and sha256 of one member, read in blocks rather than all at once."""
    hasher = hashlib.sha256()
    size = 0
    with archive.open(member) as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            hasher.update(block)
    return size, hasher.hexdigest()


def _reconcile_attachments(
    archive: zipfile.ZipFile,
    names: set[str],
    listed: dict[str, dict[str, Any]],
    required: dict[str, dict[str, Any]],
) -> tuple[int, list[str]]:
    """Check the manifest and the archive against what the database requires.

    Digests are compared to the *database's* recorded sha256, not the
    manifest's. A manifest edited to match a substituted file agrees with
    itself perfectly and is exactly what this has to catch.
    """
    problems: list[str] = []
    verified = 0
    stored = {
        name[len(ATTACHMENT_PREFIX) :]
        for name in names
        if name.startswith(ATTACHMENT_PREFIX) and name != ATTACHMENT_PREFIX
    }

    for name in sorted(set(listed) - set(required)):
        problems.append(f"the manifest lists an attachment no source row refers to: {name}")
    for name in sorted(stored - set(required)):
        problems.append(f"the archive carries an attachment no source row refers to: {name}")

    for name, expected in sorted(required.items()):
        entry = listed.get(name)
        if entry is None:
            problems.append(f"the manifest omits attachment: {name}")
        elif (entry.get("sha256"), entry.get("byte_size")) != (
            expected["sha256"],
            expected["byte_size"],
        ):
            problems.append(f"the manifest disagrees with the database about {name}")
        if name not in stored:
            problems.append(f"missing attachment: {name}")
            continue
        size, digest = _member_digest(archive, ATTACHMENT_PREFIX + name)
        if digest != expected["sha256"]:
            problems.append(f"attachment digest mismatch: {name}")
        elif size != expected["byte_size"]:
            problems.append(f"attachment size mismatch: {name}")
        elif entry is not None:
            verified += 1
    return verified, problems


def _supported_schema_version() -> int:
    """The newest schema this build knows how to run. 0 if that is unknowable."""
    try:
        return max((migration.version for migration in discover()), default=0)
    except Exception:
        return 0


def _version_problems(
    manifest: dict[str, Any], database: _BundleDatabase
) -> list[str]:
    """Format and schema versions: internally consistent, and restorable here."""
    problems: list[str] = []
    format_version = manifest.get("format_version")
    if not isinstance(format_version, int):
        problems.append("the manifest declares no backup format version")
    elif format_version > BACKUP_FORMAT_VERSION:
        problems.append(
            f"this bundle is backup format {format_version}; this build reads "
            f"{BACKUP_FORMAT_VERSION}"
        )

    claimed = manifest.get("schema_version")
    if not isinstance(claimed, int):
        problems.append("the manifest declares no schema version")
        return problems
    if database.schema_version is not None and claimed != database.schema_version:
        problems.append(
            f"the manifest claims schema version {claimed} but its database is at "
            f"{database.schema_version}"
        )
    supported = _supported_schema_version()
    if supported and claimed > supported:
        problems.append(
            f"this bundle needs schema version {claimed}; this build has {supported}"
        )
    return problems


def _count_problems(manifest: dict[str, Any], counts: dict[str, int]) -> list[str]:
    """The manifest's row counts must match the database it ships with."""
    claimed = manifest.get("counts")
    if not isinstance(claimed, dict):
        return ["the manifest records no table counts"]
    problems: list[str] = []
    for table, actual in sorted(counts.items()):
        if table not in claimed:
            problems.append(f"the manifest records no row count for {table}")
        elif claimed[table] != actual:
            problems.append(
                f"the manifest claims {claimed[table]} rows in {table}; its database "
                f"has {actual}"
            )
    for table in sorted(set(claimed) - set(counts)):
        problems.append(f"the manifest counts a table the database does not have: {table}")
    return problems


def _required_attachments(
    connection: sqlite3.Connection,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Every original the contained `sources` rows say must be in the bundle."""
    required: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    rows = connection.execute(
        "SELECT DISTINCT stored_name, sha256, byte_size FROM sources"
        " ORDER BY stored_name"
    ).fetchall()
    for row in rows:
        expected = {"sha256": row["sha256"], "byte_size": row["byte_size"]}
        if required.setdefault(row["stored_name"], expected) != expected:
            problems.append(
                f"the database gives {row['stored_name']} two different digests"
            )
    return required, problems


def _check_database(archive: zipfile.ZipFile, bundle_path: Path) -> _BundleDatabase:
    """Extract the contained database to a temporary file and check it."""
    import tempfile

    with tempfile.TemporaryDirectory(dir=str(bundle_path.parent)) as workspace:
        target = Path(workspace) / DATABASE_MEMBER
        target.write_bytes(archive.read(DATABASE_MEMBER))
        connection = connect_reader(target)
        try:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return _BundleDatabase(
                    problems=(f"database integrity check failed: {integrity}",)
                )

            problems: list[str] = []
            # Enforcement is per-connection and off here, but the check itself
            # runs regardless: a bundle whose rows point at rows that are not
            # there restores into a broken library.
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                tables = sorted({row[0] for row in violations})
                problems.append(
                    f"{len(violations)} foreign key violation(s) in: {', '.join(tables)}"
                )

            counts: dict[str, int] = {}
            absent: list[str] = []
            for table in EXPORTED_TABLES:
                # Table names come from the module-level tuple, never from input.
                try:
                    counts[table] = connection.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()[0]
                except sqlite3.Error:
                    absent.append(table)
            if absent:
                problems.append(
                    "the bundle's database is missing table(s): " + ", ".join(absent)
                )

            schema_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            required, conflicts = _required_attachments(connection)
            problems.extend(conflicts)
            return _BundleDatabase(
                counts=counts,
                schema_version=schema_version,
                required=required,
                problems=tuple(problems),
            )
        except sqlite3.Error as exc:
            return _BundleDatabase(problems=(f"database unreadable ({type(exc).__name__})",))
        finally:
            connection.close()


def write_text_file(directory: Path, stem: str, suffix: str, text: str, label: str) -> WrittenFile:
    """A readable file in *directory*, named by stem and time; the label is what the reply says."""
    now = utc_now()
    path = _unique_path(directory, f"{stem}-{_timestamp_slug(now)}", suffix)
    data = text.encode("utf-8")
    path.write_bytes(data)
    return WrittenFile(path=path, directory=label, created_at=now, byte_size=len(data), detail={})


def list_written(directory: Path, suffix: str) -> list[dict[str, Any]]:
    """What is already in ``exports/`` or ``backups/``, newest first."""
    if not directory.exists():
        return []
    return [
        {
            "filename": path.name,
            "directory": directory.name,
            "byte_size": path.stat().st_size,
        }
        for path in sorted(directory.glob(f"*{suffix}"), reverse=True)
        if path.is_file()
    ]
