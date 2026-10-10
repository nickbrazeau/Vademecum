"""The seat's supervisor (ADR 0017): restore, snapshot, and what it insists on."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("seat", HERE / "seat.py")
seat = importlib.util.module_from_spec(spec)
sys.modules["seat"] = seat
spec.loader.exec_module(seat)


class MemoryStore:
    """The snapshot store, in memory: what the Worker does with its bucket."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.times: dict[str, str] = {}
        self.puts: list[str] = []
        self.clock = 0

    def list(self, prefix: str) -> list[tuple[str, int]]:
        return [(key, len(data)) for key, data in sorted(self.objects.items()) if key.startswith(prefix)]

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put(self, key: str, data: bytes) -> bool:
        self.objects[key] = data
        self.clock += 1
        self.times[key] = f"2026-10-09T00:00:{self.clock:02d}.000Z"
        self.puts.append(key)
        return True

    def delete(self, key: str) -> bool:
        self.objects.pop(key, None)
        self.times.pop(key, None)
        return True

    def uploaded(self, prefix: str) -> dict[str, str]:
        return {key: at for key, at in self.times.items() if key.startswith(prefix)}


def test_a_fresh_container_restores_the_databases_first_and_the_files_later(tmp_path: Path) -> None:
    store = MemoryStore()
    data = tmp_path / "data"
    assert seat.restore_databases(data, store) == "fresh"

    records = b"SQLite format 3\x00records"
    access = b"SQLite format 3\x00access"
    store.objects["db/vademecum.sqlite3"] = records
    store.objects["mcp/access.sqlite3"] = access
    store.objects["attachments/sources/abc.pdf"] = b"%PDF-"
    data2 = tmp_path / "data2"
    assert seat.restore_databases(data2, store) == "restored"
    assert (data2 / "vademecum.sqlite3").read_bytes() == records
    assert (data2 / "mcp" / "access.sqlite3").read_bytes() == access
    assert not (data2 / "attachments").exists(), "files come later, in the background"
    assert seat.restore_files(data2, store) == 1
    assert (data2 / "attachments" / "sources" / "abc.pdf").read_bytes() == b"%PDF-"
    assert seat.restore_files(data2, store) == 0, "already there, same size"
    assert seat.restore_databases(data2, store) == "present"


def test_snapshot_copies_each_database_consistently_and_uploads_only_new_files(tmp_path: Path) -> None:
    store = MemoryStore()
    data = tmp_path / "data"
    (data / "mcp").mkdir(parents=True)
    (data / "attachments" / "sources").mkdir(parents=True)
    with sqlite3.connect(data / "vademecum.sqlite3") as live:
        live.execute("CREATE TABLE t (x)")
        live.execute("INSERT INTO t VALUES (1)")
    with sqlite3.connect(data / "mcp" / "access.sqlite3") as live:
        live.execute("CREATE TABLE a (y)")
    (data / "attachments" / "sources" / "abc.pdf").write_bytes(b"%PDF-1")
    (data / "attachments" / "sources" / ".part").write_bytes(b"half")

    seat._uploaded.clear()  # noqa: SLF001 - a fresh boot
    first = seat.snapshot(data, store)
    assert first == {"db": 2, "unchanged": 0, "files": 1, "failed": 0, "pruned": 0}
    assert sorted(store.objects) == ["attachments/sources/abc.pdf", "db/vademecum.sqlite3", "mcp/access.sqlite3"]
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(store.objects["db/vademecum.sqlite3"])
    with sqlite3.connect(copy) as backup:
        assert backup.execute("SELECT x FROM t").fetchone() == (1,)

    second = seat.snapshot(data, store)
    assert second == {"db": 0, "unchanged": 2, "files": 0, "failed": 0, "pruned": 0}, "nothing changed, nothing sent"
    with sqlite3.connect(data / "vademecum.sqlite3") as live:
        live.execute("INSERT INTO t VALUES (2)")
    third = seat.snapshot(data, store)
    assert third == {"db": 1, "unchanged": 1, "files": 0, "failed": 0, "pruned": 0}, "only the database that changed"


def test_a_large_object_goes_up_in_parts_and_comes_back_whole(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(seat, "PART_BYTES", 10)
    store = MemoryStore()
    big = tmp_path / "big.bin"
    big.write_bytes(bytes(range(256)) * 2)  # 512 bytes: 52 parts
    assert seat.put_object(store, "attachments/sources/big.bin", big) is True
    assert "attachments/sources/big.bin.manifest" in store.objects
    assert len([k for k in store.objects if ".part-" in k]) == 52
    assert seat.get_object(store, "attachments/sources/big.bin") == big.read_bytes()
    # A restore skips the parts and the manifest as files of their own.
    data = tmp_path / "data"
    assert seat.restore_files(data, store) == 1
    assert (data / "attachments" / "sources" / "big.bin").read_bytes() == big.read_bytes()


def test_the_seat_refuses_to_start_without_its_secrets(monkeypatch) -> None:
    for name in ("VADEMECUM_MCP_PUBLIC_URL", "VADEMECUM_SYNC_ACCEPT_TOKEN", "VADEMECUM_MCP_PASSPHRASE", "SEAT_STORE_URL", "SEAT_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert seat.run() == 2


def test_the_store_insists_on_https() -> None:
    import pytest

    with pytest.raises(ValueError):
        seat.WorkerStore("http://seat.example/__seat/", "k")
    store = seat.WorkerStore("https://seat.example/__seat/", "k")
    assert store._prefix == "/__seat"  # noqa: SLF001


def test_the_api_on_the_seat_is_away_single_tenancy_host_mode(monkeypatch) -> None:
    monkeypatch.setenv("VADEMECUM_SYNC_ACCEPT_TOKEN", "t")
    env = seat.api_environment()
    assert env["VADEMECUM_SYNC_ROLE"] == "foris"
    assert env["VADEMECUM_TENANCY"] == "single"
    assert env["VADEMECUM_MODEL_PROVIDER"] == "host"
    assert env["VADEMECUM_SOURCES_FOLDER_ENABLED"] == "false"
    gateway = seat.gateway_environment()
    assert gateway["VADEMECUM_MCP_PORT"] == str(seat.MCP_PORT)
    assert gateway["VADEMECUM_MCP_LISTEN_ALL"] == "true", "the Worker reaches it on the private interface"
    assert "VADEMECUM_HOST" not in gateway, "the API behind it stays on loopback"



def test_retired_podcast_audio_leaves_the_store_but_nothing_else_does(tmp_path: Path) -> None:
    """ADR 0027: an episode listened to has its audio deleted on disk; the next
    snapshot deletes it from the store too, but only once the boot restore is done,
    and never anything outside the podcast folder."""
    store = MemoryStore()
    data = tmp_path / "data"
    (data / "attachments" / "podcasts").mkdir(parents=True)
    (data / "attachments" / "sources").mkdir(parents=True)
    (data / "attachments" / "podcasts" / "pod_a.m4a").write_bytes(b"a" * 10)
    (data / "attachments" / "podcasts" / "pod_b.m4a").write_bytes(b"b" * 10)
    (data / "attachments" / "sources" / "x.pdf").write_bytes(b"x")
    seat._files_restored.clear()
    seat.snapshot(data, store)
    assert "attachments/podcasts/pod_a.m4a" in store.objects
    (data / "attachments" / "podcasts" / "pod_a.m4a").unlink()
    (data / "attachments" / "sources" / "x.pdf").unlink()
    assert seat.snapshot(data, store)["pruned"] == 0, "not before the restore has finished"
    seat._files_restored.set()
    try:
        assert seat.snapshot(data, store)["pruned"] == 1
    finally:
        seat._files_restored.clear()
    assert "attachments/podcasts/pod_a.m4a" not in store.objects
    assert "attachments/podcasts/pod_b.m4a" in store.objects
    assert "attachments/sources/x.pdf" in store.objects, "only podcast audio and page figures are ever pruned"
    # A page figure no page places any more leaves the store the same way (feedback of 6 October).
    (data / "attachments" / "figures").mkdir(parents=True)
    (data / "attachments" / "figures" / "img_abcdef12.png").write_bytes(b"f" * 10)
    seat.snapshot(data, store)
    assert "attachments/figures/img_abcdef12.png" in store.objects
    (data / "attachments" / "figures" / "img_abcdef12.png").unlink()
    seat._files_restored.set()
    try:
        seat.snapshot(data, store)
    finally:
        seat._files_restored.clear()
    assert "attachments/figures/img_abcdef12.png" not in store.objects


def _database(path: Path, rows: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS t (x TEXT)")
        db.execute("DELETE FROM t")
        db.executemany("INSERT INTO t VALUES (?)", [("x" * 1000,) for _ in range(rows)])
    with sqlite3.connect(path) as db:
        db.execute("VACUUM")


def test_a_database_that_shrank_below_the_part_size_is_never_restored_from_old_parts(tmp_path: Path, monkeypatch) -> None:
    """Feedback of 9 October: a manifest left from when the database was larger was
    preferred on restore, bringing back an old copy at every container start."""
    monkeypatch.setattr(seat, "PART_BYTES", 64 * 1024)
    store = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", 200)  # larger than a part: goes up in parts
    seat._uploaded.clear()
    seat.snapshot(data, store)
    assert "db/vademecum.sqlite3.manifest" in store.objects
    _database(data / "vademecum.sqlite3", 3)  # now small: one object
    seat._uploaded.clear()
    seat.snapshot(data, store)
    assert "db/vademecum.sqlite3" in store.objects
    assert not any(key.startswith("db/vademecum.sqlite3.") for key in store.objects), "the old parts and manifest are gone"
    fresh = tmp_path / "fresh"
    seat.restore_databases(fresh, store)
    with sqlite3.connect(fresh / "vademecum.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_where_a_stale_manifest_survives_the_newer_single_copy_is_restored(tmp_path: Path, monkeypatch) -> None:
    """A store written by the old code still holds a stale manifest beside a newer object."""
    monkeypatch.setattr(seat, "PART_BYTES", 64 * 1024)
    store = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", 200)
    seat._uploaded.clear()
    seat.snapshot(data, store)
    _database(data / "vademecum.sqlite3", 3)
    store.put("db/vademecum.sqlite3", (data / "vademecum.sqlite3").read_bytes())  # as the old code did: the manifest stays
    assert "db/vademecum.sqlite3.manifest" in store.objects
    fresh = tmp_path / "fresh"
    seat.restore_databases(fresh, store)
    with sqlite3.connect(fresh / "vademecum.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3


def test_the_sign_in_database_is_noticed_when_it_or_its_log_changes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    before = seat.access_signature(data)
    _database(data / "mcp" / "access.sqlite3", 1)
    after = seat.access_signature(data)
    assert before != after
    store = MemoryStore()
    seat._uploaded.clear()
    assert seat.snapshot_database(data, store, *seat.ACCESS) == "db"
    assert seat.snapshot_database(data, store, *seat.ACCESS) == "unchanged"
    assert "mcp/access.sqlite3" in store.objects


def test_a_stored_database_that_cannot_be_restored_stops_the_seat_rather_than_starting_empty(tmp_path: Path, monkeypatch) -> None:
    """Feedback of 10 October: a failed restore started an empty copy with a new identity."""
    monkeypatch.setattr(seat, "RESTORE_PAUSE", 0.0)
    store = MemoryStore()
    store.objects["db/vademecum.sqlite3"] = b"not a database, a torn upload"
    try:
        seat.restore_databases(tmp_path / "data", store)
    except seat.RestoreFailed:
        pass
    else:
        raise AssertionError("it must refuse")
    assert not (tmp_path / "data" / "vademecum.sqlite3").exists()


def test_parts_whose_checksum_does_not_match_are_not_restored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(seat, "PART_BYTES", 64 * 1024)
    monkeypatch.setattr(seat, "RESTORE_PAUSE", 0.0)
    store = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", 200)
    seat._uploaded.clear()
    seat.snapshot(data, store)
    part = next(key for key in store.objects if ".part-" in key)
    store.objects[part] = store.objects[part][:-10] + b"0123456789"  # one part from another save
    try:
        seat.restore_databases(tmp_path / "fresh", store)
    except seat.RestoreFailed:
        pass
    else:
        raise AssertionError("mismatched parts must not be restored")


def test_a_new_multipart_save_never_overwrites_the_parts_of_the_last(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(seat, "PART_BYTES", 64 * 1024)
    store = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", 200)
    seat._uploaded.clear()
    seat.snapshot(data, store)
    first = {key for key in store.objects if ".part-" in key}
    _database(data / "vademecum.sqlite3", 210)
    seat._uploaded.clear()
    seat.snapshot(data, store)
    second = {key for key in store.objects if ".part-" in key}
    assert first and second and not (first & second), "fresh names each time; the old parts went after"
    with sqlite3.connect(data / "vademecum.sqlite3") as db:
        expected = db.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    seat.restore_databases(tmp_path / "fresh", store)
    with sqlite3.connect(tmp_path / "fresh" / "vademecum.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == expected
