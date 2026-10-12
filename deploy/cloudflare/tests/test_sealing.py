"""Backups sealed with the owner's own key (ADR 0036): what the bucket holds can be opened
only with that key, only as the object it was sealed as, and only whole."""

from __future__ import annotations

import base64
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import sealing  # noqa: E402

from test_seat import MemoryStore, seat  # noqa: E402

KEY = bytes(range(32))
OTHER = bytes(range(1, 33))
MiB = 1024 * 1024


@pytest.mark.parametrize("length", [0, 1, 4 * MiB, 4 * MiB + 1, 70 * MiB])
def test_a_round_trip_at_every_edge_and_the_size_is_exact(length: int) -> None:
    data = os.urandom(length) if length <= 4 * MiB + 1 else (b"x" * length)
    blob = sealing.seal(KEY, "db/vademecum.sqlite3", data)
    assert len(blob) == sealing.sealed_size(length), "the sealed size follows from the plain size"
    assert sealing.opened_size(len(blob)) == length
    assert blob.startswith(b"VDMC1")
    if length >= 64:
        assert data[:64] not in blob, "the plaintext is not in what the bucket holds"
    assert sealing.open_sealed(KEY, "db/vademecum.sqlite3", blob) == data


def test_a_length_no_seal_could_have_is_told_apart() -> None:
    for impossible in (0, 10, sealing.HEADER, sealing.HEADER + 15):
        assert sealing.opened_size(impossible) == -1


def test_one_flipped_byte_anywhere_refuses() -> None:
    blob = bytearray(sealing.seal(KEY, "k", os.urandom(9 * MiB)))
    for at in (0, 6, sealing.HEADER - 1, sealing.HEADER + 5, len(blob) // 2, len(blob) - 1):
        altered = bytearray(blob)
        altered[at] ^= 0x01
        with pytest.raises(sealing.SealError):
            sealing.open_sealed(KEY, "k", bytes(altered))


def test_a_chunk_dropped_reordered_or_cut_off_refuses() -> None:
    data = os.urandom(3 * sealing.CHUNK)
    blob = sealing.seal(KEY, "k", data)
    head, body = blob[: sealing.HEADER], blob[sealing.HEADER :]
    size = sealing.CHUNK + sealing.TAG
    chunks = [body[i * size : (i + 1) * size] for i in range(3)]
    for forged in (
        head + chunks[0] + chunks[2],  # the middle one dropped
        head + chunks[1] + chunks[0] + chunks[2],  # two swapped
        head + chunks[0] + chunks[1],  # the end cut off
        blob[:-1],  # one byte short
    ):
        with pytest.raises(sealing.SealError):
            sealing.open_sealed(KEY, "k", forged)


def test_another_key_or_another_name_refuses() -> None:
    blob = sealing.seal(KEY, "db/vademecum.sqlite3", b"records")
    with pytest.raises(sealing.SealError, match="another key"):
        sealing.open_sealed(OTHER, "db/vademecum.sqlite3", blob)
    with pytest.raises(sealing.SealError):
        sealing.open_sealed(KEY, "mcp/access.sqlite3", blob)  # renamed in the bucket


def test_the_key_is_read_from_its_secret_and_only_its_fingerprint_is_shown() -> None:
    text = base64.b64encode(KEY).decode()
    assert sealing.key_from_text(text) == KEY
    with pytest.raises(sealing.SealError):
        sealing.key_from_text(base64.b64encode(b"short").decode())
    assert sealing.fingerprint(KEY) == sealing.key_id(KEY).hex() and text not in sealing.fingerprint(KEY)


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        connection.execute("INSERT INTO t VALUES (?)", (value,))


def _files(data: Path) -> None:
    (data / "attachments" / "podcasts").mkdir(parents=True, exist_ok=True)
    (data / "attachments" / "podcasts" / "pod.m4a").write_bytes(b"audio" * 1000)
    (data / "attachments" / "figures").mkdir(parents=True, exist_ok=True)
    (data / "attachments" / "figures" / "img.png").write_bytes(b"png")


def test_plaintext_backups_are_sealed_on_the_first_round_then_refused(tmp_path: Path) -> None:
    seat._uploaded.clear()
    raw = MemoryStore()
    old = tmp_path / "old"
    _database(old / "vademecum.sqlite3", "before sealing")
    _database(old / "mcp" / "access.sqlite3", "sign-ins")
    _files(old)
    seat.snapshot(old, raw)  # the copy from before sealing: plaintext in the bucket
    assert raw.objects["db/vademecum.sqlite3"].startswith(b"SQLite format 3")

    store = sealing.SealedStore(raw, KEY)
    fresh = tmp_path / "fresh"
    assert seat.restore_databases(fresh, store) == "restored", "read once, before the marker"
    seat._uploaded.clear()
    result = seat.snapshot(fresh, store)
    seat.restore_files(fresh, store)
    seat.snapshot(fresh, store)
    assert result["plain"] >= 0
    assert all(sealing.is_sealed(blob) for blob in raw.objects.values()), "every object sealed"
    assert raw.objects[sealing.MARKER]
    status = (fresh / ".seat-status.json").read_text()
    assert '"plain": 0' in status and sealing.fingerprint(KEY) in status and "seal_ms" in status

    raw.objects["attachments/figures/img.png"] = b"png"  # someone puts plaintext back
    with pytest.raises(sealing.SealError, match="not sealed"):
        sealing.SealedStore(raw, KEY).get("attachments/figures/img.png")


def test_an_unchanged_file_is_not_sent_again(tmp_path: Path) -> None:
    seat._uploaded.clear()
    raw = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", "x")
    _files(data)
    store = sealing.SealedStore(raw, KEY)
    seat.snapshot(data, store)
    sent = len(raw.puts)
    seat.snapshot(data, store)
    assert len(raw.puts) == sent, "sealed sizes are compared exactly: nothing goes up twice"


def test_a_replaced_container_restores_byte_identical_records_and_files(tmp_path: Path, monkeypatch) -> None:
    seat._uploaded.clear()
    monkeypatch.setattr(seat, "PART_BYTES", 4096)  # the database goes up in parts
    raw = MemoryStore()
    first = tmp_path / "first"
    for index in range(300):
        _database(first / "vademecum.sqlite3", f"row {index} " * 20)
    _database(first / "mcp" / "access.sqlite3", "sign-ins")
    _files(first)
    seat.snapshot(first, sealing.SealedStore(raw, KEY))
    assert any(".part-" in name for name in raw.objects)

    second = tmp_path / "second"
    store = sealing.SealedStore(raw, KEY)
    assert seat.restore_databases(second, store) == "restored"
    seat.restore_files(second, store)
    with sqlite3.connect(second / "vademecum.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 300
    assert (second / "attachments" / "podcasts" / "pod.m4a").read_bytes() == b"audio" * 1000


def test_with_the_wrong_key_the_seat_refuses_rather_than_start_empty(tmp_path: Path) -> None:
    seat._uploaded.clear()
    raw = MemoryStore()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", "x")
    seat.snapshot(data, sealing.SealedStore(raw, KEY))
    with pytest.raises(seat.RestoreFailed, match="another key"):
        seat.restore_databases(tmp_path / "elsewhere", sealing.SealedStore(raw, OTHER))
    assert not (tmp_path / "elsewhere" / "vademecum.sqlite3").exists()


def test_the_relay_flag_stays_readable_as_a_number() -> None:
    """The Worker reads relay/wanted itself; the seat never seals it (only the API writes it)."""
    assert not any(prefix.startswith("relay") for prefix in seat.SEALED_PREFIXES)


def test_without_a_key_the_store_is_as_before(monkeypatch) -> None:
    monkeypatch.setenv("SEAT_STORE_URL", "https://example.workers.dev/__seat/")
    monkeypatch.setenv("SEAT_KEY", "k" * 32)
    monkeypatch.delenv("SEAT_BACKUP_KEY", raising=False)
    assert isinstance(seat.store_from_environment(), seat.WorkerStore)
    monkeypatch.setenv("SEAT_BACKUP_KEY", base64.b64encode(KEY).decode())
    assert isinstance(seat.store_from_environment(), sealing.SealedStore)


def test_a_whole_copy_left_from_before_parts_is_counted_as_superseded(tmp_path: Path, monkeypatch) -> None:
    seat._uploaded.clear()

    class AsTheWorker(MemoryStore):
        """Deletes only what the Worker allows: retired audio and figures, replaced parts and manifests."""

        def delete(self, key: str) -> bool:
            allowed = key.startswith(("attachments/podcasts/", "attachments/figures/")) or ".part-" in key or key.endswith(".manifest")
            return super().delete(key) if allowed else False

    raw = AsTheWorker()
    raw.objects["db/vademecum.sqlite3"] = b"SQLite format 3\x00 an old whole copy"
    monkeypatch.setattr(seat, "PART_BYTES", 4096)
    data = tmp_path / "data"
    for index in range(300):
        _database(data / "vademecum.sqlite3", f"row {index} " * 20)
    seat.snapshot(data, sealing.SealedStore(raw, KEY))
    status = sealing.SealedStore(raw, KEY).status(seat.SEALED_PREFIXES)
    assert status["plain"] == 0 and status["superseded"] == 1


def test_a_failed_listing_is_never_counted_as_an_empty_bucket(tmp_path: Path) -> None:
    """Live, 11 October: the edge refused the container's own listings during the sealing
    round, and the count said 0 objects. A failed listing is now said, and no marker written."""
    seat._uploaded.clear()

    class Refusing(MemoryStore):
        def entries(self, prefix: str):
            raise seat.StoreUnavailable(f"listing {prefix} answered 429")

    raw = Refusing()
    data = tmp_path / "data"
    _database(data / "vademecum.sqlite3", "x")
    seat.snapshot(data, sealing.SealedStore(raw, KEY))
    status = (data / ".seat-status.json").read_text()
    assert '"listing_failed": true' in status and '"objects"' not in status
    assert sealing.MARKER not in raw.objects


def test_the_store_tries_a_refused_call_again(monkeypatch) -> None:
    store = seat.WorkerStore("https://example.workers.dev/__seat", "k" * 32)
    answers = iter([(429, b""), (503, b""), (200, b'{"objects": []}')])
    monkeypatch.setattr(store, "_once", lambda *a, **k: next(answers))
    monkeypatch.setattr(seat, "STORE_PAUSE", 0)
    assert store.entries("db/") == []
