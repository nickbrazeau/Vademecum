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


def fake_runner(calls: list[tuple[str, ...]]):
    def run(*args: str) -> int:
        calls.append(args)
        return 0

    return run


def test_restore_pulls_the_databases_and_the_files_only_when_there_are_no_records(tmp_path: Path) -> None:
    data = tmp_path / "data"
    calls: list[tuple[str, ...]] = []
    assert seat.restore(data, "r2:bucket", fake_runner(calls)) == "fresh"
    assert [c[0] for c in calls] == ["copy", "copy", "copy"]
    assert calls[0][1] == "r2:bucket/db" and calls[1][1] == "r2:bucket/mcp" and calls[2][1] == "r2:bucket/attachments"
    assert (data / "mcp").is_dir()

    (data / "vademecum.sqlite3").write_bytes(b"x")
    calls.clear()
    assert seat.restore(data, "r2:bucket", fake_runner(calls)) == "present"
    assert calls == []


def test_snapshot_copies_each_database_consistently_then_syncs(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "mcp").mkdir(parents=True)
    (data / "attachments" / "sources").mkdir(parents=True)
    with sqlite3.connect(data / "vademecum.sqlite3") as live:
        live.execute("CREATE TABLE t (x)")
        live.execute("INSERT INTO t VALUES (1)")
    with sqlite3.connect(data / "mcp" / "access.sqlite3") as live:
        live.execute("CREATE TABLE a (y)")
    calls: list[tuple[str, ...]] = []
    result = seat.snapshot(data, "r2:bucket", fake_runner(calls))
    assert result == {"db": 0, "mcp": 0, "attachments": 0}
    assert [(c[0], c[2]) for c in calls] == [
        ("sync", "r2:bucket/db"),
        ("sync", "r2:bucket/mcp"),
        ("sync", "r2:bucket/attachments"),
    ]
    copy = data / ".snapshot" / "db" / "vademecum.sqlite3"
    with sqlite3.connect(copy) as backup:
        assert backup.execute("SELECT x FROM t").fetchone() == (1,)
    assert (data / ".snapshot" / "mcp" / "access.sqlite3").exists()


def test_the_seat_refuses_to_start_without_its_secrets(monkeypatch) -> None:
    for name in ("VADEMECUM_MCP_PUBLIC_URL", "VADEMECUM_SYNC_ACCEPT_TOKEN", "VADEMECUM_MCP_PASSPHRASE"):
        monkeypatch.delenv(name, raising=False)
    assert seat.run() == 2


def test_the_api_on_the_seat_is_away_single_tenancy_host_mode(monkeypatch) -> None:
    monkeypatch.setenv("VADEMECUM_SYNC_ACCEPT_TOKEN", "t")
    env = seat.api_environment()
    assert env["VADEMECUM_SYNC_ROLE"] == "away"
    assert env["VADEMECUM_TENANCY"] == "single"
    assert env["VADEMECUM_MODEL_PROVIDER"] == "host"
    assert env["VADEMECUM_SOURCES_FOLDER_ENABLED"] == "false"
    assert seat.gateway_environment()["VADEMECUM_MCP_PORT"] == str(seat.MCP_PORT)
