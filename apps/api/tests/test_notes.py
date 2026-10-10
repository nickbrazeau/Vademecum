"""The owner's notes (ADR 0032, feedback of 10 October): notebooks and nested notes in
Markdown, shared by the Mac and the phone, mirrored to files, and a source when marked."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.storage import note_files
from vademecum.storage import notes as store


def test_a_tree_of_notebooks_and_notes_over_the_api(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    with TestClient(create_app(settings, transport_factory=refusing_factory()), base_url=LOCAL_ORIGIN) as client:
        book = client.post("/api/notes", json={"title": "Cardiology", "notebook": True}).json()
        hf = client.post("/api/notes", json={"title": "Heart failure", "parent_id": book["id"], "body_md": "## GDMT\\n\\nFour pillars."}).json()
        sub = client.post("/api/notes", json={"title": "SGLT2 inhibitors", "parent_id": hf["id"]}).json()
        listed = client.get("/api/notes").json()["notes"]
        assert {n["title"] for n in listed} == {"Cardiology", "Heart failure", "SGLT2 inhibitors"}
        assert "body_md" not in listed[0], "the tree comes without the text"
        assert client.get(f"/api/notes/{hf['id']}").json()["path"] == ["Cardiology", "Heart failure"]
        found = client.get("/api/notes/search", params={"q": "pillars"}).json()["notes"]
        assert [n["id"] for n in found] == [hf["id"]]
        loop = client.patch(f"/api/notes/{hf['id']}", json={"parent_id": sub["id"]})
        assert loop.status_code == 409, "a note cannot go inside itself"
        moved = client.patch(f"/api/notes/{sub['id']}", json={"parent_id": ""}).json()
        assert moved["parent_id"] is None
        assert client.delete(f"/api/notes/{book['id']}").json()["deleted"] == 2
        assert [n["title"] for n in client.get("/api/notes").json()["notes"]] == ["SGLT2 inhibitors"]


def test_notes_mirror_to_markdown_and_an_edit_in_the_file_comes_back(connection, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    book = store.create_note(connection, title="Renal", notebook=True)
    note = store.create_note(connection, title="Hyperkalaemia", parent_id=book.id, body_md="Calcium first.")
    assert note_files.sync_folder(connection, folder)["written"] == 1
    path = folder / "notes" / "Renal" / "Hyperkalaemia.md"
    assert path.read_text().startswith(f"---\nvademecum-note: {note.id}\n---\n# Hyperkalaemia\n\nCalcium first.")
    assert note_files.sync_folder(connection, folder) == {"written": 0, "read": 0}, "nothing changed, nothing written"
    path.write_text(path.read_text().replace("Calcium first.", "Calcium first, then insulin and glucose."))
    assert note_files.sync_folder(connection, folder)["read"] == 1
    assert "insulin" in store.get_note(connection, note.id).body_md
    store.delete_note(connection, note.id)
    note_files.sync_folder(connection, folder)
    assert not path.exists(), "a deleted note takes its file with it"


def test_a_note_marked_as_a_source_goes_where_the_builder_reads(connection, tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    note = store.create_note(connection, title="Sodium pearls", body_md="Correct no faster than 8 mmol/L a day.")
    note_files.sync_folder(connection, folder)
    as_source = folder / "piles" / "lowconfidence" / "My notes" / "Sodium pearls.md"
    assert not as_source.exists()
    store.update_note(connection, note.id, use_as_source=True)
    note_files.sync_folder(connection, folder)
    assert as_source.read_text() == "# Sodium pearls\n\nCorrect no faster than 8 mmol/L a day.\n"
    store.update_note(connection, note.id, use_as_source=False)
    note_files.sync_folder(connection, folder)
    assert not as_source.exists()


def test_notes_nest_only_so_deep(connection) -> None:
    parent = None
    for depth in range(store.MAX_DEPTH):
        parent = store.create_note(connection, title=f"Level {depth}", parent_id=parent).id
    with pytest.raises(ValueError):
        store.create_note(connection, title="Too deep", parent_id=parent)
