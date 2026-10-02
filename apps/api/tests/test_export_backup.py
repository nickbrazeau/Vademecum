"""Export and backup: readable by a human, restorable by SQLite."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from vademecum.db import connect
from vademecum.storage import flags, piles
from vademecum.storage.maintenance import (
    DATABASE_MEMBER,
    EXPORTED_TABLES,
    missing_from_export,
    verify_backup,
    write_backup,
    write_export,
)


def open_bundle(path: Path) -> sqlite3.Connection:
    """The database inside a backup bundle, extracted to a scratch file."""
    import tempfile
    import zipfile

    workspace = Path(tempfile.mkdtemp())
    with zipfile.ZipFile(path) as archive:
        target = workspace / DATABASE_MEMBER
        target.write_bytes(archive.read(DATABASE_MEMBER))
    return connect(target)


def _populate(connection: sqlite3.Connection) -> None:
    pile = piles.create_pile(connection, title="Endocarditis", tier="high")
    piles.create_item(connection, pile_id=pile.id, title="Duke criteria", body="Body")
    flags.create_flag(connection, text="Unsure when to image", topic="Cardiology")


def test_an_export_is_readable_json_containing_every_table(
    connection: sqlite3.Connection, data_dir: Path
) -> None:
    _populate(connection)
    written = write_export(connection, data_dir / "exports")

    payload = json.loads(written.path.read_text(encoding="utf-8"))
    assert set(payload["tables"]) == set(EXPORTED_TABLES)
    assert payload["vademecum_export"]["format_version"] == 2
    assert payload["vademecum_export"]["schema_version"] >= 1
    assert payload["tables"]["piles"][0]["title"] == "Endocarditis"
    assert payload["tables"]["knowledge_gap_flags"][0]["text"] == "Unsure when to image"
    # Human-readable means indented and openable in any editor.
    assert written.path.read_text(encoding="utf-8").startswith("{\n  ")
    # Nothing in the live schema may be silently left out of an export.
    assert missing_from_export(connection) == []
    assert payload["vademecum_export"]["tables_omitted"] == []


def test_an_export_never_reports_a_filesystem_path(
    connection: sqlite3.Connection, data_dir: Path
) -> None:
    written = write_export(connection, data_dir / "exports")
    body = written.as_dict()
    assert body["directory"] == "exports"
    assert "path" not in body
    assert str(data_dir) not in json.dumps(body)


def test_two_exports_in_the_same_moment_do_not_overwrite_each_other(
    connection: sqlite3.Connection, data_dir: Path
) -> None:
    first = write_export(connection, data_dir / "exports")
    second = write_export(connection, data_dir / "exports")
    assert first.path != second.path
    assert first.path.exists() and second.path.exists()


def test_a_backup_is_a_consistent_openable_database(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _populate(connection)
    written = write_backup(database_path, data_dir / "backups")
    assert written.detail["integrity_check"] == "ok"

    restored = open_bundle(written.path)
    assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [row["title"] for row in restored.execute("SELECT title FROM piles")] == [
        "Endocarditis"
    ]
    assert restored.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
    restored.close()


def test_a_backup_taken_mid_write_sees_committed_rows_only(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """The writer keeps its transaction open across the whole backup.

    A backup driven from that same connection would either deadlock on it or
    copy the uncommitted row. Reading the database file through a separate
    connection does neither.
    """
    _populate(connection)
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO piles (id, title, tier, description, created_at, updated_at)"
        " VALUES ('pil_uncommitted', 'Uncommitted', 'low', '', '2026-01-01', '2026-01-01')"
    )
    try:
        written = write_backup(database_path, data_dir / "backups")
    finally:
        connection.execute("ROLLBACK")

    restored = open_bundle(written.path)
    titles = {row["title"] for row in restored.execute("SELECT title FROM piles")}
    restored.close()
    assert titles == {"Endocarditis"}


def test_a_backup_does_not_disturb_the_writer_that_was_mid_transaction(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """The open transaction survives the backup and can still commit."""
    _populate(connection)
    connection.execute("BEGIN")
    connection.execute(
        "INSERT INTO piles (id, title, tier, description, created_at, updated_at)"
        " VALUES ('pil_later', 'Committed later', 'low', '', '2026-01-01', '2026-01-01')"
    )
    write_backup(database_path, data_dir / "backups")
    connection.execute("COMMIT")

    titles = {row["title"] for row in connection.execute("SELECT title FROM piles")}
    assert titles == {"Endocarditis", "Committed later"}


def test_a_second_backup_sees_what_the_first_one_missed(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _populate(connection)
    first = write_backup(database_path, data_dir / "backups")
    piles.create_pile(connection, title="Neutropenic fever", tier="mid")
    second = write_backup(database_path, data_dir / "backups")

    def titles(path: Path) -> set[str]:
        restored = open_bundle(path)
        try:
            return {row["title"] for row in restored.execute("SELECT title FROM piles")}
        finally:
            restored.close()

    assert titles(first.path) == {"Endocarditis"}
    assert titles(second.path) == {"Endocarditis", "Neutropenic fever"}


def test_a_backup_leaves_no_wal_sidecar_to_restore_wrongly(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    written = write_backup(database_path, data_dir / "backups")
    assert not written.path.with_name(written.path.name + "-wal").exists()
    assert not written.path.with_name(written.path.name + "-shm").exists()


def test_export_and_backup_over_http(client: TestClient) -> None:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    client.post(f"/api/piles/{pile['id']}/items", json={"title": "Lactate"})

    export = client.post("/api/export")
    assert export.status_code == 201
    assert export.json()["directory"] == "exports"
    assert export.json()["counts"]["piles"] == 1
    assert export.json()["byte_size"] > 0

    backup = client.post("/api/backup")
    assert backup.status_code == 201
    assert backup.json()["integrity_check"] == "ok"
    assert backup.json()["restorable"] is True
    verified = client.post(f"/api/backup/{backup.json()['filename']}/verify")
    assert verified.status_code == 200
    assert verified.json()["ok"] is True

    assert [entry["filename"] for entry in client.get("/api/export").json()] == [
        export.json()["filename"]
    ]
    assert len(client.get("/api/backup").json()) == 1


def test_written_files_are_owner_only(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    export = write_export(connection, data_dir / "exports")
    backup = write_backup(database_path, data_dir / "backups")
    assert export.path.stat().st_mode & 0o077 == 0
    assert backup.path.stat().st_mode & 0o077 == 0
