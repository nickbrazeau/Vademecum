"""Filing flags under topics (ADR 0021): one turn, topics written, the owner's
own specialty calls untouched, host mode refusing."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from test_exam_reports import ReportTurns
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.schemas import FLAGS_SCHEMA


class FilingTurns(ReportTurns):
    def __init__(self, filings: list[dict]) -> None:
        super().__init__([])
        self.filings = list(filings)

    def __call__(self):
        outer = self

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                from vademecum.appserver.turns import TurnResult

                outer.prompts.append(prompt)
                if output_schema is FLAGS_SCHEMA:
                    return TurnResult(payload=outer.filings.pop(0), raw_chars=10, turn_id="t", duration_ms=1.0)
                raise AssertionError("unexpected turn")

        return Runner()


def test_unfiled_flags_get_topics_and_the_map_draws_them(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        a = c.post("/api/flags", json={"text": "Differences between HRS-AKI and ATN in cirrhosis"}).json()
        b = c.post("/api/flags", json={"text": "https://www.nejm.org/doi/full/10.1056/NEJMra2032085"}).json()
        kept = c.post("/api/flags", json={"text": "Beta-lactam allergy delabeling", "topic": "Beta-lactam allergy"}).json()
        turns = FilingTurns([
            {"flags": [
                {"id": a["id"], "topic": "Hepatorenal syndrome", "specialty": "nephrology"},
                {"id": b["id"], "topic": "unsorted link", "specialty": ""},
                {"id": "flg_nope", "topic": "Nothing", "specialty": ""},
            ]}
        ])
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001
            workspace.turn_factory = turns

        assert c.get("/api/improvement-map").json()["unfiled_flag_count"] == 2
        started = c.post("/api/flags/file")
        assert started.status_code == 202, started.text
        for _ in range(100):
            if c.get("/api/improvement-map").json()["unfiled_flag_count"] == 0:
                break
            time.sleep(0.05)
        flags = {f["id"]: f for f in c.get("/api/flags").json()}
        assert flags[a["id"]]["topic"] == "Hepatorenal syndrome"
        assert flags[b["id"]]["topic"] == "unsorted link"
        assert flags[kept["id"]]["topic"] == "Beta-lactam allergy", "an already filed flag is not sent"
        assert f"[{kept['id']}]" not in turns.prompts[0] and "===== BEGIN USER MATERIAL" in turns.prompts[0]
        topics = {t["topic"]: t for t in c.get("/api/improvement-map").json()["topics"]}
        assert topics["Hepatorenal syndrome"]["specialty"]["id"] == "nephrology"
        assert "unsorted link" not in topics, "a link nothing could be told about is not a place on the map"


def test_host_mode_refuses_to_file(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        refused = c.post("/api/flags/file")
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "needs_model"
