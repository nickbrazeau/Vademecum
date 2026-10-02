"""The narrow client and the PubMed parser.

No test here opens a socket: the client takes an injected connection factory and
the provider takes an injected fetcher. What is being checked is the refusals --
of a host, of a redirect, of an oversized body, of a query that is not a topic.
"""

from __future__ import annotations

from typing import Any

import pytest

import fake_pubmed
from vademecum.literature import pubmed
from vademecum.literature.http import (
    ALLOWED_HOSTS,
    MAX_BODY_BYTES,
    HttpsFetcher,
    ProviderError,
)
from vademecum.literature.pubmed import PubMedProvider, QueryError

HOST = pubmed.HOST


# -- a fake socket-free connection --------------------------------------------


class FakeResponse:
    def __init__(self, status: int, body: bytes, content_length: str | None = None):
        self.status = status
        self._body = body
        self._content_length = content_length

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def getheader(self, name: str, default: Any = None) -> Any:
        if name.lower() == "content-length":
            return self._content_length
        return default


class FakeConnection:
    """One scripted reply, or one exception, per attempt."""

    def __init__(self, script: list[Any], log: list[dict[str, Any]]):
        self._script = script
        self._log = log
        self.closed = False

    def request(self, method: str, url: str, *, headers: dict[str, str]) -> None:
        self._log.append({"method": method, "url": url, "headers": headers})

    def getresponse(self) -> FakeResponse:
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class Clock:
    """A monotonic clock that only moves when a fake sleep moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def build(script: list[Any], **kwargs: Any) -> tuple[HttpsFetcher, list, Clock]:
    log: list[dict[str, Any]] = []
    clock = Clock()

    def factory(host: str, timeout: float) -> FakeConnection:
        assert host in ALLOWED_HOSTS
        return FakeConnection(script, log)

    fetcher = HttpsFetcher(
        HOST,
        timeout=5.0,
        clock=clock.time,
        sleep=clock.sleep,
        connection_factory=factory,
        **kwargs,
    )
    return fetcher, log, clock


# -- the allowlist -------------------------------------------------------------


def test_a_host_outside_the_allowlist_is_refused_before_any_socket() -> None:
    with pytest.raises(ProviderError) as caught:
        HttpsFetcher("evil.example.com", timeout=5.0)
    assert caught.value.category == "blocked_host"
    assert str(caught.value) == "blocked_host"


def test_the_allowlist_holds_exactly_one_host() -> None:
    assert ALLOWED_HOSTS == frozenset({"eutils.ncbi.nlm.nih.gov"})


def test_a_lookalike_host_is_still_refused() -> None:
    for host in ("eutils.ncbi.nlm.nih.gov.evil.test", "ncbi.nlm.nih.gov", "localhost"):
        with pytest.raises(ProviderError):
            HttpsFetcher(host, timeout=5.0)


# -- responses -----------------------------------------------------------------


def test_a_normal_reply_comes_back_as_bytes() -> None:
    fetcher, log, _ = build([FakeResponse(200, b"hello")])
    assert fetcher.get("/entrez/eutils/esearch.fcgi", {"term": "sepsis"}) == b"hello"
    assert log[0]["method"] == "GET"
    assert log[0]["url"] == "/entrez/eutils/esearch.fcgi?term=sepsis"


def test_the_user_agent_names_vademecum_and_carries_no_contact_by_default() -> None:
    fetcher, log, _ = build([FakeResponse(200, b"{}")])
    fetcher.get("/x", {})
    agent = log[0]["headers"]["User-Agent"]
    assert agent.startswith("Vademecum/")
    assert "mailto" not in agent


def test_a_contact_address_is_sent_only_when_the_caller_supplies_one() -> None:
    fetcher, log, _ = build([FakeResponse(200, b"{}")], contact_email="owner@example.test")
    fetcher.get("/x", {})
    assert "(mailto:owner@example.test)" in log[0]["headers"]["User-Agent"]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_is_refused_rather_than_followed(status: int) -> None:
    fetcher, log, _ = build([FakeResponse(status, b"")])
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "redirect_refused"
    assert len(log) == 1, "a refused redirect makes exactly one request"


def test_an_oversized_body_is_refused_rather_than_buffered() -> None:
    fetcher, _, _ = build([FakeResponse(200, b"x" * (MAX_BODY_BYTES + 10))])
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "too_large"


def test_an_oversized_content_length_is_refused_before_the_read() -> None:
    fetcher, _, _ = build(
        [FakeResponse(200, b"short", content_length=str(MAX_BODY_BYTES + 1))]
    )
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "too_large"


def test_a_timeout_reports_the_timeout_category_and_nothing_else() -> None:
    fetcher, _, _ = build([TimeoutError("connect timed out")] * 3)
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "timeout"
    assert str(caught.value) == "timeout"
    assert "connect timed out" not in str(caught.value)


def test_a_connection_error_reports_the_connection_category() -> None:
    fetcher, _, _ = build([OSError("no route to host")] * 3)
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "connection"
    assert "no route" not in str(caught.value)


def test_a_client_error_carries_no_provider_text() -> None:
    fetcher, log, _ = build([FakeResponse(404, b"<html>Not Found: term=...</html>")])
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert str(caught.value) == "http_error"
    assert len(log) == 1


# -- retries and throttling ----------------------------------------------------


def test_a_500_is_retried_twice_with_exponential_backoff() -> None:
    fetcher, log, clock = build([FakeResponse(500, b"")] * 3)
    with pytest.raises(ProviderError) as caught:
        fetcher.get("/x", {})
    assert caught.value.category == "http_error"
    assert len(log) == 3, "one attempt plus two retries, and no more"
    backoffs = [value for value in clock.slept if value >= 1.0]
    assert backoffs == [1.0, 2.0]


def test_a_retry_can_succeed() -> None:
    fetcher, log, _ = build([FakeResponse(503, b""), FakeResponse(200, b"ok")])
    assert fetcher.get("/x", {}) == b"ok"
    assert len(log) == 2


def test_a_429_is_retried_but_a_400_is_not() -> None:
    fetcher, log, _ = build([FakeResponse(429, b""), FakeResponse(200, b"ok")])
    assert fetcher.get("/x", {}) == b"ok"

    fetcher, log, _ = build([FakeResponse(400, b""), FakeResponse(200, b"ok")])
    with pytest.raises(ProviderError):
        fetcher.get("/x", {})
    assert len(log) == 1, "a 4xx other than 429 is an answer, not a hiccup"


def test_calls_are_spaced_by_the_rate_limit() -> None:
    fetcher, _, clock = build([FakeResponse(200, b"a"), FakeResponse(200, b"b")])
    fetcher.get("/x", {})
    fetcher.get("/y", {})
    assert clock.slept, "the second call waited"
    assert clock.slept[0] == pytest.approx(1 / 3, abs=1e-6)


# -- query validation ----------------------------------------------------------


def test_a_plain_topic_is_accepted() -> None:
    assert pubmed.validate_query("  sepsis fluids ") == "sepsis fluids"
    assert pubmed.validate_query("(sepsis OR septic shock) AND crystalloid")
    assert pubmed.validate_query("Ann Intern Med[ta] AND 2025[dp]")


def test_a_multi_line_passage_is_rejected() -> None:
    passage = (
        "The patient was started on norepinephrine.\n"
        "Lactate cleared over six hours.\n"
    )
    with pytest.raises(ValueError) as caught:
        pubmed.validate_query(passage)
    assert "single line" in str(caught.value)
    assert isinstance(caught.value, QueryError)


def test_a_five_thousand_character_string_is_rejected() -> None:
    with pytest.raises(ValueError) as caught:
        pubmed.validate_query("sepsis " * 715)
    assert "at most 200 characters" in str(caught.value)


def test_an_empty_query_is_rejected() -> None:
    with pytest.raises(ValueError):
        pubmed.validate_query("   ")


@pytest.mark.parametrize("query", ["sepsis; DROP", "sepsis <tag>", "sepsis {x}", "café"])
def test_characters_outside_the_conservative_set_are_rejected(query: str) -> None:
    with pytest.raises(ValueError):
        pubmed.validate_query(query)


def test_a_rejected_query_never_reaches_the_fetcher() -> None:
    fetcher = fake_pubmed.FakeFetcher()
    provider = PubMedProvider(fetcher, max_results=5)
    with pytest.raises(ValueError):
        provider.search("a passage about a particular patient.\nand a second line")
    assert fetcher.calls == []


# -- parsing -------------------------------------------------------------------


def articles(**kwargs: Any) -> list[pubmed.Article]:
    provider = PubMedProvider(fake_pubmed.FakeFetcher(**kwargs), max_results=25)
    return provider.search("sepsis")


def test_a_normal_article_parses_into_its_fields() -> None:
    article = articles()[0]
    assert article.pmid == "40000001"
    assert article.doi == "10.1056/NEJMoa000001"
    assert article.title == "Balanced crystalloids in sepsis"
    assert article.journal == "N Engl J Med"
    assert article.abstract == (
        "BACKGROUND: Fluid choice is debated.\n\nRESULTS: No difference in mortality."
    )
    assert article.published_on == "2025-08-14"
    assert article.provider_date == "2025-08-15"
    assert article.publication_types == ("Journal Article", "Randomized Controlled Trial")
    assert article.url == "https://pubmed.ncbi.nlm.nih.gov/40000001/"
    assert article.priority == "trial"


def test_a_record_with_no_abstract_gets_an_empty_string_not_a_summary() -> None:
    article = next(a for a in articles() if a.pmid == "40000002")
    assert article.abstract == ""
    assert article.as_row()["abstract"] == ""
    assert article.title == "Abstract unavailable for this record"


def test_no_module_writes_a_substitute_abstract() -> None:
    """The refusal is structural: nothing in the package writes abstract text."""
    from pathlib import Path

    package = Path(pubmed.__file__).parent
    for path in sorted(package.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for phrase in ("No abstract available", "Summary unavailable", "summarise"):
            assert phrase not in text, f"{path.name} writes stand-in abstract text"


def test_a_retraction_and_a_correction_are_different_flags() -> None:
    found = {article.pmid: article for article in articles()}
    retracted = found["40000003"]
    corrected = found["40000004"]

    assert (retracted.retracted, retracted.corrected) == (True, False)
    assert (corrected.retracted, corrected.corrected) == (False, True)
    assert [note.ref_type for note in retracted.correction_notes] == ["RetractionIn"]
    assert retracted.correction_notes[0].pmid == "40000099"
    assert [note.ref_type for note in corrected.correction_notes] == ["ErratumIn"]


# -- retraction status: types and link direction -------------------------------


def test_a_retracted_publication_type_alone_is_a_retraction() -> None:
    """The defect: no CommentsCorrections entry, so it reported retracted=False.

    NLM types a withdrawn paper "Retracted Publication" before, or instead of,
    linking the notice. Reading only the links under-reports every one of them.
    """
    article = articles(
        identifiers=("40000010",), xml=fake_pubmed.article_set(fake_pubmed.RETRACTED_BY_TYPE)
    )[0]
    assert article.retracted is True
    assert article.corrected is False
    assert article.is_notice is False
    assert article.correction_notes == ()


def test_a_retraction_notice_is_marked_as_a_notice_and_is_not_retracted() -> None:
    """Direction: RetractionOf is this article retracting another one."""
    article = articles(
        identifiers=("40000099",),
        xml=fake_pubmed.article_set(fake_pubmed.RETRACTION_NOTICE),
    )[0]
    assert article.is_notice is True, "a notice must never be cited as support"
    assert article.retracted is False, "the notice itself was not retracted"
    assert article.corrected is False
    assert article.correction_notes[0].ref_type == "RetractionOf"


def test_an_erratum_notice_is_a_notice_and_the_corrected_paper_is_not() -> None:
    found = {
        article.pmid: article
        for article in articles(
            identifiers=("40000004", "40000098"),
            xml=fake_pubmed.article_set(fake_pubmed.CORRECTED, fake_pubmed.ERRATUM_NOTICE),
        )
    }
    erratum = found["40000098"]
    original = found["40000004"]

    assert (erratum.is_notice, erratum.corrected, erratum.retracted) == (True, False, False)
    assert (original.is_notice, original.corrected, original.retracted) == (
        False,
        True,
        False,
    )


def test_an_expression_of_concern_has_a_direction_too() -> None:
    found = {
        article.pmid: article
        for article in articles(
            identifiers=("40000011", "40000097"),
            xml=fake_pubmed.article_set(fake_pubmed.CONCERNED, fake_pubmed.CONCERN_NOTICE),
        )
    }
    concerned = found["40000011"]
    notice = found["40000097"]

    assert (concerned.corrected, concerned.retracted, concerned.is_notice) == (
        True,
        False,
        False,
    )
    assert (notice.is_notice, notice.corrected, notice.retracted) == (True, False, False)


def test_a_correction_is_never_reported_as_a_retraction() -> None:
    for xml, pmid in (
        (fake_pubmed.CORRECTED, "40000004"),
        (fake_pubmed.CONCERNED, "40000011"),
    ):
        article = articles(identifiers=(pmid,), xml=fake_pubmed.article_set(xml))[0]
        assert article.corrected is True
        assert article.retracted is False, "an erratum is not a withdrawal"


def test_derive_status_reads_types_and_directions_together() -> None:
    assert pubmed.derive_status(("Retracted Publication",), ()) == (True, False, False)
    assert pubmed.derive_status((), ("RetractionIn",)) == (True, False, False)
    assert pubmed.derive_status((), ("RetractionOf",)) == (False, False, True)
    assert pubmed.derive_status((), ("ErratumIn",)) == (False, True, False)
    assert pubmed.derive_status((), ("ErratumFor",)) == (False, False, True)
    assert pubmed.derive_status((), ("ExpressionOfConcernIn",)) == (False, True, False)
    assert pubmed.derive_status((), ("ExpressionOfConcernFor",)) == (False, False, True)
    assert pubmed.derive_status(("Published Erratum",), ()) == (False, False, True)
    assert pubmed.derive_status(("Expression of Concern",), ()) == (False, False, True)
    assert pubmed.derive_status(("Journal Article",), ("CommentIn",)) == (
        False,
        False,
        False,
    )
    # A retracted paper that also had an erratum is both, and never merely one.
    assert pubmed.derive_status(("Retracted Publication",), ("ErratumIn",)) == (
        True,
        True,
        False,
    )


# -- fetch by identifier -------------------------------------------------------


def test_fetch_by_pmid_asks_for_the_named_records_without_a_search() -> None:
    fetcher = fake_pubmed.FakeFetcher()
    found = PubMedProvider(fetcher, max_results=5).fetch_by_pmid(["40000003"])
    assert [path for path, _ in fetcher.calls] == [pubmed.EFETCH_PATH], (
        "a refresh does not search: no date window is involved"
    )
    assert fetcher.calls[0][1]["id"] == "40000003"
    assert {article.pmid for article in found} >= {"40000003"}


def test_fetch_by_pmid_refuses_anything_that_is_not_digits() -> None:
    fetcher = fake_pubmed.FakeFetcher()
    provider = PubMedProvider(fetcher, max_results=5)
    provider.fetch_by_pmid(["40000001", "../etc/passwd", "40000001", " 40000002 "])
    assert fetcher.calls[0][1]["id"] == "40000001,40000002"

    assert provider.fetch_by_pmid([]) == []
    assert provider.fetch_by_pmid(["nonsense"]) == []
    assert len(fetcher.calls) == 1, "nothing valid to ask about means no request"


def test_fetch_by_pmid_batches_and_uses_the_same_throttled_client() -> None:
    fetcher = fake_pubmed.FakeFetcher()
    wanted = [str(40000000 + n) for n in range(pubmed.EFETCH_BATCH + 5)]
    PubMedProvider(fetcher, max_results=5).fetch_by_pmid(wanted)
    sizes = [len(params["id"].split(",")) for _, params in fetcher.calls]
    assert sizes == [pubmed.EFETCH_BATCH, 5]
    # Same Fetcher as search: the allowlist, throttle, retry and body cap are
    # not restated for refresh, they are simply the same ones.
    assert all(path == pubmed.EFETCH_PATH for path, _ in fetcher.calls)


def test_fetch_by_pmid_reports_a_bad_payload_as_a_category() -> None:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(xml=fake_pubmed.MALFORMED_PAYLOAD), max_results=5
    )
    with pytest.raises(ProviderError) as caught:
        provider.fetch_by_pmid(["40000001"])
    assert caught.value.category == pubmed.MALFORMED


def test_an_article_with_a_doi_and_no_pmid_keeps_the_doi_and_gets_no_url() -> None:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(
            identifiers=("40000007",), xml=fake_pubmed.article_set(fake_pubmed.DOI_ONLY)
        ),
        max_results=5,
    )
    article = provider.search("troponin")[0]
    assert article.pmid == ""
    assert article.doi == "10.1161/CIRC.000002"
    assert article.url == "", "the URL is built from a PMID or it is not built"
    assert article.published_on == "2023", "a MedlineDate yields the year, honestly"


def test_a_partial_date_is_reported_as_far_as_it_is_known() -> None:
    found = {article.pmid: article for article in articles()}
    assert found["40000002"].published_on == "2019"
    assert found["40000004"].published_on == "2024-02"


def test_priority_is_derived_from_type_then_journal() -> None:
    assert pubmed.derive_priority(("Guideline",), "Chest") == "guideline"
    assert pubmed.derive_priority(("Practice Guideline",), "Chest") == "guideline"
    assert pubmed.derive_priority(("Clinical Trial, Phase III",), "Chest") == "trial"
    assert pubmed.derive_priority(("Journal Article",), "Lancet") == "major_journal"
    assert pubmed.derive_priority(("Journal Article",), "J Hosp Med") == "other"
    assert pubmed.derive_priority((), "") == "other"


def test_priority_is_only_ever_one_of_four_ranking_hints() -> None:
    """There is no value that means "important", so none can be produced."""
    assert pubmed.PRIORITIES == ("guideline", "trial", "major_journal", "other")
    produced = {article.priority for article in articles()}
    assert produced <= set(pubmed.PRIORITIES)
    for banned in ("practice_changing", "landmark", "must_read", "important"):
        assert banned not in pubmed.PRIORITIES


# -- the XML boundary: a real DOCTYPE in, every entity refused ------------------


def test_a_real_efetch_reply_with_the_nlm_doctype_parses() -> None:
    """The defect: every genuine reply carries an external DOCTYPE.

    ``forbid_dtd=True`` rejected all of them, so the watch failed against the
    real provider on every single check while passing its own tests.
    """
    assert "<!DOCTYPE PubmedArticleSet PUBLIC" in fake_pubmed.REALISTIC_EFETCH
    assert "dtd.nlm.nih.gov" in fake_pubmed.REALISTIC_EFETCH

    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(xml=fake_pubmed.REALISTIC_EFETCH), max_results=25
    )
    parsed = provider.search("sepsis")
    assert [article.pmid for article in parsed if article.pmid] == [
        "40000001",
        "40000002",
        "40000003",
        "40000004",
    ]
    assert parsed[0].title == "Balanced crystalloids in sepsis"


def test_a_doctype_with_an_entity_is_refused_not_expanded() -> None:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(
            identifiers=("40000006",), xml=fake_pubmed.DOCTYPE_PAYLOAD
        ),
        max_results=5,
    )
    with pytest.raises(ProviderError) as caught:
        provider.search("sepsis")
    assert caught.value.category == pubmed.MALFORMED
    assert fake_pubmed.ENTITY_MARKER not in str(caught.value)


@pytest.mark.parametrize("name", sorted(fake_pubmed.MALICIOUS_PAYLOADS))
def test_every_entity_payload_is_refused_without_expanding(name: str) -> None:
    """Internal, parameter, external-URL and billion-laughs: all refused."""
    payload = fake_pubmed.MALICIOUS_PAYLOADS[name]
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(identifiers=("40000006",), xml=payload), max_results=5
    )
    with pytest.raises(ProviderError) as caught:
        provider.search("sepsis")
    assert caught.value.category == pubmed.MALFORMED
    assert fake_pubmed.ENTITY_MARKER not in str(caught.value)


def test_an_external_file_entity_never_reads_the_file(tmp_path: Any) -> None:
    """XXE: the refusal has to happen before anything opens the path."""
    secret = tmp_path / "secret.txt"
    secret.write_text(fake_pubmed.ENTITY_MARKER, encoding="utf-8")
    payload = fake_pubmed.EXTERNAL_FILE_ENTITY_TEMPLATE.format(path=secret)

    opened: list[str] = []
    import builtins

    real_open = builtins.open

    def watched_open(file: Any, *args: Any, **kwargs: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    builtins.open = watched_open
    try:
        provider = PubMedProvider(
            fake_pubmed.FakeFetcher(identifiers=("40000006",), xml=payload),
            max_results=5,
        )
        with pytest.raises(ProviderError) as caught:
            provider.search("sepsis")
    finally:
        builtins.open = real_open

    assert caught.value.category == pubmed.MALFORMED
    assert str(secret) not in opened, "the parser opened the file the payload named"
    assert fake_pubmed.ENTITY_MARKER not in str(caught.value)


def test_parsing_a_real_reply_opens_no_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """A DOCTYPE naming an external DTD must not go and get it."""
    import socket

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("the parser opened a socket")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    root = pubmed._parse_xml(fake_pubmed.REALISTIC_EFETCH.encode("utf-8"))
    assert root.tag == "PubmedArticleSet"


def test_the_parser_still_refuses_entities_even_if_the_scan_is_bypassed() -> None:
    """Belt and braces: the scan is not the only defence.

    If a declaration ever slips past the byte scan, the parser itself must
    raise rather than expand. Calling defusedxml with this module's settings
    proves the second layer is really configured.
    """
    from defusedxml import ElementTree
    from defusedxml.common import EntitiesForbidden

    with pytest.raises(EntitiesForbidden):
        ElementTree.fromstring(
            fake_pubmed.INTERNAL_ENTITY_PAYLOAD,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )


def test_a_malformed_payload_is_a_category_not_a_crash() -> None:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(xml=fake_pubmed.MALFORMED_PAYLOAD), max_results=5
    )
    with pytest.raises(ProviderError) as caught:
        provider.search("sepsis")
    assert caught.value.category == pubmed.MALFORMED


def test_malformed_search_json_is_a_category_too() -> None:
    provider = PubMedProvider(
        fake_pubmed.FakeFetcher(search_payload="{not json"), max_results=5
    )
    with pytest.raises(ProviderError) as caught:
        provider.search("sepsis")
    assert caught.value.category == pubmed.MALFORMED


def test_no_results_makes_no_second_call() -> None:
    fetcher = fake_pubmed.FakeFetcher(identifiers=())
    provider = PubMedProvider(fetcher, max_results=5)
    assert provider.search("sepsis") == []
    assert [path for path, _ in fetcher.calls] == [pubmed.ESEARCH_PATH]


def test_the_search_sends_the_topic_and_nothing_else() -> None:
    fetcher = fake_pubmed.FakeFetcher()
    PubMedProvider(fetcher, max_results=7).search("sepsis fluids")
    path, params = fetcher.calls[0]
    assert path == pubmed.ESEARCH_PATH
    assert params == {
        "db": "pubmed",
        "retmode": "json",
        "retmax": "7",
        "sort": "date",
        "term": "sepsis fluids",
    }
    assert fetcher.sent_terms == ["sepsis fluids"]


def test_non_numeric_identifiers_from_the_provider_are_discarded() -> None:
    fetcher = fake_pubmed.FakeFetcher(
        search_payload=fake_pubmed.esearch_json("40000001", "../etc/passwd", "40000001")
    )
    PubMedProvider(fetcher, max_results=25).search("sepsis")
    _, params = fetcher.calls[1]
    assert params["id"] == "40000001"


def test_as_row_matches_the_record_columns(connection: Any) -> None:
    """The dict as_row() returns is insertable, column for column."""
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(literature_records)").fetchall()
    }
    row = articles()[0].as_row()
    assert set(row) <= columns
    assert row["retracted"] in (0, 1) and row["corrected"] in (0, 1)
    assert row["publication_types"].startswith("[")
