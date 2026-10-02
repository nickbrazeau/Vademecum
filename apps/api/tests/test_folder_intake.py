"""The learner's source folder (ADR 0012): tier folders are ratings, subfolders
are piles, files become sources, scanning is idempotent, and nothing is ever
deleted by a scan."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import ConfigError, Settings, find_repo_root
from vademecum.ingest import folder as intake
from vademecum.ingest.limits import MAX_UPLOAD_BYTES

LECTURE = b"Septic shock: serial lactate measurement guides resuscitation in septic shock.\n"
OLD = time.time() - 60


def touch(path: Path, payload: bytes, *, mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    return tmp_path / "Vademecum"


@pytest.fixture()
def piles(folder: Path) -> Path:
    return folder / "piles"


@pytest.fixture()
def folder_settings(tmp_path: Path, folder: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_dir=folder)


@pytest.fixture()
def with_folder(folder_settings: Settings):
    app = create_app(folder_settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        yield client


def titles(client: TestClient) -> dict[str, dict]:
    return {pile["title"]: pile for pile in client.get("/api/piles").json()}


def test_the_folder_defaults_to_documents_and_never_the_checkout(tmp_path: Path) -> None:
    default = Settings(data_dir=tmp_path / "d").resolve_sources_dir()
    assert default is not None and default.parts[-2:] == ("Documents", "Vademecum")
    repo = find_repo_root()
    assert repo is not None
    with pytest.raises(ConfigError):
        Settings(data_dir=tmp_path / "d", sources_dir=repo / "apps").resolve_sources_dir()
    assert Settings(data_dir=tmp_path / "d", sources_folder_enabled=False).resolve_sources_dir() is None
    assert Settings(data_dir=tmp_path / "d", tenancy="multi", model_provider="host").resolve_sources_dir() is None


def test_tier_folder_names_are_forgiving() -> None:
    for name in ("highconfidence", "High Confidence", "high-confidence", "HIGH"):
        assert intake.tier_of(name) == "high", name
    for name in ("mediumconfidence", "Medium confidence", "mid", "Mid confidence"):
        assert intake.tier_of(name) == "mid", name
    assert intake.tier_of("lowconfidence") == "low"
    assert intake.tier_of("Sepsis") is None


def test_startup_scaffolds_the_layout_and_scans(folder_settings: Settings, folder: Path, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    app = create_app(folder_settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        assert app.state.sources_folder == folder
        for tier in ("highconfidence", "mediumconfidence", "lowconfidence"):
            assert (piles / tier).is_dir(), tier
        readme = (folder / "README.txt").read_text(encoding="utf-8")
        assert "piles/highconfidence" in readme and "patient" in readme
        assert [pile["title"] for pile in client.get("/api/piles").json()] == ["Sepsis"]


def test_tier_folders_rate_their_piles(with_folder: TestClient, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    touch(piles / "mediumconfidence" / "ICU handbook" / "chapter.md", b"# Chapter\n\nText.\n")
    touch(piles / "lowconfidence" / "Old notes" / "scribbles.txt", b"Half remembered.\n")
    touch(piles / "highconfidence" / "Sepsis" / ".DS_Store", b"junk")
    touch(piles / "mediumconfidence" / "loose.txt", b"A loose file in the tier folder.\n")
    touch(piles / "stray.txt", b"A file directly under piles.\n")
    touch(piles / "Cardiology" / "ecg.txt", b"A pile folder in the wrong place.\n")

    report = with_folder.post("/api/sources/scan").json()
    created = {entry["pile"]: entry["confidence"] for entry in report["piles_created"]}
    assert created == {
        "Sepsis": "high",
        "ICU handbook": "mid",
        "Old notes": "low",
        "Medium confidence": "mid",
        "Unsorted": "mid",
        "Cardiology": "mid",
    }
    assert len(report["stored"]) == 6
    assert report["rejected"] == []
    assert "/" not in "".join(entry["filename"] for entry in report["stored"])

    found = titles(with_folder)
    assert found["Sepsis"]["tier"] == "high" and found["Sepsis"]["source_count"] == 1
    assert found["Old notes"]["tier"] == "low"
    sources = with_folder.get(f"/api/piles/{found['Sepsis']['id']}/sources").json()
    assert sources[0]["display_name"] == "lecture.txt"
    assert sources[0]["confidence"] == "high"
    assert sources[0]["status"] == "extracted"
    preview = with_folder.get(f"/api/piles/{found['Sepsis']['id']}/build/preview").json()
    assert preview["blocked_reason"] == "", "a folder file is buildable like an upload"


def test_moving_a_pile_between_tiers_changes_its_rating(with_folder: TestClient, piles: Path) -> None:
    path = touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    with_folder.post("/api/sources/scan")
    assert titles(with_folder)["Sepsis"]["tier"] == "high"
    target = piles / "lowconfidence" / "Sepsis" / "lecture.txt"
    target.parent.mkdir(parents=True)
    path.rename(target)
    path.parent.rmdir()
    report = with_folder.post("/api/sources/scan").json()
    assert report["retiered"] == [{"pile": "Sepsis", "confidence": "low"}]
    assert report["stored"] == [], "the same bytes in the same pile are not stored twice"
    assert titles(with_folder)["Sepsis"]["tier"] == "low"


def test_a_second_scan_changes_nothing(with_folder: TestClient, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    first = with_folder.post("/api/sources/scan").json()
    assert len(first["stored"]) == 1
    second = with_folder.post("/api/sources/scan").json()
    assert second["stored"] == [] and second["piles_created"] == [] and second["retiered"] == []
    assert second["already_present"] == 1


def test_the_same_file_in_two_piles_is_two_sources(with_folder: TestClient, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    touch(piles / "lowconfidence" / "ICU" / "lecture.txt", LECTURE)
    report = with_folder.post("/api/sources/scan").json()
    assert len(report["stored"]) == 2, "two piles may rate the same document differently"


def test_a_file_still_being_written_waits_for_the_next_scan(with_folder: TestClient, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "fresh.txt", LECTURE, mtime=time.time())
    report = with_folder.post("/api/sources/scan").json()
    assert report["waiting"] == 1 and report["stored"] == []


def test_unsupported_and_oversized_files_are_reported_with_advice(with_folder: TestClient, piles: Path) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "old-deck.ppt", b"\xd0\xcf\x11\xe0 binary deck")
    touch(piles / "highconfidence" / "Sepsis" / "huge.txt", b"x" * (MAX_UPLOAD_BYTES + 1))
    report = with_folder.post("/api/sources/scan").json()
    rejected = {entry["filename"]: entry["message"] for entry in report["rejected"]}
    assert "pptx" in rejected["old-deck.ppt"]
    assert "MB" in rejected["huge.txt"]
    assert report["stored"] == []


def test_a_scan_never_deletes(with_folder: TestClient, piles: Path) -> None:
    path = touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    with_folder.post("/api/sources/scan")
    path.unlink()
    with_folder.post("/api/sources/scan")
    assert titles(with_folder)["Sepsis"]["source_count"] == 1


def test_nothing_outside_piles_is_read(with_folder: TestClient, folder: Path) -> None:
    touch(folder / "exports" / "old-export.txt", b"Not material.\n")
    touch(folder / "note-at-root.txt", b"Not material either.\n")
    report = with_folder.post("/api/sources/scan").json()
    assert report["stored"] == [] and report["piles_created"] == []


def test_a_folder_chosen_while_running_is_used_at_the_next_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`setup folder` records a new location; a running API follows it."""
    from vademecum.config import write_setting

    settings_file = tmp_path / "settings.env"
    monkeypatch.setenv("VADEMECUM_SETTINGS_FILE", str(settings_file))
    first, second = tmp_path / "First", tmp_path / "Second"
    write_setting("SOURCES_DIR", str(first), path=settings_file)
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        assert app.state.sources_folder == first
        touch(second / "piles" / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
        write_setting("SOURCES_DIR", str(second), path=settings_file)
        report = client.post("/api/sources/scan").json()
        assert [entry["pile"] for entry in report["piles_created"]] == ["Sepsis"]
        assert app.state.sources_folder == second
        assert (second / "README.txt").exists(), "the new folder is laid out too"


def test_no_folder_in_the_hosted_mode(tmp_path: Path) -> None:
    from test_tenancy import A, TOKENS
    from vademecum.tenancy import StaticResolver

    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", tenancy="multi")
    app = create_app(settings, transport_factory=refusing_factory(), token_resolver=StaticResolver(TOKENS))
    with TestClient(app, base_url=LOCAL_ORIGIN) as client:
        response = client.post("/api/sources/scan", headers=A)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "not_in_this_mode"


def test_the_scan_report_carries_no_path(with_folder: TestClient, piles: Path, folder: Path, folder_settings: Settings) -> None:
    touch(piles / "highconfidence" / "Sepsis" / "lecture.txt", LECTURE)
    text = with_folder.post("/api/sources/scan").text
    assert str(folder) not in text
    assert str(folder_settings.resolve_data_dir()) not in text
    assert "/Users/" not in text
