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
        self.puts: list[str] = []

    def list(self, prefix: str) -> list[tuple[str, int]]:
        return [(key, len(data)) for key, data in sorted(self.objects.items()) if key.startswith(prefix)]

    def get(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def put(self, key: str, data: bytes) -> bool:
        self.objects[key] = data
        self.puts.append(key)
        return True


def test_a_fresh_container_restores_the_databases_first_and_the_files_later(tmp_path: Path) -> None:
    store = MemoryStore()
    data = tmp_path / "data"
    assert seat.restore_databases(data, store) == "fresh"

    store.objects["db/vademecum.sqlite3"] = b"records"
    store.objects["mcp/access.sqlite3"] = b"access"
    store.objects["attachments/sources/abc.pdf"] = b"%PDF-"
    data2 = tmp_path / "data2"
    assert seat.restore_databases(data2, store) == "restored"
    assert (data2 / "vademecum.sqlite3").read_bytes() == b"records"
    assert (data2 / "mcp" / "access.sqlite3").read_bytes() == b"access"
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

    first = seat.snapshot(data, store)
    assert first == {"db": 2, "files": 1, "skipped": 0}
    assert sorted(store.objects) == ["attachments/sources/abc.pdf", "db/vademecum.sqlite3", "mcp/access.sqlite3"]
    copy = tmp_path / "copy.sqlite3"
    copy.write_bytes(store.objects["db/vademecum.sqlite3"])
    with sqlite3.connect(copy) as backup:
        assert backup.execute("SELECT x FROM t").fetchone() == (1,)

    second = seat.snapshot(data, store)
    assert second == {"db": 2, "files": 0, "skipped": 0}, "databases always, files only when new"


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
    assert env["VADEMECUM_SYNC_ROLE"] == "away"
    assert env["VADEMECUM_TENANCY"] == "single"
    assert env["VADEMECUM_MODEL_PROVIDER"] == "host"
    assert env["VADEMECUM_SOURCES_FOLDER_ENABLED"] == "false"
    assert seat.gateway_environment()["VADEMECUM_MCP_PORT"] == str(seat.MCP_PORT)
