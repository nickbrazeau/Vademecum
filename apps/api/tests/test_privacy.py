"""ADR 0002: the boundary as tests rather than as prose.

Nothing here asserts an intention. Each test either observes an artefact (a log
line, a response body, a socket call) or reads the source for a pattern that
must not be there.
"""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vademecum.config import find_repo_root

MARKER = "ZZQXMARKERZZ-particular-clinical-detail"

REPO_ROOT = find_repo_root()
assert REPO_ROOT is not None
API_SOURCE = REPO_ROOT / "apps" / "api" / "src" / "vademecum"


def _python_sources() -> list[Path]:
    return sorted(API_SOURCE.rglob("*.py"))


def test_free_text_never_reaches_the_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        created = client.post("/api/flags", json={"text": MARKER, "topic": MARKER})
        assert created.status_code == 201
        flag_id = created.json()["id"]
        client.get("/api/flags")
        client.get(f"/api/flags/{flag_id}")
        client.patch(f"/api/flags/{flag_id}", json={"text": MARKER + " edited"})
        client.get("/api/today")
        client.post("/api/export")
        client.delete(f"/api/flags/{flag_id}")

    assert MARKER not in caplog.text
    assert "http_request" in caplog.text, "requests should still be logged structurally"


def test_a_failed_request_logs_its_shape_but_not_its_content(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        response = client.post("/api/flags", json={"text": MARKER * 400})
    assert response.status_code == 422
    assert MARKER not in caplog.text


def test_a_validation_error_does_not_echo_the_submitted_text(
    client: TestClient,
) -> None:
    response = client.post("/api/flags", json={"text": MARKER * 400})
    assert response.status_code == 422
    assert MARKER not in response.text
    assert response.json()["error"]["fields"], "it should still say which field"


def test_no_response_carries_a_filesystem_path(client: TestClient, data_dir: Path) -> None:
    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    client.post(f"/api/piles/{pile['id']}/items", json={"title": "Lactate"})
    paths = [
        "/api/health",
        "/api/today",
        "/api/improvement-map",
        "/api/piles",
        "/api/flags",
    ]
    bodies = [client.get(path).text for path in paths]
    bodies.append(client.post("/api/export").text)
    bodies.append(client.post("/api/backup").text)
    bodies.append(client.get("/api/export").text)
    bodies.append(client.get("/api/backup").text)

    for body in bodies:
        assert str(data_dir) not in body
        assert str(data_dir.parent) not in body
        assert "/Users/" not in body


def test_the_backend_process_opens_no_socket_of_its_own(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This process makes no network request. Codex, on the other hand, does.

    The distinction matters and is easy to collapse. Account, sign-in and
    usage operations are handed to the local ``codex app-server`` child over a
    pipe, and Codex contacts OpenAI to carry them out; this test says nothing
    about that path and the ``client`` fixture has no bridge wired up. What it
    does establish is that no route in this application reaches a network
    itself -- which, with ``test_the_backend_imports_no_http_client``, is what
    "no egress from Vademecum's own code" is made of.
    """

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the backend opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    pile = client.post("/api/piles", json={"title": "Sepsis", "tier": "mid"}).json()
    client.post(f"/api/piles/{pile['id']}/items", json={"title": "Lactate"})
    client.post("/api/flags", json={"text": "Unsure about vasopressors"})
    for path in ("/api/health", "/api/today", "/api/improvement-map"):
        assert client.get(path).status_code == 200
    assert client.post("/api/export").status_code == 201


# Where the string "apiKey" is allowed to appear at all. The App Server's own
# schema offers API-key login, so the bridge has to be able to *recognise* the
# word in order to refuse it (AGENTS.md, boundary 4). Everywhere else it is a
# sign that something started using one.
# `turns.py` joins the list because every content turn now runs a fresh
# account/read preflight and refuses anything that is not a ChatGPT account --
# which means it has to be able to name the credential it is refusing.
CREDENTIAL_REFUSAL_FILES = {"protocol.py", "account.py", "turns.py"}

# NCBI's E-utilities courtesy key: a public database's rate-limit identifier,
# not a model credential. AGENTS.md names it as the only key the product may
# hold. It may be configured and sent to the one allowlisted host, nowhere else.
PUBLIC_DATABASE_KEY_FILES = {"http.py", "config.py"}


def test_the_backend_reaches_no_provider_directly() -> None:
    """The bridge talks to a local process, never to a provider endpoint.

    Codex holds the ChatGPT credential and makes the upstream call. Nothing in
    this backend has an endpoint, a key, or a reason to have either.
    """
    forbidden = (
        "openai.com",
        "anthropic",
        "openai_api_key",
        "authorization",
        "bearer ",
        "embedding",
    )
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8").lower()
        for needle in forbidden:
            if needle in text:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == [], offenders


def test_api_key_billing_appears_only_where_it_is_refused() -> None:
    """`apiKey` may be recognised. It may not be constructed or configured."""
    offenders: list[str] = []
    for path in _python_sources():
        text = path.read_text(encoding="utf-8").lower()
        if "apikey" not in text and "api_key" not in text:
            continue
        if path.name in PUBLIC_DATABASE_KEY_FILES and "ncbi" in text:
            continue
        if path.name not in CREDENTIAL_REFUSAL_FILES:
            offenders.append(path.name)
    assert offenders == [], offenders

    # And where it does appear, it is a value to compare against, never a
    # login this codebase can send.
    for name in CREDENTIAL_REFUSAL_FILES:
        source = next(path for path in _python_sources() if path.name == name)
        text = source.read_text(encoding="utf-8")
        assert '"type": "apiKey"' not in text
        assert "'type': 'apiKey'" not in text


# The two documents that describe the boundary in prose. A claim here that the
# code cannot support is the same defect as a wrong response body, and it is
# the one a person actually reads.
BOUNDARY_DOCUMENTS = (
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "adr" / "0002-local-first-boundary.md",
)


def test_the_api_description_does_not_claim_it_reaches_no_network(
    client: TestClient,
) -> None:
    """It once said "makes no outbound network requests". That is now false.

    Account, rate-limit and login operations go through Codex, which contacts
    OpenAI. The description has to state the claim this slice can actually
    keep: no learning content is transmitted.
    """
    description = client.get("/api/openapi.json").json()["info"]["description"].lower()
    assert "no outbound network request" not in description
    assert "nothing is sent" not in description
    assert "no learning content" in description
    assert "codex" in description


def test_no_boundary_document_claims_nothing_is_transmitted() -> None:
    """The prose has to describe the slice that exists, not the previous one.

    Build and Grade transmit content, and the literature watch opens a socket.
    A document that still says otherwise is the same defect as a wrong response
    body, and it is the one a person actually reads.
    """
    for path in BOUNDARY_DOCUMENTS:
        lowered = path.read_text(encoding="utf-8").lower()
        for line in lowered.splitlines():
            # "No network egress" may survive only as history, explicitly marked.
            if "no network egress" in line:
                assert any(
                    marker in line for marker in ("own code", "previous", "was true", "no longer")
                ), f"{path.name}: unqualified egress claim: {line}"
            if "no learning content is transmitted" in line:
                assert any(
                    marker in line for marker in ("previous", "was true", "no longer", "said")
                ), f"{path.name}: stale transmission claim: {line}"
            # The phrase may appear only where a document is REJECTING it.
            if "nothing is sent" in line:
                assert any(
                    marker in line
                    for marker in ("not acceptable", "never", "refus", "not an acceptable")
                ), f"{path.name}: says nothing is sent: {line}"


def test_the_boundary_documents_name_what_is_actually_sent() -> None:
    for path in BOUNDARY_DOCUMENTS:
        lowered = path.read_text(encoding="utf-8").lower()
        assert "build learning material" in lowered, path.name
        assert "grade" in lowered, path.name
        assert "no api key" in lowered, path.name


def test_the_readme_names_the_one_allowlisted_provider_host() -> None:
    lowered = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "eutils.ncbi.nlm.nih.gov" in lowered
    assert "pubmed" in lowered


def test_the_readme_never_promises_verification_it_cannot_deliver() -> None:
    lowered = (REPO_ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert "machine reviewed" in lowered
    for overclaim in (
        "clinically validated",
        "human-approved",
        "human approved",
    ):
        # The phrase may appear only in a sentence that DENIES it.
        for line in lowered.splitlines():
            if overclaim in line:
                assert "not" in line, f"README overclaims: {line}"


def test_the_interface_copy_states_the_narrow_claim_not_the_broad_one() -> None:
    """The plain-language details the Model page shows come from here."""
    from vademecum.appserver.account import DETAIL, SIGNED_IN_DETAIL

    assert "contacts OpenAI" in SIGNED_IN_DETAIL
    assert "this status check sends no study content" in SIGNED_IN_DETAIL.lower()
    assert "Build and Grade send only the content described in their previews" in SIGNED_IN_DETAIL
    assert "explicitly approve" in SIGNED_IN_DETAIL
    for category, detail in DETAIL.items():
        assert "nothing is sent" not in detail.lower(), category
        assert "nothing was sent" not in detail.lower(), category


def test_the_bridge_logs_no_payload_field() -> None:
    """Diagnostics accept a closed set of field names, and no payload field."""
    from vademecum.appserver.diagnostics import ALLOWED_FIELDS

    for name in ("params", "message", "email", "token", "code_verifier", "user_code", "url"):
        assert name not in ALLOWED_FIELDS


def test_the_backend_imports_no_http_client() -> None:
    forbidden = ("import requests", "import httpx", "import urllib.request", "import aiohttp")
    for path in _python_sources():
        text = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in text, f"{path.name} imports an HTTP client: {needle}"


def test_the_openapi_document_matches_the_documented_surface(client: TestClient) -> None:
    """Every route, pinned. A new one has to be added here deliberately."""
    document = client.get("/api/openapi.json").json()
    assert set(document["paths"]) == {
        "/api/health",
        "/api/piles",
        "/api/piles/{pile_id}",
        "/api/piles/{pile_id}/items",
        "/api/items/{item_id}",
        "/api/flags",
        "/api/flags/{flag_id}",
        "/api/today",
        "/api/improvement-map",
        "/api/improvement-map/topics/specialty",
        "/api/improvement-map/positions",
        "/api/export",
        "/api/backup",
        "/api/backup/{filename}/verify",
        # Source intake and the build flow.
        "/api/piles/{pile_id}/sources",
        "/api/sources/scan",
        "/api/sources/{source_id}",
        "/api/sources/{source_id}/segments",
        "/api/piles/{pile_id}/build/preview",
        "/api/piles/{pile_id}/build",
        "/api/piles/{pile_id}/build/status",
        "/api/piles/{pile_id}/build/cancel",
        # Host mode (ADR 0009): the model work waiting on the learner's ChatGPT.
        "/api/piles/{pile_id}/build/pending",
        "/api/piles/{pile_id}/build/submit",
        "/api/piles/{pile_id}/material",
        "/api/piles/{pile_id}/recheck",
        "/api/runs",
        "/api/points",
        "/api/points/{point_id}",
        # Tutor.
        "/api/tutor",
        "/api/tutor/next",
        "/api/tutor/advance",
        "/api/tutor/grade",
        "/api/tutor/grade/submit",
        "/api/tutor/reveal",
        "/api/tutor/self-assess",
        "/api/tutor/history",
        # The public-literature watch.
        "/api/literature/topics",
        "/api/literature/topics/{topic_id}",
        "/api/literature/check",
        "/api/literature/updates",
        "/api/literature/updates/{update_id}",
        "/api/literature/settings",
        "/api/literature/suggestions",
        # Connection state and ChatGPT sign-in.
        "/api/model/status",
        "/api/model/login",
        "/api/model/login/cancel",
        "/api/model/restart",
        # The learner's workspace as one thing (ADR 0010).
        "/api/workspace",
        # Pictures beside a source, schematics for a point (ADR 0013).
        "/api/sources/{source_id}/images",
        "/api/images/{image_id}",
        "/api/points/{point_id}/schematics",
        "/api/schematics/{schematic_id}",
        # Filing flags under topics on the Mac's model connection (ADR 0021).
        "/api/flags/file",
        # Exam reports for the Improvement Map (ADR 0020).
        "/api/improvement-map/reports",
        "/api/improvement-map/reports/{report_id}",
        "/api/improvement-map/reports/{report_id}/parse",
        # Builds on a timer (ADR 0018): a standing consent, read and set here.
        "/api/build/schedule",
        "/api/build/schedule/run",
        # The Case Series hub (ADR 0022): fixed public requests, a switch, a refresh.
        "/api/cases",
        "/api/cases/settings",
        "/api/cases/refresh",
        # The encyclopedia and the board bank (ADR 0023).
        "/api/encyclopedia",
        "/api/encyclopedia/page",
        "/api/encyclopedia/compile",
        "/api/encyclopedia/dissection",
        "/api/encyclopedia/dissection/stop",
        "/api/encyclopedia/{entry_id}",
        "/api/tutor/board",
        "/api/tutor/board/next",
        "/api/tutor/board/answer",
        "/api/tutor/board/advance",
        "/api/tutor/board/history",
        # Flashcards and preferences (ADR 0024): local, no model turn.
        "/api/flashcards",
        "/api/flashcards/next",
        "/api/flashcards/review",
        "/api/preferences",
        # The Socratic tutor and the podcast generator (ADR 0025).
        "/api/socratic",
        "/api/socratic/{session_id}",
        "/api/socratic/{session_id}/material",
        "/api/socratic/{session_id}/answer",
        "/api/socratic/{session_id}/turn",
        "/api/socratic/{session_id}/finish",
        "/api/socratic/{session_id}/abandon",
        "/api/podcasts",
        "/api/podcasts/voices",
        "/api/podcasts/{episode_id}",
        "/api/podcasts/{episode_id}/script",
        "/api/podcasts/{episode_id}/render",
        "/api/podcasts/{episode_id}/audio",
        "/api/podcasts/{episode_id}/listened",
        "/api/socratic/import",
        "/api/encyclopedia/{entry_id}/reveal",
        "/api/encyclopedia/deleted",
        "/api/construction",
        "/api/encyclopedia/deleted/restore",
        "/api/sources/folder-drop",
        "/api/model/last-seen",
        "/api/socratic/{session_id}/assess",
        # The feedback of 4 October (ADR 0026): local reads and writes, no model turn.
        "/api/tutor/scorecard",
        "/api/activity",
        "/api/activity/page",
        "/api/encyclopedia/{entry_id}/edit",
        "/api/cases/{entry_id}/acknowledge",
        "/api/improvement-map/strengths",
        # Two Vademecums that sync (ADR 0015): served only with a peer token.
        "/api/sync/status",
        "/api/sync/changes",
        "/api/sync/apply",
        "/api/sync/podcast-audio",
        "/api/sync/podcast-audio/{episode_id}",
        "/api/sync/relay/wait",
        "/api/sync/figures",
        "/api/sync/figures/{image_id}",
        "/api/sync/relay/{request_id}",
        "/api/sync/file/{kind}/{name}",
    }


def test_the_only_transmitting_routes_are_the_two_explicit_actions() -> None:
    """Grep-level check that nothing else can reach a turn runner.

    Four routes may start a model turn: the build starter, Tutor grading, the
    exam-report intake (ADR 0020), where uploading the report is the explicit
    act and the dashboard states the disclosure above the button, and filing
    flags (ADR 0021), where the press is the act and the map says what goes.
    Anything else acquiring a turn factory would be another way for content to
    leave without a disclosure in front of it.
    """
    users = []
    for path in _python_sources():
        if path.parent.name != "api":
            continue
        text = path.read_text(encoding="utf-8")
        if "get_turn_factory" in text or "turn_factory" in text:
            users.append(path.name)
    assert sorted(users) == [
        "deps.py",
        "routes_flags.py",
        # Writing a podcast script and answering the Socratic tutor (ADR 0025): a
        # disclosure sits beside each, and each sends only what it names.
        "routes_podcasts.py",
        "routes_reports.py",
        "routes_socratic.py",
        "routes_tutor.py",
    ], users
    # The build path reaches a turn through the BuildService, which is
    # constructed once in app.py and handed the same factory.
    starter = (API_SOURCE / "api" / "routes_sources.py").read_text(encoding="utf-8")
    assert "BuildService" in starter


def _named_surface(document: dict) -> set[str]:
    """Every path and every field name the API can return, lowercased.

    Descriptions are excluded on purpose: prose may say "no prompt is sent",
    and a test that cannot tell that apart from a `prompt` field is a test that
    punishes the honest sentence.
    """
    names = {path.lower() for path in document["paths"]}
    for schema in document.get("components", {}).get("schemas", {}).values():
        names.update(name.lower() for name in schema.get("properties", {}))
    return names


def test_no_route_can_return_an_account_identifier(client: TestClient) -> None:
    """There is no field for one, so a refactor cannot reintroduce it quietly."""
    named = _named_surface(client.get("/api/openapi.json").json())
    for identifier in ("email", "account_id", "accountid", "chatgpt_account", "user_id"):
        assert not any(identifier in name for name in named), identifier


def test_the_api_does_not_solicit_patient_identifiers(client: TestClient) -> None:
    """No field asks for one. The rule is stated; nothing here detects them."""
    document = json.dumps(client.get("/api/openapi.json").json()).lower()
    for identifier in ("patient_name", "date_of_birth", "dob", "mrn", "medical_record"):
        assert identifier not in document
