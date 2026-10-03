"""The Case Series hub (ADR 0022): fixed public requests reduced to entries,
teaching points kept only where they quote the public text, a switch that is
off until chosen, and a hub that fetches only on Domi."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.literature.cases import (
    CaseItem,
    FetchGroup,
    WordPressSeries,
    classify_nejm,
    fetch_nejm,
    fetch_wordpress,
    strip_html,
    subseries_of,
)
from vademecum.model.cases import check_synthesis
from vademecum.model.schemas import CASE_SCHEMA
from vademecum.storage import cases as store
from vademecum.storage.maintenance import EXPORTED_TABLES
from vademecum.storage.sync import DOMI_OWNED, SYNCED_TABLES


class FakeFetcher:
    """One scripted reply per call, in order; the paths asked for are recorded."""

    def __init__(self, replies: list[bytes]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, path: str, params: dict[str, str]) -> bytes:
        self.calls.append((path, dict(params)))
        return self.replies.pop(0)


def _pubmed_article(pmid: str, doi: str, title: str, authors: list[tuple[str, str]]) -> str:
    author_xml = "".join(
        f"<Author><LastName>{last}</LastName><ForeName>{fore}</ForeName></Author>" for fore, last in authors
    )
    return (
        f"<PubmedArticle><MedlineCitation><PMID>{pmid}</PMID><Article>"
        f"<Journal><Title>The New England journal of medicine</Title><ISOAbbreviation>N Engl J Med</ISOAbbreviation>"
        f"<JournalIssue><PubDate><Year>2026</Year><Month>Sep</Month><Day>24</Day></PubDate></JournalIssue></Journal>"
        f"<ArticleTitle>{title}</ArticleTitle><AuthorList>{author_xml}</AuthorList>"
        f"<ELocationID EIdType=\"doi\">{doi}</ELocationID>"
        f"<PublicationTypeList><PublicationType>Case Reports</PublicationType></PublicationTypeList>"
        f"</Article></MedlineCitation><PubmedData><ArticleIdList><ArticleId IdType=\"doi\">{doi}</ArticleId>"
        f"<ArticleId IdType=\"pubmed\">{pmid}</ArticleId></ArticleIdList></PubmedData></PubmedArticle>"
    )


def test_the_doi_prefix_names_the_series() -> None:
    assert classify_nejm("10.1056/NEJMcpc2603181") == "nejm_cpc"
    assert classify_nejm("10.1056/nejmcps2600385") == "nejm_cps"
    assert classify_nejm("10.1056/NEJMicm2607537") is None
    assert classify_nejm("10.1056/NEJMoa2601337") is None
    assert classify_nejm("") is None


def test_the_nejm_fetch_is_one_fixed_query_and_keeps_only_the_two_series() -> None:
    esearch = json.dumps({"esearchresult": {"idlist": ["1", "2", "3"]}}).encode()
    efetch = (
        "<PubmedArticleSet>"
        + _pubmed_article("1", "10.1056/NEJMcpc2603181", "Case 27-2026: A 4-Year-Old Boy with Fatigue, Imbalance, and Frequent Falls.", [("Alexy D", "Arauz Boudreau"), ("Hee Mang", "Yoon")])
        + _pubmed_article("2", "10.1056/NEJMcps2600385", "An Echo Unheard.", [("Roni", "Eichel")])
        + _pubmed_article("3", "10.1056/NEJMicm2607537", "Serotonin Syndrome.", [("Hiro", "Takefuji")])
        + "</PubmedArticleSet>"
    ).encode()
    fetcher = FakeFetcher([esearch, efetch])
    items = fetch_nejm(fetcher, max_results=50)
    assert [item.series for item in items] == ["nejm_cpc", "nejm_cps"]
    assert items[0].title == "Case 27-2026: A 4-Year-Old Boy with Fatigue, Imbalance, and Frequent Falls"
    assert items[0].url == "https://www.nejm.org/doi/full/10.1056/NEJMcpc2603181"
    assert items[0].credit == "Alexy D Arauz Boudreau, Hee Mang Yoon"
    assert items[0].published_on == "2026-09-24"
    assert items[0].text == "", "PubMed carries no abstract for these; nothing is invented"
    search_path, search_params = fetcher.calls[0]
    assert search_path.endswith("esearch.fcgi")
    assert search_params["term"] == '"N Engl J Med"[ta] AND "Case Reports"[pt]'
    assert search_params["retmax"] == "50"


def test_show_notes_are_reduced_to_text_and_the_strand_is_read_from_the_title() -> None:
    markup = (
        "<p>Transcript available via <a href='x'>YouTube</a></p><script>alert(1)</script>"
        "<p>Pearls:</p><ul><li>Check the anion gap &amp; the osmolar gap.</li><li>Think about toxic alcohols.</li></ul>"
        "<p>https://example.test/audio/episode.mp3</p>"
    )
    text = strip_html(markup)
    assert "alert" not in text and "<" not in text and ".mp3" not in text
    assert "Check the anion gap & the osmolar gap." in text
    assert text.splitlines()[0] == "Transcript available via YouTube"
    assert subseries_of("Episode 475 – Clinical Unknown Series with Dr. Alec Rezigh") == "Clinical Unknown"
    assert subseries_of("Episode 476 – Schema Series – Thrombocytopenia") == "Schema"
    assert subseries_of("#540 Hotcakes: Varenicline by mail") == "Hotcakes"
    assert subseries_of("#538: More Than a Gut Feeling – DGBIs") == ""


def test_the_wordpress_fetch_keeps_posts_that_point_back_at_the_publisher() -> None:
    posts = [
        {
            "id": 54242,
            "date": "2026-09-11T17:13:44",
            "link": "https://clinicalproblemsolving.com/2026/09/11/episode-475/",
            "title": {"rendered": "Episode 475 &#8211; Clinical Unknown Series"},
            "content": {"rendered": "<p>Alec challenges the team with a case of lung–back–brain syndrome.</p>"},
        },
        {"id": 1, "date": "2026-09-01", "link": "https://elsewhere.test/post", "title": {"rendered": "Not ours"}, "content": {"rendered": ""}},
        {"id": "nope", "date": "", "link": "https://clinicalproblemsolving.com/x/", "title": {"rendered": "Bad id"}, "content": {}},
        "not a post",
    ]
    fetcher = FakeFetcher([json.dumps(posts).encode()])
    items = fetch_wordpress(fetcher, WordPressSeries("cps", "clinicalproblemsolving.com", 8253, per_page=25))
    assert len(items) == 1
    item = items[0]
    assert item.series == "cps" and item.external_id == "54242"
    assert item.title == "Episode 475 – Clinical Unknown Series"
    assert item.subseries == "Clinical Unknown"
    assert item.published_on == "2026-09-11"
    assert item.text == "Alec challenges the team with a case of lung–back–brain syndrome."
    path, params = fetcher.calls[0]
    assert path == "/wp-json/wp/v2/posts"
    assert params == {"per_page": "25", "categories": "8253", "_fields": "id,date,link,title,content"}


def test_entries_are_recorded_once_and_listed_newest_first(connection) -> None:
    items = [
        CaseItem("cps", "1", "Episode 1 – Schema", "https://clinicalproblemsolving.com/1/", "2026-09-01", text="Anion gap first.").as_dict(),
        CaseItem("curbsiders", "9", "#9 Hotcakes", "https://thecurbsiders.com/9", "2026-09-20", text="Statins.").as_dict(),
        CaseItem("nejm_cpc", "10.1056/NEJMcpc1", "Case 1-2026", "https://www.nejm.org/doi/full/10.1056/NEJMcpc1", "2026-09-10", credit="A Author").as_dict(),
        CaseItem("unknown", "x", "Nope", "https://x.test/", None).as_dict(),
        CaseItem("cps", "2", "Insecure link", "http://clinicalproblemsolving.com/2/", None).as_dict(),
    ]
    assert store.record_items(connection, items) == 3
    assert store.record_items(connection, items) == 0, "the same case is not a new row on the next fetch"
    listed = store.list_entries(connection)
    assert [entry.external_id for entry in listed] == ["9", "10.1056/NEJMcpc1", "1"]
    assert all(entry.status == "new" for entry in listed)
    assert listed[1].credit == "A Author"
    assert "text" not in listed[0].as_dict(), "the publisher's text never leaves the table through the API"
    assert listed[0].as_dict()["series_short"] == "The Curbsiders"
    assert [entry.external_id for entry in store.list_entries(connection, series="cps")] == ["1"]
    assert [entry.external_id for entry in store.list_entries(connection, q="hotcakes")] == ["9"]
    assert store.list_entries(connection, q="%") == []
    assert store.counts(connection) == {"total": 3, "pending": 3, "by_series": {"nejm_cpc": 1, "nejm_cps": 0, "cps": 1, "curbsiders": 1}}


def test_settings_are_off_until_chosen_and_bounded(connection) -> None:
    assert store.get_settings(connection) == {
        "enabled": False,
        "interval_hours": 6.0,
        "series": {"nejm_cpc": True, "nejm_cps": True, "cps": True, "curbsiders": True},
    }
    on = store.set_settings(connection, enabled=True, interval_hours=0.1, series={"cps": False, "bogus": True})
    assert on["enabled"] is True and on["interval_hours"] == 1.0
    assert on["series"]["cps"] is False and "bogus" not in on["series"]


TEXT = (
    "Pearls: Check the anion gap and the osmolar gap before anything else. Think about toxic "
    "alcohols when the osmolar gap is wide. Featuring Paul Williams and Matthew Watto."
)


def test_only_quoted_points_and_named_people_survive() -> None:
    payload = {
        "one_liner": "A wide osmolar gap.",
        "teaching_points": [
            {"point": "Measure both gaps early.", "quote": "Check the anion gap and the osmolar gap"},
            {"point": "Fomepizole is first-line.", "quote": "fomepizole is given first"},
            {"point": "", "quote": "Check the anion gap"},
        ],
        "think_first": ["What widens an osmolar gap?", " ", "Which alcohols?"],
        "specialty": "nephrology",
        "credit": "Paul Williams, Matthew Watto, Somebody Else",
    }
    kept = check_synthesis(payload, text=TEXT, specialty_ids={"nephrology"})
    assert [p["point"] for p in kept["points"]] == ["Measure both gaps early."]
    assert kept["think_first"] == ["What widens an osmolar gap?", "Which alcohols?"]
    assert kept["specialty_id"] == "nephrology"
    assert kept["credit"] == "Paul Williams, Matthew Watto"
    title_only = check_synthesis({**payload, "specialty": "made-up"}, text="", specialty_ids={"nephrology"})
    assert title_only["points"] == [], "a title alone supports no teaching point"
    assert title_only["specialty_id"] is None and title_only["credit"] == ""


class CaseTurns:
    """A runner that answers every case the same way, so a refresh racing a scheduled one cannot run out of script."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self):
        outer = self

        class Runner:
            async def run(self, *, instructions, developer_instructions, prompt, output_schema, max_output_chars=200_000):
                from vademecum.appserver.turns import TurnResult

                assert output_schema is CASE_SCHEMA
                outer.prompts.append(prompt)
                return TurnResult(
                    payload={
                        "one_liner": "Toxic alcohol ingestion with a wide osmolar gap.",
                        "teaching_points": [
                            {"point": "Measure both gaps early.", "quote": "Check the anion gap and the osmolar gap"},
                            {"point": "Invented.", "quote": "nowhere in the notes"},
                        ],
                        "think_first": ["What widens an osmolar gap?"],
                        "specialty": "nephrology",
                        "credit": "Paul Williams",
                    },
                    raw_chars=10,
                    turn_id="t",
                    duration_ms=1.0,
                )

        return Runner()


def _groups() -> list[FetchGroup]:
    def podcasts() -> list[CaseItem]:
        return [
            CaseItem("curbsiders", "540", "#540 Hotcakes: Toxic alcohols", "https://thecurbsiders.com/540", "2026-09-28", text=TEXT, subseries="Hotcakes"),
        ]

    def nejm() -> list[CaseItem]:
        return [
            CaseItem("nejm_cpc", "10.1056/NEJMcpc1", "Case 27-2026: A 4-Year-Old Boy with Falls", "https://www.nejm.org/doi/full/10.1056/NEJMcpc1", "2026-09-24", credit="A Author"),
            CaseItem("nejm_cps", "10.1056/NEJMcps1", "An Echo Unheard", "https://www.nejm.org/doi/full/10.1056/NEJMcps1", "2026-10-01", credit="B Author"),
        ]

    return [
        FetchGroup("curbsiders", frozenset({"curbsiders"}), podcasts),
        FetchGroup("nejm", frozenset({"nejm_cpc", "nejm_cps"}), nejm),
    ]


def _wait_for(client: TestClient, predicate, *, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = client.get("/api/cases").json()
        if predicate(payload):
            return payload
        time.sleep(0.05)
    raise AssertionError("the hub did not get there in time")


def test_a_refresh_gathers_the_series_and_writes_checked_notes_on_the_mac(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False)
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        hub = app.state.case_hub
        hub._groups = _groups()  # noqa: SLF001 - the seam for the fetches
        turns = CaseTurns()
        hub._turn_factory = turns  # noqa: SLF001

        before = c.get("/api/cases/settings").json()
        assert before["enabled"] is False and before["fetches_here"] is True and before["can_synthesise"] is True
        assert "nothing of yours" in before["disclosure"] and "work of its authors" in before["credit"]
        assert c.get("/api/cases").json()["entries"] == []

        started = c.post("/api/cases/refresh")
        assert started.status_code == 202, started.text
        done = _wait_for(c, lambda p: p["counts"]["total"] == 3 and p["counts"]["pending"] == 0)
        by_series = {entry["series"]: entry for entry in done["entries"]}
        assert [entry["external_id"] for entry in done["entries"]] == ["10.1056/NEJMcps1", "540", "10.1056/NEJMcpc1"], "newest first"

        podcast = by_series["curbsiders"]
        assert podcast["status"] == "synthesised"
        assert [p["point"] for p in podcast["points"]] == ["Measure both gaps early."], "the invented point was dropped"
        assert podcast["think_first"] == ["What widens an osmolar gap?"]
        assert podcast["specialty_id"] == "nephrology"
        assert podcast["credit"] == "Paul Williams", "named in the notes, so credited"
        assert podcast["series_short"] == "The Curbsiders" and podcast["subseries"] == "Hotcakes"
        assert podcast["snippet"].startswith("Pearls:") and "text" not in podcast

        article = by_series["nejm_cpc"]
        assert article["points"] == [], "a title alone yields prompts, never points"
        assert article["think_first"] == ["What widens an osmolar gap?"]
        assert article["credit"] == "A Author", "the publisher's authors are kept; the model cannot overwrite them"
        assert article["url"].startswith("https://www.nejm.org/doi/full/")
        assert len(turns.prompts) == 3
        assert "the title is all the publisher offers" in next(p for p in turns.prompts if "An Echo Unheard" in p)

        after = c.get("/api/cases/settings").json()
        assert after["last_refresh"]["reason"] == "requested"
        assert after["last_refresh"]["fetched"] == {"curbsiders": {"new": 1, "error": ""}, "nejm": {"new": 2, "error": ""}}
        assert after["last_refresh"]["synthesised"] == 3 and after["running"] is False

        filtered = c.get("/api/cases", params={"series": "nejm_cps"}).json()["entries"]
        assert [entry["title"] for entry in filtered] == ["An Echo Unheard"]
        assert c.get("/api/cases", params={"q": "osmolar"}).json()["counts"]["total"] == 3
        assert c.get("/api/cases", params={"series": "nope"}).status_code == 409

        # Switch it on: the loop wakes, refreshes again, and finds nothing new.
        on = c.put("/api/cases/settings", json={"enabled": True, "interval_hours": 2, "series": {"nejm_cps": False}}).json()
        assert on["enabled"] is True and on["interval_hours"] == 2.0 and on["series"]["nejm_cps"] is False
        again = c.post("/api/cases/refresh")
        assert again.status_code in (202, 409)
        settled = _wait_for(c, lambda p: p["counts"]["total"] == 3)
        assert settled["counts"]["pending"] == 0


def test_foris_shows_the_hub_but_does_not_fetch(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, model_provider="host", sync_role="foris")
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        described = c.get("/api/cases/settings").json()
        assert described["fetches_here"] is False and described["can_synthesise"] is False
        assert "Domi gathered" in described["note"]
        refused = c.post("/api/cases/refresh")
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_here"
        assert c.get("/api/cases").json()["entries"] == []


def test_host_mode_fetches_but_the_notes_wait(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, model_provider="host")
    app = create_app(settings, transport_factory=refusing_factory())
    with TestClient(app, base_url=LOCAL_ORIGIN) as c:
        hub = app.state.case_hub
        hub._groups = _groups()  # noqa: SLF001
        described = c.get("/api/cases/settings").json()
        assert described["fetches_here"] is True and described["can_synthesise"] is False
        assert "codex or claude mode" in described["note"]
        assert c.post("/api/cases/refresh").status_code == 202
        gathered = _wait_for(c, lambda p: p["counts"]["total"] == 3)
        assert all(entry["status"] == "new" for entry in gathered["entries"])


def test_case_entries_sync_from_domi_and_export() -> None:
    assert "case_entries" in SYNCED_TABLES and "case_entries" in DOMI_OWNED
    assert "case_entries" in EXPORTED_TABLES
