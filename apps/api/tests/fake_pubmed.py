"""Canned E-utilities payloads and a Fetcher that never opens a socket.

Every literature test runs against these. The XML is trimmed to the elements the
parser actually reads, plus:

* :data:`REALISTIC_EFETCH` -- what NCBI actually sends, external DOCTYPE line
  and all. Refusing that DOCTYPE meant refusing every real reply.
* the payloads that must be refused: an internal entity, a parameter entity, an
  external entity pointing at a file and at a URL, a billion-laughs bomb, and a
  truncated document.
* the retraction/erratum/expression-of-concern shapes, in both directions.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from vademecum.literature.http import ProviderError

# -- individual records --------------------------------------------------------

WITH_ABSTRACT = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000001</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>N Engl J Med</ISOAbbreviation>
        <Title>The New England Journal of Medicine</Title>
        <JournalIssue>
          <PubDate><Year>2025</Year><Month>Aug</Month><Day>14</Day></PubDate>
        </JournalIssue>
      </Journal>
      <ArticleTitle>Balanced crystalloids in sepsis</ArticleTitle>
      <Abstract>
        <AbstractText Label="BACKGROUND">Fluid choice is debated.</AbstractText>
        <AbstractText Label="RESULTS">No difference in mortality.</AbstractText>
      </Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
        <PublicationType>Randomized Controlled Trial</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <History>
      <PubMedPubDate PubStatus="entrez">
        <Year>2025</Year><Month>8</Month><Day>15</Day>
      </PubMedPubDate>
    </History>
    <ArticleIdList>
      <ArticleId IdType="pubmed">40000001</ArticleId>
      <ArticleId IdType="doi">10.1056/NEJMoa000001</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# No <Abstract> at all. The stored abstract must be "" and stay "".
NO_ABSTRACT = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000002</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>J Hosp Med</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2019</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Abstract unavailable for this record</ArticleTitle>
      <PublicationTypeList>
        <PublicationType>Editorial</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">40000002</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

RETRACTED = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000003</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>Lancet</ISOAbbreviation>
        <JournalIssue>
          <PubDate><Year>2020</Year><Month>May</Month><Day>22</Day></PubDate>
        </JournalIssue>
      </Journal>
      <ArticleTitle>Hydroxychloroquine and outcomes</ArticleTitle>
      <Abstract><AbstractText>An observational analysis.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="RetractionIn">
        <RefSource>Lancet. 2020;395:1820</RefSource>
        <PMID>40000099</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">40000003</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# Same article as RETRACTED, before the retraction was linked.
NOT_YET_RETRACTED = RETRACTED.replace(
    """    <CommentsCorrectionsList>
      <CommentsCorrections RefType="RetractionIn">
        <RefSource>Lancet. 2020;395:1820</RefSource>
        <PMID>40000099</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
""",
    "",
)

CORRECTED = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000004</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>BMJ</ISOAbbreviation>
        <JournalIssue>
          <PubDate><Year>2024</Year><Month>Feb</Month></PubDate>
        </JournalIssue>
      </Journal>
      <ArticleTitle>Anticoagulation after ablation</ArticleTitle>
      <Abstract><AbstractText>A cohort study.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Practice Guideline</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ErratumIn">
        <RefSource>BMJ. 2024;384:q1</RefSource>
        <PMID>40000098</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="pubmed">40000004</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

NOT_YET_CORRECTED = CORRECTED.replace(
    """    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ErratumIn">
        <RefSource>BMJ. 2024;384:q1</RefSource>
        <PMID>40000098</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
""",
    "",
)

# A DOI and no usable PMID: deduplication has to fall back to the DOI.
DOI_ONLY = """
<PubmedArticle>
  <MedlineCitation>
    <PMID></PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>Circulation</ISOAbbreviation>
        <JournalIssue><PubDate><MedlineDate>2023 Nov-Dec</MedlineDate></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Ahead of print: troponin thresholds</ArticleTitle>
      <Abstract><AbstractText>Early release.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList>
      <ArticleId IdType="doi">10.1161/CIRC.000002</ArticleId>
    </ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# The same record twice in one reply, which providers do.
DUPLICATE_PMID = WITH_ABSTRACT

GUIDELINE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000005</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>Chest</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2025</Year><Month>Jan</Month><Day>02</Day></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Antithrombotic therapy: a guideline</ArticleTitle>
      <Abstract><AbstractText>Recommendations.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Guideline</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000005</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# -- the retraction/correction shapes, in both directions ----------------------

# Typed "Retracted Publication" with no CommentsCorrections entry at all. NLM
# does this: the type is assigned before, or instead of, the link.
RETRACTED_BY_TYPE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000010</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>J Clin Invest</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Withdrawn: a mechanism study</ArticleTitle>
      <Abstract><AbstractText>A mechanism.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
        <PublicationType>Retracted Publication</PublicationType>
      </PublicationTypeList>
    </Article>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000010</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# The retraction notice itself: it is *about* 40000003, it is not retracted.
RETRACTION_NOTICE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000099</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>Lancet</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2020</Year><Month>Jun</Month></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Retraction: Hydroxychloroquine and outcomes</ArticleTitle>
      <PublicationTypeList>
        <PublicationType>Retraction of Publication</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="RetractionOf">
        <RefSource>Lancet. 2020;395:1</RefSource>
        <PMID>40000003</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000099</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# The erratum itself: a notice, not a corrected article.
ERRATUM_NOTICE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000098</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>BMJ</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2024</Year><Month>Mar</Month></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Correction: Anticoagulation after ablation</ArticleTitle>
      <PublicationTypeList>
        <PublicationType>Published Erratum</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ErratumFor">
        <RefSource>BMJ. 2024;384:e1</RefSource>
        <PMID>40000004</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000098</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# An expression of concern applies to this article: corrected, not retracted.
CONCERNED = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000011</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>JAMA</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2022</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Under review: a cohort study</ArticleTitle>
      <Abstract><AbstractText>A cohort.</AbstractText></Abstract>
      <PublicationTypeList>
        <PublicationType>Journal Article</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ExpressionOfConcernIn">
        <RefSource>JAMA. 2023;329:1</RefSource>
        <PMID>40000097</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000011</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""

# The expression-of-concern notice itself.
CONCERN_NOTICE = """
<PubmedArticle>
  <MedlineCitation>
    <PMID>40000097</PMID>
    <Article>
      <Journal>
        <ISOAbbreviation>JAMA</ISOAbbreviation>
        <JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue>
      </Journal>
      <ArticleTitle>Expression of Concern: Under review</ArticleTitle>
      <PublicationTypeList>
        <PublicationType>Expression of Concern</PublicationType>
      </PublicationTypeList>
    </Article>
    <CommentsCorrectionsList>
      <CommentsCorrections RefType="ExpressionOfConcernFor">
        <RefSource>JAMA. 2022;327:1</RefSource>
        <PMID>40000011</PMID>
      </CommentsCorrections>
    </CommentsCorrectionsList>
  </MedlineCitation>
  <PubmedData>
    <ArticleIdList><ArticleId IdType="pubmed">40000097</ArticleId></ArticleIdList>
  </PubmedData>
</PubmedArticle>
"""


def article_set(*records: str) -> str:
    return "<PubmedArticleSet>" + "".join(records) + "</PubmedArticleSet>"


DEFAULT_XML = article_set(WITH_ABSTRACT, NO_ABSTRACT, RETRACTED, CORRECTED, DOI_ONLY)


# -- what NCBI actually sends --------------------------------------------------

# The real thing. Every genuine EFetch reply opens with an external DOCTYPE, so
# a parser that refuses all DTDs refuses every real response -- which is exactly
# what happened. The DOCTYPE itself expands nothing and fetches nothing.
NLM_DOCTYPE = (
    '<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, '
    '1st January 2024//EN" '
    '"https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_240101.dtd">'
)

REALISTIC_EFETCH = (
    '<?xml version="1.0" ?>\n'
    + NLM_DOCTYPE
    + "\n"
    + article_set(WITH_ABSTRACT, NO_ABSTRACT, RETRACTED, CORRECTED, DOI_ONLY)
)


# -- payloads that must be refused ---------------------------------------------

# If any of these ever parses, the marker appears in a title and the test that
# looks for it fails. None of them may cause a file read or a socket either.
ENTITY_MARKER = "ZZ-ENTITY-EXPANDED-ZZ"

_ONE_ARTICLE = """<PubmedArticleSet>
  <PubmedArticle>
    <MedlineCitation>
      <PMID>40000006</PMID>
      <Article><ArticleTitle>&smuggled;</ArticleTitle></Article>
    </MedlineCitation>
  </PubmedArticle>
</PubmedArticleSet>
"""

# 1. An internal general entity.
INTERNAL_ENTITY_PAYLOAD = (
    '<?xml version="1.0"?>\n'
    f'<!DOCTYPE PubmedArticleSet [\n  <!ENTITY smuggled "{ENTITY_MARKER}">\n]>\n'
    + _ONE_ARTICLE
)

# Kept under its old name: the existing test refers to it.
DOCTYPE_PAYLOAD = INTERNAL_ENTITY_PAYLOAD

# 2. A parameter entity that declares a general entity -- the indirection that
#    defeats a naive "does it reference an entity" check.
PARAMETER_ENTITY_PAYLOAD = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE PubmedArticleSet [\n"
    f"  <!ENTITY % wrapper \"<!ENTITY smuggled '{ENTITY_MARKER}'>\">\n"
    "  %wrapper;\n"
    "]>\n" + _ONE_ARTICLE
)

# 3. An external entity pointing at a file. Reading it would be an XXE; the
#    path is filled in by the test with a real file it can prove was not read.
EXTERNAL_FILE_ENTITY_TEMPLATE = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE PubmedArticleSet [\n  <!ENTITY smuggled SYSTEM "file://{path}">\n]>\n'
    + _ONE_ARTICLE
)

# 4. An external entity pointing at a URL. Fetching it would be egress to a host
#    that is not on the allowlist.
EXTERNAL_URL_ENTITY_PAYLOAD = (
    '<?xml version="1.0"?>\n'
    '<!DOCTYPE PubmedArticleSet [\n'
    '  <!ENTITY smuggled SYSTEM "http://169.254.169.254/latest/meta-data/">\n'
    "]>\n" + _ONE_ARTICLE
)

# 5. Billion laughs. Expanding it would exhaust memory before anything else.
BILLION_LAUGHS_PAYLOAD = (
    '<?xml version="1.0"?>\n'
    "<!DOCTYPE PubmedArticleSet [\n"
    '  <!ENTITY a "ZZ-ENTITY-EXPANDED-ZZ">\n'
    '  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
    '  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n'
    '  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">\n'
    '  <!ENTITY e "&d;&d;&d;&d;&d;&d;&d;&d;&d;&d;">\n'
    '  <!ENTITY f "&e;&e;&e;&e;&e;&e;&e;&e;&e;&e;">\n'
    '  <!ENTITY g "&f;&f;&f;&f;&f;&f;&f;&f;&f;&f;">\n'
    '  <!ENTITY h "&g;&g;&g;&g;&g;&g;&g;&g;&g;&g;">\n'
    '  <!ENTITY i "&h;&h;&h;&h;&h;&h;&h;&h;&h;&h;">\n'
    "]>\n"
    "<PubmedArticleSet>\n"
    "  <PubmedArticle>\n"
    "    <MedlineCitation>\n"
    "      <PMID>40000006</PMID>\n"
    "      <Article><ArticleTitle>&i;</ArticleTitle></Article>\n"
    "    </MedlineCitation>\n"
    "  </PubmedArticle>\n"
    "</PubmedArticleSet>\n"
)

MALICIOUS_PAYLOADS = {
    "internal_entity": INTERNAL_ENTITY_PAYLOAD,
    "parameter_entity": PARAMETER_ENTITY_PAYLOAD,
    "external_url_entity": EXTERNAL_URL_ENTITY_PAYLOAD,
    "billion_laughs": BILLION_LAUGHS_PAYLOAD,
}

MALFORMED_PAYLOAD = "<PubmedArticleSet><PubmedArticle><MedlineCitation>"


def esearch_json(*identifiers: str) -> str:
    return json.dumps(
        {
            "header": {"type": "esearch", "version": "0.3"},
            "esearchresult": {
                "count": str(len(identifiers)),
                "retmax": str(len(identifiers)),
                "idlist": list(identifiers),
            },
        }
    )


DEFAULT_IDS = ("40000001", "40000002", "40000003", "40000004", "40000005")


class FakeFetcher:
    """A :class:`~vademecum.literature.http.Fetcher` backed by strings.

    Records every call so a test can assert what was (and was not) sent.
    """

    def __init__(
        self,
        *,
        identifiers: tuple[str, ...] = DEFAULT_IDS,
        xml: str = DEFAULT_XML,
        search_payload: str | None = None,
    ) -> None:
        self.identifiers = identifiers
        self.xml = xml
        self.search_payload = search_payload
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, str]) -> bytes:
        self.calls.append((path, dict(params)))
        if path.endswith("esearch.fcgi"):
            payload = (
                self.search_payload
                if self.search_payload is not None
                else esearch_json(*self.identifiers)
            )
            return payload.encode("utf-8")
        if path.endswith("efetch.fcgi"):
            return self.xml.encode("utf-8")
        raise AssertionError(f"the provider asked for an unexpected path: {path}")

    @property
    def sent_terms(self) -> list[str]:
        return [params["term"] for _, params in self.calls if "term" in params]


class FailingFetcher:
    """Fails every call with one transport category."""

    def __init__(self, category: str = "timeout") -> None:
        self.category = category
        self.calls = 0

    def get(self, path: str, params: dict[str, str]) -> bytes:
        self.calls += 1
        raise ProviderError(self.category)


class ScriptedFetcher:
    """A fetcher whose efetch reply changes between calls.

    Enough to stage "the world changed upstream": the first check sees a paper,
    the refresh a week later sees it retracted. Search and fetch-by-id both go
    through :meth:`get`, so a test can also assert the refresh used the same
    client -- and therefore the same allowlist, throttle and body cap.
    """

    def __init__(self, stages: list[str], identifiers: tuple[str, ...] = DEFAULT_IDS):
        self.stages = list(stages)
        self.identifiers = identifiers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, str]) -> bytes:
        self.calls.append((path, dict(params)))
        if path.endswith("esearch.fcgi"):
            return esearch_json(*self.identifiers).encode("utf-8")
        if path.endswith("efetch.fcgi"):
            stage = self.stages[0] if len(self.stages) == 1 else self.stages.pop(0)
            return stage.encode("utf-8")
        raise AssertionError(f"the provider asked for an unexpected path: {path}")

    @property
    def fetched_ids(self) -> list[str]:
        return [
            params["id"]
            for path, params in self.calls
            if path.endswith("efetch.fcgi") and "id" in params
        ]


class RecordingProvider:
    """A provider stand-in for the scheduler: scripted per query, and counted.

    ``searches`` maps a topic query to either a list of articles or an
    exception to raise, so one topic can fail while another succeeds.
    """

    def __init__(
        self,
        searches: dict[str, Any] | None = None,
        *,
        refresh: Any = None,
    ) -> None:
        self.searches = searches or {}
        self.refresh = refresh
        self.queries: list[str] = []
        self.refreshed: list[list[str]] = []

    def search(self, query: str) -> list[Any]:
        self.queries.append(query)
        outcome = self.searches.get(query, [])
        if isinstance(outcome, Exception):
            raise outcome
        return list(outcome)

    def fetch_by_pmid(self, pmids: list[str]) -> list[Any]:
        self.refreshed.append(list(pmids))
        if isinstance(self.refresh, Exception):
            raise self.refresh
        return list(self.refresh or [])


class BlockingProvider:
    """A provider that stops inside ``search`` until the test lets it go.

    Two runs overlapping is only observable if one of them can be held open, so
    the concurrency tests are written against this. It also counts how many
    searches were inside it at once, which is the thing that must never exceed
    one. No socket here either.
    """

    def __init__(self, articles: list[Any] | None = None) -> None:
        self.articles = list(articles or [])
        self.started = threading.Event()
        self.release = threading.Event()
        self.queries: list[str] = []
        self.refreshed: list[list[str]] = []
        self.inside = 0
        self.most_at_once = 0
        self._guard = threading.Lock()

    def search(self, query: str) -> list[Any]:
        with self._guard:
            self.queries.append(query)
            self.inside += 1
            self.most_at_once = max(self.most_at_once, self.inside)
        self.started.set()
        # A bound, so a broken gate fails the test instead of hanging the suite.
        self.release.wait(timeout=10.0)
        with self._guard:
            self.inside -= 1
        return list(self.articles)

    def fetch_by_pmid(self, pmids: list[str]) -> list[Any]:
        self.refreshed.append(list(pmids))
        return []
