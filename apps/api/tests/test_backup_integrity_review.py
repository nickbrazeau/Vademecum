"""A backup may only call itself restorable if it demonstrably is.

Everything here runs against a temporary database and temporary attachment
files. Nothing touches a real data directory, a provider, or the network.

The defects these cover all shared one shape: the bundle was checked against its
own manifest, so a manifest that was wrong in a self-consistent way passed.

* ``verify_backup`` read only ``manifest["attachments"]``. A bundle whose
  manifest listed nothing verified clean while the database inside it referred
  to uploaded originals that were absent -- the exact bundle an owner would
  discover was worthless only when they needed it.
* Nothing compared the manifest's ``schema_version`` or row counts with the
  database it shipped with, and no foreign-key check ran.
* ``write_backup`` set ``restorable = not missing``, so a bundle carrying
  originals whose bytes no longer matched the recorded digest was labelled
  restorable.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import warnings
import zipfile
from pathlib import Path

from vademecum.storage.maintenance import (
    ATTACHMENT_PREFIX,
    DATABASE_MEMBER,
    MANIFEST_MEMBER,
    export_payload,
    missing_from_export,
    verify_backup,
    write_backup,
)

SOURCE_COLUMNS = (
    "id, pile_id, display_name, stored_name, media_type, byte_size, sha256,"
    " confidence, status, created_at, updated_at"
)
WHEN = "2026-01-01T00:00:00+00:00"
SAME = object()  # "the file on disk is the file the row records"


def _pile(connection: sqlite3.Connection, pile_id: str = "pil_test") -> str:
    connection.execute(
        "INSERT OR IGNORE INTO piles (id, title, tier, description, created_at,"
        " updated_at) VALUES (?, 'Cardiology', 'high', '', ?, ?)",
        (pile_id, WHEN, WHEN),
    )
    return pile_id


def _add_source(
    connection: sqlite3.Connection,
    source_dir: Path,
    *,
    source_id: str,
    recorded: bytes,
    on_disk: object = SAME,
    pile_id: str = "pil_test",
    enforce_keys: bool = True,
) -> str:
    """A `sources` row plus the original it refers to.

    ``on_disk`` may differ from ``recorded`` (the file was corrupted after
    upload) or be ``None`` (the file was deleted).
    """
    digest = hashlib.sha256(recorded).hexdigest()
    stored_name = f"{digest}.pdf"
    body = recorded if on_disk is SAME else on_disk
    if body is not None:
        (source_dir / stored_name).write_bytes(body)
    values = (
        source_id,
        pile_id,
        f"{source_id}.pdf",
        stored_name,
        "application/pdf",
        len(recorded),
        digest,
        "high",
        "extracted",
        WHEN,
        WHEN,
    )
    statement = (
        f"INSERT INTO sources ({SOURCE_COLUMNS})"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    if enforce_keys:
        connection.execute(statement, values)
    else:
        # A second connection, without PRAGMA foreign_keys, is the only way to
        # get the orphan row that `foreign_key_check` has to find.
        raw = sqlite3.connect(str(Path(connection.execute("PRAGMA database_list")
                                       .fetchone()[2])))
        try:
            raw.execute(statement, values)
            raw.commit()
        finally:
            raw.close()
    return stored_name


def _rebuild(
    source: Path,
    target: Path,
    *,
    manifest_edit=None,
    replace: dict[str, bytes] | None = None,
    drop: frozenset[str] = frozenset(),
    extra: dict[str, bytes] | None = None,
    duplicate: str | None = None,
) -> Path:
    """A copy of a bundle with specific damage done to it."""
    with zipfile.ZipFile(source) as original:
        members = [(info.filename, original.read(info.filename)) for info in original.infolist()]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # duplicate member names are the point
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as rebuilt:
            for name, payload in members:
                if name in drop:
                    continue
                if name == MANIFEST_MEMBER and manifest_edit is not None:
                    manifest = json.loads(payload)
                    manifest_edit(manifest)
                    payload = (json.dumps(manifest, indent=2) + "\n").encode()
                if replace and name in replace:
                    payload = replace[name]
                rebuilt.writestr(name, payload)
                if duplicate == name:
                    rebuilt.writestr(name, b"a second member wearing the same name")
            for name, payload in (extra or {}).items():
                rebuilt.writestr(name, payload)
    return target


def _library(connection: sqlite3.Connection, source_dir: Path) -> list[str]:
    """Two piles' worth of uploaded originals, all present and correct."""
    _pile(connection)
    return [
        _add_source(connection, source_dir, source_id="src_a", recorded=b"First original, a PDF as far as anyone here cares."),
        _add_source(connection, source_dir, source_id="src_b", recorded=b"Second original, with entirely different bytes."),
    ]


def _sources_dir(data_dir: Path) -> Path:
    return data_dir / "attachments" / "sources"


def _problems(report: dict) -> str:
    return " | ".join(report["problems"])


# --- a good bundle still passes ----------------------------------------------


def test_a_backup_carrying_its_originals_verifies_clean(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)

    written = write_backup(database_path, data_dir / "backups", source_dir)

    assert written.detail["attachments_included"] == 2
    assert written.detail["attachments_missing"] == 0
    assert written.detail["attachments_corrupt"] == 0
    assert written.detail["verified"] is True
    assert written.detail["restorable"] is True
    assert written.detail["problems"] == []

    report = verify_backup(written.path)
    assert report["ok"] is True, _problems(report)
    assert report["problems"] == []
    assert report["attachments_expected"] == 2
    assert report["attachments_verified"] == 2
    assert report["counts"]["sources"] == 2
    assert report["schema_version"] >= 1
    with zipfile.ZipFile(written.path) as archive:
        assert set(archive.namelist()) >= {ATTACHMENT_PREFIX + name for name in names}


def test_a_backup_with_no_sources_at_all_still_verifies(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """The shape every existing legitimate backup has. Must keep passing."""
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))

    assert written.detail["restorable"] is True
    report = verify_backup(written.path)
    assert report["ok"] is True, _problems(report)
    assert report["attachments_expected"] == 0


def test_an_export_still_carries_every_table_and_every_attachment(
    connection: sqlite3.Connection, data_dir: Path
) -> None:
    """Tightening the backup must not narrow what an export preserves."""
    source_dir = _sources_dir(data_dir)
    _library(connection, source_dir)
    connection.execute(
        "INSERT INTO learning_items (id, pile_id, title, body, content_hash,"
        " created_at, updated_at)"
        " VALUES ('lit_1', 'pil_test', 'Duke criteria', 'Body', 'hash', ?, ?)",
        (WHEN, WHEN),
    )

    payload = export_payload(connection, source_dir)

    assert missing_from_export(connection) == []
    assert payload["vademecum_export"]["tables_omitted"] == []
    assert len(payload["tables"]["sources"]) == 2
    assert payload["tables"]["learning_items"][0]["title"] == "Duke criteria"
    assert payload["tables"]["piles"][0]["title"] == "Cardiology"
    assert len(payload["attachments"]) == 2
    assert all(entry["present"] for entry in payload["attachments"])
    assert all(entry["digest_matches"] for entry in payload["attachments"])


# --- the manifest is not the authority ---------------------------------------


def test_a_manifest_listing_no_attachments_cannot_verify(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """The headline defect: an empty list used to mean "nothing to check"."""
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)

    stripped = _rebuild(
        written.path,
        data_dir / "backups" / "stripped.zip",
        manifest_edit=lambda manifest: manifest.update({"attachments": [], "attachments_included": 0}),
        drop=frozenset(ATTACHMENT_PREFIX + name for name in names),
    )

    report = verify_backup(stripped)
    assert report["ok"] is False
    assert report["attachments_expected"] == 2
    assert report["attachments_verified"] == 0
    for name in names:
        assert f"the manifest omits attachment: {name}" in report["problems"]
        assert f"missing attachment: {name}" in report["problems"]


def test_a_manifest_omission_fails_even_when_the_bytes_are_present(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)

    quiet = _rebuild(
        written.path,
        data_dir / "backups" / "quiet.zip",
        manifest_edit=lambda manifest: manifest.update({"attachments": []}),
    )

    report = verify_backup(quiet)
    assert report["ok"] is False
    assert f"the manifest omits attachment: {names[0]}" in report["problems"]
    assert report["attachments_verified"] == 0


def test_an_original_dropped_from_both_manifest_and_archive_still_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """Manifest and archive agree with each other. Only the database disagrees."""
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)
    victim = names[0]

    def forget(manifest: dict) -> None:
        manifest["attachments"] = [
            entry for entry in manifest["attachments"] if entry["stored_name"] != victim
        ]
        manifest["attachments_included"] = 1

    trimmed = _rebuild(
        written.path,
        data_dir / "backups" / "trimmed.zip",
        manifest_edit=forget,
        drop=frozenset({ATTACHMENT_PREFIX + victim}),
    )

    report = verify_backup(trimmed)
    assert report["ok"] is False
    assert f"the manifest omits attachment: {victim}" in report["problems"]
    assert f"missing attachment: {victim}" in report["problems"]
    assert report["attachments_verified"] == 1


def test_a_corrupted_original_inside_the_bundle_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)

    rotted = _rebuild(
        written.path,
        data_dir / "backups" / "rotted.zip",
        replace={ATTACHMENT_PREFIX + names[0]: b"bytes that are not the ones recorded"},
    )

    report = verify_backup(rotted)
    assert report["ok"] is False
    assert f"attachment digest mismatch: {names[0]}" in report["problems"]
    assert report["attachments_verified"] == 1


def test_two_originals_swapped_for_each_other_fail(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)
    with zipfile.ZipFile(written.path) as archive:
        first = archive.read(ATTACHMENT_PREFIX + names[0])
        second = archive.read(ATTACHMENT_PREFIX + names[1])

    swapped = _rebuild(
        written.path,
        data_dir / "backups" / "swapped.zip",
        replace={
            ATTACHMENT_PREFIX + names[0]: second,
            ATTACHMENT_PREFIX + names[1]: first,
        },
    )

    report = verify_backup(swapped)
    assert report["ok"] is False
    assert f"attachment digest mismatch: {names[0]}" in report["problems"]
    assert f"attachment digest mismatch: {names[1]}" in report["problems"]
    assert report["attachments_verified"] == 0


def test_a_manifest_edited_to_agree_with_a_substituted_file_still_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """A self-consistent manifest is exactly what checking the database catches."""
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)
    substitute = b"a different document entirely, and the manifest agrees"
    victim = names[0]

    def relabel(manifest: dict) -> None:
        for entry in manifest["attachments"]:
            if entry["stored_name"] == victim:
                entry["sha256"] = hashlib.sha256(substitute).hexdigest()
                entry["byte_size"] = len(substitute)

    forged = _rebuild(
        written.path,
        data_dir / "backups" / "forged.zip",
        manifest_edit=relabel,
        replace={ATTACHMENT_PREFIX + victim: substitute},
    )

    report = verify_backup(forged)
    assert report["ok"] is False
    assert f"the manifest disagrees with the database about {victim}" in report["problems"]
    assert f"attachment digest mismatch: {victim}" in report["problems"]


def test_an_attachment_no_source_row_refers_to_is_reported(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)

    padded = _rebuild(
        written.path,
        data_dir / "backups" / "padded.zip",
        extra={ATTACHMENT_PREFIX + "stowaway.pdf": b"not in the database"},
    )

    report = verify_backup(padded)
    assert report["ok"] is False
    assert (
        "the archive carries an attachment no source row refers to: stowaway.pdf"
        in report["problems"]
    )


# --- the database inside the bundle must be sound ----------------------------


def test_a_manifest_row_count_that_disagrees_with_its_database_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)

    def inflate(manifest: dict) -> None:
        manifest["counts"]["sources"] = 99

    miscounted = _rebuild(
        written.path, data_dir / "backups" / "miscounted.zip", manifest_edit=inflate
    )

    report = verify_backup(miscounted)
    assert report["ok"] is False
    assert "the manifest claims 99 rows in sources; its database has 2" in report["problems"]


def test_a_manifest_missing_a_table_count_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))

    def forget(manifest: dict) -> None:
        del manifest["counts"]["piles"]

    partial = _rebuild(
        written.path, data_dir / "backups" / "partial.zip", manifest_edit=forget
    )

    report = verify_backup(partial)
    assert report["ok"] is False
    assert "the manifest records no row count for piles" in report["problems"]


def test_a_manifest_schema_version_that_disagrees_with_its_database_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    actual = verify_backup(written.path)["schema_version"]

    def lie(manifest: dict) -> None:
        manifest["schema_version"] = actual + 1

    mislabelled = _rebuild(
        written.path, data_dir / "backups" / "mislabelled.zip", manifest_edit=lie
    )

    report = verify_backup(mislabelled)
    assert report["ok"] is False
    assert any("but its database is at" in problem for problem in report["problems"])


def test_a_bundle_needing_a_newer_schema_than_this_build_has_is_refused(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """Internally consistent, and still not restorable by this checkout."""
    connection.execute(
        "INSERT INTO schema_migrations (version, name, checksum, applied_at)"
        " VALUES (9999, 'from_the_future', 'x', ?)",
        (WHEN,),
    )
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))

    report = verify_backup(written.path)
    assert report["ok"] is False
    assert any(
        "needs schema version 9999" in problem for problem in report["problems"]
    ), _problems(report)
    assert written.detail["restorable"] is False


def test_a_bundle_in_a_newer_backup_format_is_refused(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))

    def bump(manifest: dict) -> None:
        manifest["format_version"] = 99

    future = _rebuild(written.path, data_dir / "backups" / "future.zip", manifest_edit=bump)

    report = verify_backup(future)
    assert report["ok"] is False
    assert any("backup format 99" in problem for problem in report["problems"])


def test_foreign_key_violations_inside_the_bundle_fail(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """An orphan row restores into a library with a source belonging to no pile."""
    source_dir = _sources_dir(data_dir)
    _pile(connection)
    _add_source(
        connection,
        source_dir,
        source_id="src_orphan",
        recorded=b"An original whose pile does not exist.",
        pile_id="pil_does_not_exist",
        enforce_keys=False,
    )
    written = write_backup(database_path, data_dir / "backups", source_dir)

    report = verify_backup(written.path)
    assert report["ok"] is False
    assert any("foreign key violation" in problem for problem in report["problems"])
    assert any("sources" in problem for problem in report["problems"])
    assert written.detail["restorable"] is False


def test_a_bundle_with_no_database_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    hollow = _rebuild(
        written.path, data_dir / "backups" / "hollow.zip", drop=frozenset({DATABASE_MEMBER})
    )

    report = verify_backup(hollow)
    assert report["ok"] is False
    assert "the bundle contains no database" in report["problems"]


def test_a_bundle_with_no_manifest_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    mute = _rebuild(
        written.path, data_dir / "backups" / "mute.zip", drop=frozenset({MANIFEST_MEMBER})
    )

    report = verify_backup(mute)
    assert report["ok"] is False
    assert "the bundle has no manifest" in report["problems"]


def test_a_corrupt_database_inside_the_bundle_fails(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    with zipfile.ZipFile(written.path) as archive:
        payload = archive.read(DATABASE_MEMBER)

    shredded = _rebuild(
        written.path,
        data_dir / "backups" / "shredded.zip",
        replace={DATABASE_MEMBER: payload[: len(payload) // 2]},
    )

    report = verify_backup(shredded)
    assert report["ok"] is False
    assert any("database" in problem for problem in report["problems"]), _problems(report)
    assert report["counts"] == {}


# --- archive paths ------------------------------------------------------------


def test_an_archive_path_that_escapes_the_bundle_is_refused(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    escaping = _rebuild(
        written.path,
        data_dir / "backups" / "escaping.zip",
        extra={"../elsewhere.txt": b"written outside the restore directory"},
    )

    report = verify_backup(escaping)
    assert report["ok"] is False
    assert "unsafe archive path: ../elsewhere.txt" in report["problems"]


def test_an_absolute_archive_path_is_refused(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    _pile(connection)
    written = write_backup(database_path, data_dir / "backups", _sources_dir(data_dir))
    rooted = _rebuild(
        written.path,
        data_dir / "backups" / "rooted.zip",
        extra={"/etc/cron.d/whatever": b"absolute"},
    )

    report = verify_backup(rooted)
    assert report["ok"] is False
    assert any("unsafe archive path" in problem for problem in report["problems"])


def test_a_member_carried_twice_is_refused(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """One member gets verified, the other gets extracted. Refuse the pair."""
    source_dir = _sources_dir(data_dir)
    names = _library(connection, source_dir)
    written = write_backup(database_path, data_dir / "backups", source_dir)
    member = ATTACHMENT_PREFIX + names[0]

    doubled = _rebuild(
        written.path, data_dir / "backups" / "doubled.zip", duplicate=member
    )

    report = verify_backup(doubled)
    assert report["ok"] is False
    assert f"the archive carries {member} more than once" in report["problems"]


def test_a_bundle_that_is_not_a_zip_fails_without_raising(data_dir: Path) -> None:
    rubbish = data_dir / "backups" / "rubbish.zip"
    rubbish.parent.mkdir(parents=True, exist_ok=True)
    rubbish.write_bytes(b"PK\x03\x04 and then nothing that follows the format")

    report = verify_backup(rubbish)
    assert report["ok"] is False
    assert report["problems"]


# --- write_backup tells the truth about what it wrote -------------------------


def test_a_deleted_original_is_reported_and_not_called_restorable(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    source_dir = _sources_dir(data_dir)
    _pile(connection)
    _add_source(
        connection,
        source_dir,
        source_id="src_gone",
        recorded=b"An original the owner deleted from disk.",
        on_disk=None,
    )

    written = write_backup(database_path, data_dir / "backups", source_dir)

    assert written.detail["attachments_included"] == 0
    assert written.detail["attachments_missing"] == 1
    assert written.detail["verified"] is False
    assert written.detail["restorable"] is False
    assert verify_backup(written.path)["ok"] is False


def test_a_corrupted_original_is_reported_and_not_called_restorable(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """The defect exactly: present but wrong used to count as restorable."""
    source_dir = _sources_dir(data_dir)
    _pile(connection)
    _add_source(
        connection,
        source_dir,
        source_id="src_rotted",
        recorded=b"The bytes this source was uploaded with.",
        on_disk=b"Bytes that replaced them at some point since.",
    )

    written = write_backup(database_path, data_dir / "backups", source_dir)

    assert written.detail["attachments_included"] == 1
    assert written.detail["attachments_missing"] == 0
    assert written.detail["attachments_corrupt"] == 1
    assert written.detail["verified"] is False
    assert written.detail["restorable"] is False
    assert any("digest mismatch" in problem for problem in written.detail["problems"])


def test_the_backup_report_still_names_no_filesystem_path(
    connection: sqlite3.Connection, data_dir: Path, database_path: Path
) -> None:
    """New detail fields must not undo ADR 0002 rule 6."""
    source_dir = _sources_dir(data_dir)
    _add_source(
        connection,
        _pile(connection) and source_dir,
        source_id="src_p",
        recorded=b"An original with a perfectly ordinary body.",
        on_disk=None,
    )
    written = write_backup(database_path, data_dir / "backups", source_dir)

    body = json.dumps(written.as_dict())
    assert str(data_dir) not in body
    assert "path" not in written.as_dict()
