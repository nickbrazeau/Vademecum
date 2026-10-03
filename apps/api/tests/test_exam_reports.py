"""Exam reports (ADR 0020): a score report comes in as text, is read by the
Mac's own model connection into content areas with checked quotes, and the
Improvement Map carries them."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from fake_model import ScriptedTurns
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.model.reports import check_areas
from vademecum.model.schemas import REPORT_SCHEMA

REPORT = (
    "In-Training Examination 2026. Content area performance, percentile relative to PGY-2 peers.\n"
    "Infectious Disease: 38th percentile. Below the mean for your level.\n"
    "Cardiology: 71st percentile. Above the mean.\n"
    "Nephrology: 52nd percentile. At the mean.\n"
)
AREAS = {
    "areas": [
        {"topic": "Infectious Disease", "specialty": "infectious-disease", "standing": "below", "quote": "Infectious Disease: 38th percentile. Below the mean", "note": "Lowest area."},
        {"topic": "Cardiology", "specialty": "cardiology", "standing": "above", "quote": "Cardiology: 71st percentile. Above the mean", "note": ""},
        {"topic": "Nephrology", "specialty": "made-up", "standing": "at", "quote": "Nephrology: 52nd percentile", "note": ""},
        {"topic": "Invented area", "specialty": "", "standing": "below", "quote": "this sentence is not in the report", "note": ""},
    ]
}


def test_only_quoted_areas_survive_and_unknown_specialties_are_dropped() -> None:
    kept = check_areas(AREAS, REPORT, {"infectious-disease", "cardiology"})
    assert [a["topic"] for a in kept] == ["Infectious Disease", "Cardiology", "Nephrology"]
    assert kept[2]["specialty"] == "", "a specialty id the list does not have is not kept"
    assert kept[0]["standing"] == "below"


class ReportTurns(ScriptedTurns):
    """The build pipeline's scripted runner, taught one more exchange."""

    def __init__(self, reports: list[dict]) -> None:
        super().__init__(synthesis=[], evidence=[], assessment=[])
        self.reports = list(reports)
        self.prompts: list[str] = []

    def __call__(self):
        outer = self

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                if output_schema is REPORT_SCHEMA:
                    outer.prompts.append(prompt)
                    from vademecum.appserver.turns import TurnResult

                    return TurnResult(payload=outer.reports.pop(0), raw_chars=10, turn_id="t", duration_ms=1.0)
                raise AssertionError("only report turns here")

        return Runner()


@pytest.fixture()
def client(tmp_path: Path):
    turns = ReportTurns([AREAS])
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        # The owner's workspace turn factory is what the parser uses.
        app.state.turn_factory = turns
        for workspace in app.state.workspaces._open.values():  # noqa: SLF001 - the test's seam
            workspace.turn_factory = turns
        yield c, app, turns


def test_a_report_is_taken_in_read_and_drawn_on_the_map(client, tmp_path: Path) -> None:
    c, _app, turns = client

    uploaded = c.post("/api/improvement-map/reports", files={"file": ("ite-2026.txt", REPORT.encode(), "text/plain")})
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert body["status"] == "uploaded" and body["text_chars"] > 100
    assert "Reading it now" in body["note"]
    assert "/" not in body["display_name"] and "stored_name" not in body

    for _ in range(100):
        listed = c.get("/api/improvement-map/reports").json()
        if listed and listed[0]["status"] != "uploaded":
            break
        time.sleep(0.05)
    report = listed[0]
    assert report["status"] == "parsed", report
    assert [a["topic"] for a in report["areas"]] == ["Infectious Disease", "Cardiology", "Nephrology"]
    assert report["areas"][0]["specialty_id"] == "infectious-disease" and report["areas"][2]["specialty_id"] is None
    assert "===== BEGIN USER MATERIAL" in turns.prompts[0] and "infectious-disease:" in turns.prompts[0]

    areas = c.get("/api/improvement-map").json()["report_areas"]
    assert [(a["topic"], a["standing"]) for a in areas] == [("Infectious Disease", "below"), ("Cardiology", "above"), ("Nephrology", "at")]
    assert areas[0]["report"] == "ite-2026.txt"

    assert (tmp_path / "data" / "attachments" / "reports").is_dir()
    assert c.delete(f"/api/improvement-map/reports/{report['id']}").status_code == 204
    assert c.get("/api/improvement-map/reports").json() == []
    assert c.get("/api/improvement-map").json()["report_areas"] == []


def test_a_file_with_no_text_is_refused_and_host_mode_waits(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, model_provider="host", sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        empty = c.post("/api/improvement-map/reports", files={"file": ("blank.txt", b"   ", "text/plain")})
        assert empty.status_code in (409, 422)
        waiting = c.post("/api/improvement-map/reports", files={"file": ("ite.txt", REPORT.encode(), "text/plain")}).json()
        assert waiting["status"] == "uploaded" and "Waiting" in waiting["note"]
        again = c.post(f"/api/improvement-map/reports/{waiting['id']}/parse")
        assert again.status_code == 409 and again.json()["error"]["code"] == "needs_model"
