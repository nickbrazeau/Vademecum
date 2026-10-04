"""The Socratic tutor and the podcast generator (ADR 0025): a session run by the
Mac's own connection and one recorded by a chat host, gaps filed as flags; a
script written from pages and rendered on-device with an injected voice."""

from __future__ import annotations

import time
import wave
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.podcasts import check_script, render_lines
from vademecum.model.schemas import PODCAST_SCHEMA, SOCRATIC_SCHEMA
from vademecum.storage import encyclopedia as pages
from vademecum.storage import socratic as sessions
from vademecum.storage.maintenance import EXPORTED_TABLES
from vademecum.storage.sync import DOMI_OWNED, SYNCED_TABLES


class DialogueTurns:
    """A tutor that asks twice, then wraps up with an assessment; a podcast writer beside it."""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self):
        outer = self

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                from vademecum.appserver.turns import TurnResult

                outer.prompts.append(prompt)
                if output_schema is PODCAST_SCHEMA:
                    return TurnResult(
                        payload={
                            "title": "Lactate, two ways",
                            "lines": [{"speaker": "A", "text": "Welcome. Today, lactate in sepsis."}, {"speaker": "B", "text": "Why does it matter?"}, {"speaker": "A", "text": "Above 2 mmol/L marks hypoperfusion."}],
                            "takeaways": ["Lactate above 2 mmol/L marks hypoperfusion."],
                        },
                        raw_chars=10, turn_id="t", duration_ms=1.0,
                    )
                assert output_schema is SOCRATIC_SCHEMA
                outer.calls += 1
                empty = {"differential": "", "treatment": "", "knowledge_strengths": "", "knowledge_gaps": [], "summary": ""}
                if outer.calls == 1:
                    payload = {"acknowledgement": "", "question": "A 60-year-old presents with fever and hypotension. What is on your differential?", "probe": "differential", "done": False, "assessment": empty}
                elif outer.calls == 2:
                    payload = {"acknowledgement": "Sepsis is a sound first thought.", "question": "What would you give first, and why?", "probe": "treatment", "done": False, "assessment": empty}
                else:
                    payload = {
                        "acknowledgement": "Good: fluids first.", "question": "That is where we will stop today.", "probe": "wrap_up", "done": True,
                        "assessment": {"differential": "Reasoned from sepsis outward.", "treatment": "Fluids first, pressors named.", "knowledge_strengths": "Knew the lactate threshold.", "knowledge_gaps": ["Vasopressor thresholds", "Lactate clearance targets"], "summary": "Sound on the basics; thresholds to revisit."},
                    }
                return TurnResult(payload=payload, raw_chars=10, turn_id="t", duration_ms=1.0)

        return Runner()


def _page(connection, topic="sepsis lactate"):
    return pages.upsert_entry(connection, topic=topic, title="Lactate in sepsis", specialty_id=None, summary="Lactate above 2 mmol/L marks hypoperfusion.", sections=[], point_ids=[], points_hash_value="h")


def test_a_session_on_the_mac_asks_answers_assesses_and_files_the_gaps(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect

        turns = DialogueTurns()
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001
            workspace.turn_factory = turns
        assert c.post("/api/socratic", json={}).status_code == 409, "no page yet"
        connection = connect(app.state.database_path)
        try:
            _page(connection)
        finally:
            connection.close()
        overview = c.get("/api/socratic").json()
        assert overview["can_answer_here"] is True and "once per exchange" in overview["disclosure"]

        started = c.post("/api/socratic", json={}).json()
        session = started["session"]
        assert session["status"] == "open" and session["title"] == "Lactate in sepsis" and session["mode"] == "codex"

        opened = c.post(f"/api/socratic/{session['id']}/answer", json={"answer": ""}).json()["session"]
        assert opened["transcript"][-1]["role"] == "tutor" and "differential" in opened["transcript"][-1]["text"].lower()
        assert "THE DIALOGUE SO FAR" in turns.prompts[0] and "just beginning" in turns.prompts[0]

        second = c.post(f"/api/socratic/{session['id']}/answer", json={"answer": "Sepsis, then cardiogenic and haemorrhagic shock."}).json()["session"]
        assert [t["role"] for t in second["transcript"]] == ["tutor", "learner", "tutor"] and second["exchanges"] == 1
        assert "LEARNER: Sepsis" in turns.prompts[1]

        done = c.post(f"/api/socratic/{session['id']}/answer", json={"answer": "Thirty mL per kg of crystalloid, then noradrenaline."}).json()
        assert done["session"]["status"] == "done" and done["gaps_filed"] == 2
        assert done["session"]["assessment"]["knowledge_gaps"] == ["Vasopressor thresholds", "Lactate clearance targets"]
        flags = c.get("/api/flags", params={"status": "open"}).json()
        texts = {flag["text"]: flag["topic"] for flag in flags}
        assert texts["From a Socratic session: Vasopressor thresholds"] == "sepsis lactate"
        assert c.post(f"/api/socratic/{session['id']}/answer", json={"answer": "more"}).json()["note"] == "This session has ended."
        assert c.get("/api/socratic").json()["open"] is None and len(c.get("/api/socratic").json()["recent"]) == 1


def test_a_chat_host_records_the_dialogue_and_the_assessment(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, model_provider="host")
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect

        connection = connect(app.state.database_path)
        try:
            _page(connection)
        finally:
            connection.close()
        overview = c.get("/api/socratic").json()
        assert overview["can_answer_here"] is False and "ChatGPT or Claude" in overview["note"]
        session = c.post("/api/socratic", json={}).json()["session"]
        assert session["mode"] == "host"
        material = c.get(f"/api/socratic/{session['id']}/material").json()
        assert "Socratic tutor" in material["rules"] and "TITLE: Lactate in sepsis" in material["page"] and material["max_exchanges"] == 12
        assert c.post(f"/api/socratic/{session['id']}/answer", json={"answer": "x"}).status_code == 409
        turned = c.post(f"/api/socratic/{session['id']}/turn", json={"question": "What is on your differential?", "answer": "Sepsis.", "probe": "differential"}).json()
        assert turned["session"]["exchanges"] == 1 and turned["remaining"] == 11
        finished = c.post(
            f"/api/socratic/{session['id']}/finish",
            json={"assessment": {"differential": "Narrow.", "treatment": "", "knowledge_strengths": "", "knowledge_gaps": ["Shock physiology"], "summary": "Short session.", "score": 99}},
        ).json()
        assert finished["session"]["status"] == "done" and finished["gaps_filed"] == 1
        assert "score" not in finished["session"]["assessment"], "no field for a verdict beyond words"
        assert c.post(f"/api/socratic/{session['id']}/turn", json={"question": "q", "answer": "a"}).status_code == 409


def test_a_script_is_checked_and_rendered_line_by_line() -> None:
    checked = check_script({"title": " Lactate,  two ways ", "lines": [{"speaker": "A", "text": " Welcome. [p1] "}, {"speaker": "C", "text": "Hm [p2, r1]"}, {"speaker": "B", "text": "[p3]"}], "takeaways": ["One. [p1]", 2]})
    assert checked["title"] == "Lactate, two ways" and [l["text"] for l in checked["lines"]] == ["Welcome.", "Hm"] and checked["takeaways"] == ["One."]

    spoken: list[tuple[str, str]] = []

    def synth(text: str, voice: str, target: Path) -> None:
        spoken.append((voice, text))
        with wave.open(str(target), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(22050)
            out.writeframes(b"\x01\x00" * 22050)

    def encode(source: Path, target: Path) -> None:
        target.write_bytes(source.read_bytes())

    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "out" / "episode.m4a"
        size, seconds = render_lines([{"speaker": "A", "text": "Hello."}, {"speaker": "B", "text": "Hi."}], {"A": "Samantha", "B": "Daniel"}, target, synth=synth, encode=encode)
    assert spoken == [("Samantha", "Hello."), ("Daniel", "Hi.")]
    assert seconds == 3 and size > 4 * 22050, "two seconds of speech and two pauses, encoded"


def test_an_episode_is_written_from_pages_and_rendered_on_the_mac(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        from vademecum.db import connect

        turns = DialogueTurns()
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001
            workspace.turn_factory = turns
        connection = connect(app.state.database_path)
        try:
            page = _page(connection)
        finally:
            connection.close()
        listed = c.get("/api/podcasts").json()
        assert listed["episodes"] == [] and listed["can_write"] is True and "spoken on-device" in listed["disclosure"]
        assert c.post("/api/podcasts", json={"pick": "chosen", "entry_ids": []}).status_code == 409

        created = c.post("/api/podcasts", json={"pick": "today"})
        assert created.status_code == 202, created.text
        episode_id = created.json()["id"]
        for _ in range(100):
            episode = c.get(f"/api/podcasts/{episode_id}").json()
            if episode["status"] == "scripted":
                break
            time.sleep(0.05)
        assert episode["status"] == "scripted" and episode["title"] == "Lactate, two ways" and episode["words"] > 5
        assert episode["entry_ids"] == [page.id] and "THE PAGES (1)" in turns.prompts[-1]
        assert c.get(f"/api/podcasts/{episode_id}/audio").status_code == 409, "not rendered yet"

        def synth(text: str, voice: str, target: Path) -> None:
            with wave.open(str(target), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(22050)
                out.writeframes(b"\x01\x00" * 2205)

        app.state.podcast_synth = synth
        app.state.podcast_encode = lambda source, target: target.write_bytes(source.read_bytes())
        rendered = c.post(f"/api/podcasts/{episode_id}/render", json={"voice_a": "Nobody", "voice_b": ""}).json()
        assert rendered["status"] == "rendered" and rendered["has_audio"] and rendered["duration_seconds"] >= 1
        assert rendered["voices"] == {"A": "Samantha", "B": "Daniel"}, "an unknown voice falls back to the default"
        audio = c.get(f"/api/podcasts/{episode_id}/audio")
        assert audio.status_code == 200 and audio.headers["content-type"].startswith("audio/mp4") and len(audio.content) > 1000
        assert c.delete(f"/api/podcasts/{episode_id}").status_code == 204
        assert c.get("/api/podcasts").json()["episodes"] == []


def test_the_new_tables_sync_and_export() -> None:
    assert "socratic_sessions" in SYNCED_TABLES and "socratic_sessions" not in DOMI_OWNED and "socratic_sessions" in EXPORTED_TABLES
    assert "podcast_episodes" in SYNCED_TABLES and "podcast_episodes" in DOMI_OWNED and "podcast_episodes" in EXPORTED_TABLES
    assert sessions.MAX_EXCHANGES == 12
