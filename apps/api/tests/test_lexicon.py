"""Medical pronunciations for the podcast (feedback of 10 October): lexicon words are said
from their own sounds; the rest of the line is left to espeak; the owner can add words."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model import lexicon


class FakeTokenizer:
    """Stands in for Kokoro's: brackets what espeak would be asked to say."""

    def phonemize(self, text: str, lang: str = "en-us") -> str:
        return f"[{text.strip()}]"

    def known(self, sounds: str) -> str:
        return sounds


def test_a_lexicon_word_is_said_from_its_own_sounds_and_the_rest_from_espeak() -> None:
    said = lexicon.spliced(FakeTokenizer(), "A positive Chvostek sign, then Sjögren.", "en-us")
    assert said == "[A positive] vˈɑːstɛk [sign, then] ʃˈoʊɡɹən [.]"
    assert lexicon.spliced(FakeTokenizer(), "Nothing to fix here.", "en-us") is None, "untouched lines are spoken as before"
    assert lexicon.spliced(FakeTokenizer(), "the CABG went well", "en-us") == "[the] kˈæbɪdʒ [went well]"


def test_the_owners_words_come_from_their_file_as_spelling_or_sounds(tmp_path: Path) -> None:
    lexicon.save_owner_entries(tmp_path, {"lisinopril": "lye SIN oh pril", "Kayexalate": "/keɪˈɛksəleɪt/"})
    owner = lexicon.owner_entries(tmp_path)
    assert owner == {"Kayexalate": "/keɪˈɛksəleɪt/", "lisinopril": "lye SIN oh pril"}
    said = lexicon.spliced(FakeTokenizer(), "Give Kayexalate, not lisinopril.", "en-us", owner)
    assert "keɪˈɛksəleɪt" in said and "[lyeSINohpril]" in said


def test_pronunciations_over_the_api(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    with TestClient(create_app(settings, transport_factory=refusing_factory()), base_url=LOCAL_ORIGIN) as client:
        listed = client.get("/api/podcasts/pronunciations").json()
        assert "Sjögren" in listed["built_in"] and listed["yours"] == {}
        saved = client.put("/api/podcasts/pronunciations", json={"entries": {"lisinopril": " lye SIN oh  pril "}}).json()
        assert saved["yours"] == {"lisinopril": "lye SIN oh pril"}
        refused = client.post("/api/podcasts/pronunciations/hear", json={"word": "lisinopril"})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "no_voices"
