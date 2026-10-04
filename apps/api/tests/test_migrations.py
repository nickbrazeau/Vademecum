"""ADR 0005: forward-only migrations, checksummed."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vademecum.db import apply_migrations, connect
from vademecum.db.migrate import MigrationError, discover

EXPECTED_TABLES = {
    "app_state",
    "build_batches",
    "curated_articles",
    "evidence_links",
    "generation_runs",
    "knowledge_gap_flags",
    "learning_items",
    "learning_point_sources",
    "learning_point_topics",
    "learning_points",
    "literature_checks",
    "map_positions",
    "pending_turns",
    "source_images",
    "schematics",
    "literature_records",
    "literature_topic_records",
    "literature_topics",
    "piles",
    "schema_migrations",
    "source_segments",
    "sources",
    "specialties",
    "topic_specialties",
    "tutor_attempts",
    "tutor_cycle_entries",
    "tutor_question_anchors",
    "tutor_questions",
    "sync_state",
    "sync_log",
    "exam_reports",
    "exam_areas",
    "case_entries",
    "encyclopedia_entries",
    "board_questions",
    "board_cycle_entries",
    "board_attempts",
    "encyclopedia_records",
    "flashcards",
    "flashcard_reviews",
}

# Pinned so a future migration that changes a column has to say so here.
EXPECTED_COLUMNS = {
    "sync_state": {
        "id",
        "node_id",
        "applying",
        "peer_node_id",
        "pulled_through",
        "pushed_through",
        "last_sync_at",
        "last_sync_note",
    },
    "sync_log": {"seq", "table_name", "row_key", "op", "at"},
    "exam_reports": {"id", "display_name", "media_type", "sha256", "byte_size", "stored_name", "text", "status", "status_detail", "created_at", "updated_at", "parsed_at"},
    "exam_areas": {"id", "report_id", "ordinal", "topic", "specialty_id", "standing", "quote", "note", "created_at"},
    "case_entries": {
        "id", "series", "subseries", "external_id", "title", "url", "credit", "published_on", "text", "status",
        "status_detail", "attempts", "one_liner", "points", "think_first", "specialty_id", "synthesised_at",
        "first_seen_at", "created_at", "updated_at",
    },
    "app_state": {
        "key",
        "updated_at",
        "value",
    },
    "build_batches": {
        "committed_at",
        "created_at",
        "excerpt_chars",
        "excerpt_count",
        "id",
        "pile_id",
        "ranges",
        "run_id",
        "selection_hash",
        "status",
    },
    "curated_articles": {
        "created_at",
        "id",
        "published_on",
        "source_name",
        "source_url",
        "summary",
        "title",
    },
    "evidence_links": {
        "basis",
        "evidence_grade",
        "generation_id",
        "id",
        "learning_point_id",
        "quote",
        "record_id",
        "relation",
        "superseded_at",
        "superseded_reason",
        "verified_at",
    },
    "generation_runs": {
        "excerpt_chars",
        "excerpt_count",
        "failure_category",
        "finished_at",
        "heartbeat_at",
        "held_count",
        "id",
        "kind",
        "pile_id",
        "point_count",
        "question_count",
        "source_count",
        "stage",
        "started_at",
        "status",
    },
    "source_images": {
        "id",
        "source_id",
        "ordinal",
        "unit_index",
        "locator",
        "origin",
        "media_type",
        "byte_size",
        "width",
        "height",
        "sha256",
        "stored_name",
        "created_at",
    },
    "schematics": {
        "id",
        "learning_point_id",
        "title",
        "media_type",
        "byte_size",
        "sha256",
        "stored_name",
        "created_at",
    },
    "pending_turns": {
        "context",
        "created_at",
        "expires_at",
        "id",
        "kind",
        "payload",
        "scope_id",
        "scope_kind",
        "status",
        "submitted_at",
    },
    "knowledge_gap_flags": {
        "addressed_at",
        "created_at",
        "id",
        "pile_id",
        "status",
        "text",
        "topic",
        "updated_at",
    },
    "learning_items": {
        "body",
        "content_hash",
        "created_at",
        "id",
        "pile_id",
        "source",
        "title",
        "updated_at",
    },
    "learning_point_sources": {
        "created_at",
        "id",
        "learning_point_id",
        "locator",
        "quote",
        "segment_id",
        "source_id",
    },
    "learning_point_topics": {
        "learning_point_id",
        "topic",
    },
    "learning_points": {
        "claim",
        "content_hash",
        "created_at",
        "detail",
        "evidence_basis",
        "evidence_grade",
        "generation_id",
        "held",
        "hold_reason",
        "id",
        "pile_id",
        "review_state",
        "support",
        "updated_at",
    },
    "literature_checks": {
        "failure_category",
        "finished_at",
        "id",
        "new_count",
        "result_count",
        "started_at",
        "status",
        "topic_id",
        "trigger",
    },
    "literature_records": {
        "abstract",
        "corrected",
        "correction_notes",
        "doi",
        "first_seen_at",
        "id",
        "is_notice",
        "journal",
        "pmid",
        "priority",
        "provider_date",
        "publication_types",
        "published_on",
        "retracted",
        "title",
        "updated_at",
        "url",
    },
    "literature_topic_records": {
        "check_id",
        "first_seen_at",
        "id",
        "record_id",
        "state",
        "topic_id",
        "updated_at",
        "why_relevant",
    },
    "literature_topics": {
        "consecutive_failures",
        "created_at",
        "enabled",
        "id",
        "label",
        "last_checked_at",
        "last_failure",
        "last_status",
        "next_due_at",
        "query",
        "updated_at",
    },
    "piles": {
        "created_at",
        "description",
        "id",
        "tier",
        "title",
        "updated_at",
    },
    "source_segments": {
        "char_count",
        "covered_at",
        "covered_by",
        "covered_upto",
        "id",
        "kind",
        "locator",
        "ordinal",
        "source_id",
        "text",
        "text_hash",
    },
    "sources": {
        "byte_size",
        "char_count",
        "confidence",
        "coverage_json",
        "created_at",
        "display_name",
        "excluded",
        "extracted_at",
        "extraction_hash",
        "id",
        "images_at",
        "media_type",
        "pile_id",
        "sha256",
        "status",
        "status_detail",
        "stored_name",
        "unit_count",
        "unit_kind",
        "updated_at",
    },
    "tutor_attempts": {
        "answer",
        "asked_prompt",
        "asked_reference",
        "asked_rubric",
        "created_at",
        "feedback",
        "graded_by",
        "id",
        "improved_answer",
        "missing_or_unsafe",
        "outcome",
        "question_id",
        "question_version",
        "strengths",
        "uncertainty",
    },
    "tutor_cycle_entries": {
        "created_at",
        "cycle_number",
        "id",
        "position",
        "question_id",
        "served_at",
    },
    "tutor_question_anchors": {
        "created_at",
        "id",
        "locator",
        "question_id",
        "quote",
        "segment_id",
        "source_id",
    },
    "tutor_questions": {
        "assessed_at",
        "assessment",
        "assessment_notes",
        "content_hash",
        "created_at",
        "generation_id",
        "hold_reason",
        "id",
        "learning_point_id",
        "pile_id",
        "prompt",
        "reference_answer",
        "retired_at",
        "retired_reason",
        "rubric",
        "status",
        "updated_at",
        "version",
    },
    "specialties": {"id", "name", "sort_order"},
    "topic_specialties": {"topic", "specialty_id", "assigned_by", "updated_at"},
    "map_positions": {"topic", "x", "y", "updated_at"},
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in rows}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def test_applying_every_migration_produces_the_expected_schema(tmp_path: Path) -> None:
    connection = connect(tmp_path / "db.sqlite3")
    applied = apply_migrations(connection)
    assert applied == [
        "0001_initial_schema",
        "0002_sources_learning_bank_literature",
        "0003_active_evidence_basis",
        "0004_map_specialties_and_positions",
        "0005_pending_turns",
        "0006_images_and_schematics",
        "0007_sync_log",
        "0008_sync_backfill",
        "0009_exam_reports",
        "0010_case_series",
        "0011_encyclopedia",
        "0012_encyclopedia_literature",
        "0013_flashcards",
    ]
    assert _tables(connection) == EXPECTED_TABLES
    for table, columns in EXPECTED_COLUMNS.items():
        assert _columns(connection, table) == columns, table


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    connection = connect(tmp_path / "db.sqlite3")
    apply_migrations(connection)
    assert apply_migrations(connection) == []


@pytest.mark.parametrize("starting_version", [1, 2, 3])
def test_upgrade_preserves_existing_notes_flags_and_confidence(
    tmp_path: Path, starting_version: int
) -> None:
    """An upgrade is not a new library: the old rows must survive byte-for-byte."""
    staging = tmp_path / "old-migrations"
    staging.mkdir()
    for migration in discover():
        if migration.version <= starting_version:
            (staging / migration.path.name).write_bytes(migration.path.read_bytes())
    database = tmp_path / "existing-library.sqlite3"
    connection = connect(database)
    apply_migrations(connection, staging)
    connection.execute(
        "INSERT INTO piles VALUES (?, ?, ?, ?, ?, ?)",
        ("pil_existing", "Existing learning", "low", "Keep this description", "before", "before"),
    )
    connection.execute(
        "INSERT INTO learning_items VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("itm_existing", "pil_existing", "My note", "Keep my written text.", "Lecture", "digest", "before", "before"),
    )
    connection.execute(
        "INSERT INTO knowledge_gap_flags VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("flg_existing", "Revisit this idea", "Methods", "pil_existing", "open", "before", "before", None),
    )
    connection.execute(
        "INSERT INTO curated_articles VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("art_existing", "Saved article", "Keep this summary", "Journal", "", None, "before"),
    )
    tables = ("piles", "learning_items", "knowledge_gap_flags", "curated_articles")
    before = {name: [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")] for name in tables}
    old_checksums = list(connection.execute("SELECT version, checksum FROM schema_migrations ORDER BY version"))
    connection.close()

    # Reopen as startup does, apply forward-only migrations, and verify repeat startup.
    connection = connect(database)
    applied = apply_migrations(connection)
    assert applied[-1] == "0013_flashcards"
    assert apply_migrations(connection) == []
    for name in tables:
        assert [tuple(row) for row in connection.execute(f"SELECT * FROM {name}")] == before[name]
    assert [tuple(row) for row in connection.execute(
        "SELECT version, checksum FROM schema_migrations WHERE version <= ? ORDER BY version",
        (starting_version,),
    )] == [tuple(row) for row in old_checksums]
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()


def test_a_changed_migration_refuses_to_start(tmp_path: Path) -> None:
    source = discover()
    staging = tmp_path / "migrations"
    staging.mkdir()
    for migration in source:
        (staging / migration.path.name).write_bytes(migration.path.read_bytes())

    connection = connect(tmp_path / "db.sqlite3")
    apply_migrations(connection, staging)

    edited = staging / source[0].path.name
    edited.write_text(edited.read_text() + "\n-- an edit after the fact\n")

    with pytest.raises(MigrationError) as excinfo:
        apply_migrations(connection, staging)
    assert "forward-only" in str(excinfo.value)


def test_a_database_ahead_of_the_checkout_refuses_to_start(tmp_path: Path) -> None:
    connection = connect(tmp_path / "db.sqlite3")
    apply_migrations(connection)
    connection.execute(
        "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
        " VALUES (9999, 'from_the_future', 'x', '2026-01-01T00:00:00Z')"
    )
    with pytest.raises(MigrationError) as excinfo:
        apply_migrations(connection)
    assert "older than the database" in str(excinfo.value)


def test_a_failing_migration_leaves_no_partial_schema(tmp_path: Path) -> None:
    staging = tmp_path / "migrations"
    staging.mkdir()
    (staging / "0001_broken.sql").write_text(
        "CREATE TABLE good (id TEXT PRIMARY KEY);\nCREATE TABLE good (id TEXT);\n"
    )
    connection = connect(tmp_path / "db.sqlite3")
    with pytest.raises(sqlite3.Error):
        apply_migrations(connection, staging)
    assert "good" not in _tables(connection)


def test_migration_filenames_are_well_formed() -> None:
    for migration in discover():
        assert migration.path.name == f"{migration.version:04d}_{migration.name}.sql"


def test_foreign_keys_are_on_for_every_connection(tmp_path: Path) -> None:
    connection = connect(tmp_path / "db.sqlite3")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
