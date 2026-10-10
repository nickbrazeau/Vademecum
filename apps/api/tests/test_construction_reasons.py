"""Foundation's progress (feedback of 10 October): every file waiting or not yet built says why."""

from __future__ import annotations

from pathlib import Path

from vademecum.storage import construction


def _file(folder: Path, tier: str, pile: str, name: str) -> Path:
    path = folder / "piles" / tier / pile / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    return path


def test_each_waiting_file_says_why(connection, tmp_path: Path) -> None:
    from vademecum.storage import piles

    pile = piles.create_pile(connection, title="Sepsis", tier="mid")
    connection.execute(
        "INSERT INTO sources (id, pile_id, display_name, stored_name, media_type, byte_size, sha256, confidence, status,"
        " unit_kind, unit_count, char_count, extraction_hash, created_at, updated_at)"
        " VALUES ('src_1', ?, 'lecture.pdf', 'a.pdf', 'application/pdf', 1, 'abc', 'mid', 'needs_ocr', 'page', 1, 0, 'h', 'now', 'now')",
        (pile.id,),
    )
    connection.commit()
    folder = tmp_path / "folder"
    copy = _file(folder, "mediumconfidence", "Sepsis", "lecture copy.pdf")
    _file(folder, "mediumconfidence", "Sepsis", "slides.ppt")
    _file(folder, "mediumconfidence", "Sepsis", "new.pdf")
    _file(folder, "mediumconfidence", "Sepsis", "newer.pdf")
    construction.record_scan(connection, {"rejected": [{"filename": "slides.ppt", "pile": "Sepsis", "message": "Save it as .pptx."}]})
    found = construction.progress(connection, folder, cache={str(copy): [1, 1, "abc"]}, builder={"enabled": False})
    reasons = {item["filename"]: item["reason"] for item in found["folder"]["waiting"]}
    assert reasons["slides.ppt"] == "Turned away: Save it as .pptx."
    assert sorted(v for k, v in reasons.items() if k.startswith("new")) == ["Next to be read in.", "Waiting to be read in: 1 ahead of it."]
    assert "lecture copy.pdf" not in reasons, "a copy of what is already here is not waiting"
    assert found["folder"]["duplicates"][0]["reason"] == "Already here as lecture.pdf (the same contents)."
    assert found["sources"]["items"][0]["reason"].startswith("No readable text")
