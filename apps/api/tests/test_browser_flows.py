"""Real Chrome workflows against an isolated app; no live model or PubMed calls.

Each desktop/narrow case starts an empty temporary database, uploads its own
source through the UI and genuinely builds, reveals, grades and advances. The
only doubles are the model turn runner and public-literature provider. Browser
traffic to any non-test origin is refused and recorded. This is Chrome at a
390px viewport, not a claim of on-device iPhone/Safari verification.

Build apps/web before running. Missing Chrome/Playwright or stale/missing dist
fails this suite explicitly rather than silently skipping its primary flows.
"""

from __future__ import annotations

import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from conftest import refusing_factory
from fake_model import FakeArticle, FakeProvider, ScriptedTurns
from vademecum.app import create_app
from vademecum.appserver.errors import BridgeTimeout
from vademecum.config import Settings, find_repo_root

REPO_ROOT = find_repo_root()
TOPIC = "retrieval practice"
QUOTE = (
    "Retrieval practice strengthens recall when learners attempt an answer "
    "before seeing feedback"
)
LECTURE = f"Synthetic browser-test lecture.\n\n{QUOTE}.\n"
ABSTRACT = (
    "Synthetic browser fixture, not medical guidance. "
    f"{QUOTE}. This controlled example exercises citation matching and the "
    "learning workflow without retrieving or publishing actual clinical content."
)
QUESTION_PROMPTS = (
    "What sequence does this synthetic lecture describe for retrieval practice?",
    "When should feedback be shown in the synthetic retrieval-practice example?",
)
SYNTHESIS = {
    "points": [{
        "claim": "Attempting recall before feedback is the sequence in this example.",
        "detail": "Synthetic browser-test learning point.",
        "topics": [TOPIC],
        "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
        "unclear": False,
        "questions": [{
            "prompt": prompt,
            "reference_answer": "Attempt an answer before seeing feedback.",
            "rubric": "Names both the attempt and the subsequent feedback.",
            "citations": [{"excerpt_id": "E1", "quote": QUOTE}],
        } for prompt in QUESTION_PROMPTS],
    }],
    "search_topics": [TOPIC],
}
EVIDENCE = {"relation": "supports", "quote": QUOTE, "reasoning": "In the synthetic abstract."}
ASSESSMENT = {"verdict": "sound", "problems": [], "notes": "Synthetic assessment."}
GRADE = {
    "outcome": "correct",
    "feedback": "You put the attempt before the feedback.",
    "strengths": "Both steps are in the expected order.",
    "missing_or_unsafe": "",
    "improved_answer": "Attempt an answer before seeing feedback.",
    "uncertainty": "Synthetic browser fixture only.",
}


@dataclass
class ServedApp:
    base: str
    turns: ScriptedTurns
    provider: FakeProvider
    data_dir: Path

    def rows(self, query: str) -> list[sqlite3.Row]:
        # Read-only verification of the test DB, never a seeding shortcut.
        uri = (self.data_dir / "vademecum.sqlite3").as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            return connection.execute(query).fetchall()


@pytest.fixture(scope="module")
def browser():
    assert REPO_ROOT is not None, "Vademecum repository root could not be found"
    web = REPO_ROOT / "apps" / "web"
    index = web / "dist" / "index.html"
    assert index.is_file(), "Run npm run build in apps/web before browser tests"
    inputs = [path for path in (web / "src").rglob("*")
              if ".test." not in path.name and ".spec." not in path.name
              and "__tests__" not in path.parts and "test" not in path.parts]
    inputs += list((web / "public").rglob("*"))
    inputs += [web / name for name in (
        "index.html", "package.json", "package-lock.json", "vite.config.ts",
        "tsconfig.json", "tsconfig.app.json",
    )]
    latest_input = max(path.stat().st_mtime_ns for path in inputs if path.is_file())
    assert index.stat().st_mtime_ns >= latest_input, (
        "The built frontend is stale; run npm run build before browser tests"
    )
    with sync_playwright() as driver:
        instance = driver.chromium.launch(channel="chrome", headless=True)
        yield instance
        instance.close()


@pytest.fixture()
def served(tmp_path):
    import uvicorn

    # Bind once and pass this socket to uvicorn, avoiding a port-selection race.
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    data_dir = tmp_path / "browser-data"
    settings = Settings(
        data_dir=data_dir, host="127.0.0.1", port=port, web_port=port,
        literature_enabled=True,
    )
    turns = ScriptedTurns(
        synthesis=[SYNTHESIS], evidence=[EVIDENCE], assessment=[ASSESSMENT]
    )
    turns.grading = [GRADE]
    provider = FakeProvider(articles=[
        FakeArticle(pmid="30012345", title="Synthetic retrieval-practice example", abstract=ABSTRACT)
    ])
    app = create_app(
        settings, transport_factory=refusing_factory(), provider_factory=lambda: provider,
    )
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning",
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=0.5) as reply:
                    if reply.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                time.sleep(0.05)
        else:
            pytest.fail("The isolated browser-test server did not answer /api/health")
        app.state.turn_factory = turns
        app.state.build_service._turn_factory = turns
        owner = Path.home() / "Library" / "Application Support" / "Vademecum"
        assert owner != data_dir and owner not in data_dir.parents
        assert app.state.database_path == data_dir / "vademecum.sqlite3"
        yield ServedApp(base, turns, provider, data_dir)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "The isolated browser-test server failed to stop"


@pytest.fixture(params=[(1280, 900), (390, 844)], ids=["desktop-chrome", "390px-chrome"])
def page(browser, served, request):
    width, height = request.param
    context = browser.new_context(viewport={"width": width, "height": height})
    blocked: list[str] = []
    errors: list[str] = []

    def local_only(route):
        if route.request.url.startswith(served.base + "/"):
            route.continue_()
        else:
            blocked.append(route.request.url)
            route.abort()

    context.route("**/*", local_only)
    tab = context.new_page()
    tab.set_default_timeout(15_000)
    tab.on("pageerror", lambda error: errors.append(str(error)))
    try:
        yield tab
        assert blocked == [], f"The browser attempted external requests: {blocked}"
        assert errors == [], f"Uncaught browser errors: {errors}"
    finally:
        context.close()


def _no_overflow(page) -> None:
    overflow = page.evaluate(
        "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"The layout scrolls sideways by {overflow}px"


def _api_read(page, path: str):
    return page.evaluate(
        "async path => { const r = await fetch(path, {cache:'no-store'}); "
        "if (!r.ok) throw new Error('read failed ' + r.status); return r.json(); }", path,
    )


def _response(page, path: str):
    return page.expect_response(lambda response: response.url.endswith(path)
                                and response.request.method == "POST")


def test_upload_build_tutor_and_literature_are_real_browser_flows(served, page):
    page.goto(served.base + "/sources", wait_until="networkidle")
    page.get_by_label("Title", exact=True).fill("Browser study pile")
    page.get_by_role("button", name="Create pile", exact=True).click()
    pile = page.get_by_role("button", name=re.compile(r"^Browser study pile"))
    expect(pile).to_be_visible()
    pile.click()
    page.get_by_label("Add files", exact=True).set_input_files({
        "name": "study-lecture.txt", "mimeType": "text/plain", "buffer": LECTURE.encode(),
    })
    with page.expect_response(lambda reply: reply.request.method == "POST"
                              and reply.url.endswith("/sources")) as uploaded:
        page.get_by_role("button", name="Add file", exact=True).click()
    assert uploaded.value.status == 201
    upload = uploaded.value.json()
    assert upload["accepted"] == 1 and upload["rejected"] == 0
    source = upload["results"][0]["source"]
    assert source["status"] == "extracted"
    assert source["char_count"] == len(LECTURE.strip())
    expect(page.locator(".source").get_by_text("study-lecture.txt", exact=True)).to_be_visible()
    expect(pile.locator(".muted.small")).to_have_text(
        "1 files · 0 notes · 0 points · 0 questions · "
        f"0 of {len(LECTURE.strip())} extracted-text characters processed"
    )
    assert served.turns.calls == [] and served.provider.queries == []

    # Preview is genuinely read-only; only the second explicit button builds.
    page.get_by_role("button", name="Build learning material", exact=True).click()
    send = page.get_by_role("button", name="Send this batch to the model", exact=True)
    expect(send).to_be_enabled()
    expect(page.locator(".excerpts")).to_contain_text(QUOTE)
    expect(page.locator(".excerpts")).to_contain_text("study-lecture.txt")
    expect(page.locator(".build")).to_contain_text("OpenAI")
    assert served.turns.calls == [] and served.provider.queries == []
    _no_overflow(page)
    send.click()
    expect(page.locator(".run-report")).to_contain_text("Finished", timeout=30_000)
    expect(page.locator(".run-report")).to_contain_text("2 questions")
    expect(pile.locator(".muted.small")).to_have_text(
        "1 files · 0 notes · 1 points · 2 questions · fully processed"
    )
    assert [call["kind"] for call in served.turns.calls] == [
        "synthesis", "evidence", "assessment", "assessment",
    ]
    assert served.provider.queries == [f'"{TOPIC}"']
    assert len(served.rows("SELECT id FROM tutor_questions")) == 2
    page.screenshot(path=str(served.data_dir.parent / "sources-built.png"), full_page=True)

    page.get_by_role("navigation", name="Sections").get_by_text("Tutor", exact=True).click()
    expect(page.get_by_role("heading", name="Question", exact=True)).to_be_visible()
    prompt = page.locator(".prompt")
    first = prompt.inner_text()
    assert first in QUESTION_PROMPTS
    first_state = _api_read(page, "/api/tutor/next")
    first_id = first_state["question"]["id"]
    assert first_state["cycle"]["total"] == 2
    assert "reference_answer" not in first_state["question"]
    calls_before_reveal = len(served.turns.calls)
    page.get_by_role("button", name="Show reference answer", exact=True).click()
    expect(page.get_by_role("heading", name="Reference answer", exact=True)).to_be_visible()
    assert len(served.turns.calls) == calls_before_reveal

    answer = page.get_by_role("textbox", name="Your answer", exact=True)
    answer.fill("Attempt an answer before seeing feedback.")
    with _response(page, "/api/tutor/grade") as graded:
        page.get_by_role("button", name="Grade with the model", exact=True).click()
    assert graded.value.status == 200
    assert graded.value.json()["attempt"]["graded_by"] == "model"
    expect(page.get_by_role("heading", name="Correct", exact=True)).to_be_visible()
    assert len(served.turns.prompts("grading")) == 1
    assert "Attempt an answer before seeing feedback." in served.turns.prompts("grading")[0]
    assert page.evaluate("key => localStorage.getItem(key)",
                         f"vademecum.draft.answer.{first_id}") is None
    _no_overflow(page)
    page.screenshot(path=str(served.data_dir.parent / "tutor-graded.png"), full_page=True)

    page.get_by_role("button", name="Next question", exact=True).click()
    expect(prompt).not_to_have_text(first)
    second = prompt.inner_text()
    second_state = _api_read(page, "/api/tutor/next")
    second_id = second_state["question"]["id"]
    assert second in QUESTION_PROMPTS and second_id != first_id
    assert second_state["cycle"]["cycle_number"] == first_state["cycle"]["cycle_number"]
    draft = "I am working through the order of the steps."
    answer.fill(draft)
    page.reload(wait_until="networkidle")
    expect(page.locator(".prompt")).to_have_text(second)
    expect(answer).to_have_value(draft)
    assert _api_read(page, "/api/tutor/next")["question"]["id"] == second_id

    # HTTP 200 with attempt:null is a refusal, not a successful grade.
    refused_answer = "My patient needs a decision; what should I do for them?"
    answer.fill(refused_answer)
    with _response(page, "/api/tutor/grade") as refusal:
        page.get_by_role("button", name="Grade with the model", exact=True).click()
    refused = refusal.value.json()
    assert refusal.value.status == 200 and refused["attempt"] is None
    assert refused["refused"] == "patient_specific"
    expect(page.get_by_role("alert")).to_contain_text("specific patient")
    expect(answer).to_have_value(refused_answer)
    assert len(served.turns.prompts("grading")) == 1
    assert len(_api_read(page, "/api/tutor/history")) == 1
    page.reload(wait_until="networkidle")
    expect(answer).to_have_value(refused_answer)

    # A dispatched timeout must retain the draft and offer labelled self-assessment.
    served.turns.grading = [BridgeTimeout()]
    answer.fill(draft)
    with _response(page, "/api/tutor/grade") as unavailable:
        page.get_by_role("button", name="Grade with the model", exact=True).click()
    assert unavailable.value.status == 503
    expect(page.get_by_role("heading", name="Judge it yourself instead")).to_be_visible()
    expect(page.get_by_role("heading", name="Reference answer", exact=True)).to_be_visible()
    expect(answer).to_have_value(draft)
    assert len(_api_read(page, "/api/tutor/history")) == 1
    _no_overflow(page)
    with _response(page, "/api/tutor/self-assess") as self_assessed:
        page.get_by_role("button", name="Partly", exact=True).click()
    recorded = self_assessed.value.json()["attempt"]
    assert recorded["graded_by"] == "self" and recorded["outcome"] == "self_assessed"
    expect(page.locator(".attempt-self")).to_contain_text("the model did not grade it")
    assert len(_api_read(page, "/api/tutor/history")) == 2
    page.get_by_role("button", name="Next question", exact=True).click()
    expect(page.locator(".prompt")).not_to_have_text(second)
    next_cycle = _api_read(page, "/api/tutor/next")
    assert next_cycle["cycle"]["cycle_number"] == first_state["cycle"]["cycle_number"] + 1
    assert next_cycle["question"]["id"] == first_id
    page.reload(wait_until="networkidle")
    expect(page.locator(".prompt")).to_have_text(first)

    # A watch is separately opted into; a manual check works while weekly is off.
    page.get_by_role("navigation", name="Sections").get_by_text("Today", exact=True).click()
    literature = page.locator("#literature-settings")
    expect(literature).to_be_visible()
    weekly = literature.get_by_role("checkbox")
    expect(weekly).not_to_be_checked()
    assert _api_read(page, "/api/literature/settings")["weekly_enabled"] is False
    suggested = literature.get_by_role("region", name="Suggested watch topics")
    expect(suggested).to_contain_text(f'Search words: "{TOPIC}"')
    with _response(page, "/api/literature/topics") as added_topic:
        suggested.get_by_role("button", name=f"Watch {TOPIC}", exact=True).click()
    assert added_topic.value.status == 201
    assert added_topic.value.json()["query"] == f'"{TOPIC}"'
    assert _api_read(page, "/api/literature/settings")["weekly_enabled"] is False
    previous_queries = len(served.provider.queries)
    with _response(page, "/api/literature/check") as checked:
        literature.get_by_role("button", name="Check for new literature now", exact=True).click()
    check_report = checked.value.json()
    assert checked.value.status == 200 and check_report["status"] == "ok"
    assert len(check_report["results"]) == 1
    assert check_report["results"][0]["status"] == "ok"
    assert len(served.provider.queries) == previous_queries + 1
    assert served.provider.queries[0] == f'"{TOPIC}"'
    assert all(query == f'"{TOPIC}"' for query in served.provider.queries[1:])
    expect(weekly).not_to_be_checked()
    expect(page.locator(".update").first).to_contain_text(TOPIC)
    expect(page.locator(".update").first.get_by_role("link")).to_have_attribute(
        "href", "https://pubmed.ncbi.nlm.nih.gov/30012345/",
    )
    with page.expect_response(lambda reply: reply.url.endswith("/literature/settings")
                              and reply.request.method == "PUT") as enabled:
        weekly.click()
    assert enabled.value.json()["weekly_enabled"] is True
    assert enabled.value.json()["running"] is True
    expect(weekly).to_be_checked()
    page.reload(wait_until="networkidle")
    expect(page.locator("#literature-settings").get_by_role("checkbox")).to_be_checked()
    with page.expect_response(lambda reply: reply.url.endswith("/literature/settings")
                              and reply.request.method == "PUT") as disabled:
        page.locator("#literature-settings").get_by_role("checkbox").click()
    assert disabled.value.json()["weekly_enabled"] is False
    assert disabled.value.json()["running"] is False
    expect(page.locator("#literature-settings").get_by_role("checkbox")).not_to_be_checked()
    _no_overflow(page)
    assert len(served.turns.prompts("grading")) == 2
    assert len(served.rows("SELECT id FROM sources")) == 1


def test_shell_alias_and_private_storage_policy(served, page):
    page.goto(served.base + "/piles", wait_until="networkidle")
    expect(page.get_by_role("heading", name="New pile", exact=True)).to_be_visible()
    navigation = page.get_by_role("navigation", name="Sections")
    for label in ("Today", "Tutor", "Sources", "Model"):
        expect(navigation.get_by_text(label, exact=True)).to_be_visible()
    expect(page.get_by_role("contentinfo")).to_contain_text("not a substitute for clinical judgment")
    _no_overflow(page)
    health = _api_read(page, "/api/health")
    assert health["status"] == "ok" and health["loopback_only"] is True
    cache_control = page.evaluate(
        "async () => (await fetch('/api/today', {cache:'no-store'})).headers.get('cache-control')"
    )
    assert "no-store" in cache_control
    keys = page.evaluate("() => Object.keys(localStorage)")
    assert all(key.startswith("vademecum.draft.") for key in keys)
    cached_api = page.evaluate("""async () => {
        const found = [];
        for (const name of await caches.keys()) {
            for (const req of await (await caches.open(name)).keys()) {
                if (new URL(req.url).pathname.startsWith('/api')) found.push(req.url);
            }
        }
        return found;
    }""")
    assert cached_api == []
    assert served.turns.calls == [] and served.provider.queries == []
