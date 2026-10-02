"""PubMed E-utilities: turn a short public topic string into article metadata.

Two calls per check -- ``esearch`` for identifiers, ``efetch`` for the records --
against the one host :mod:`.http` allows. The query is validated here rather
than at the socket because this is where the rule belongs: a topic is a handful
of public words, and anything that looks like prose (a newline, a passage, five
thousand characters) is refused before a request is built. That check is what
stops a source passage or a learner's answer being smuggled into a search.

Two things this module will not do. It never invents an abstract: a record with
no abstract gets ``""`` and the interface says so. And ``priority`` is an
ordering hint derived from publication type and journal -- it is not a claim
that anything is important, correct or practice-changing.

Three status facts, kept apart because collapsing any pair of them is a clinical
error:

* ``retracted`` -- this paper was withdrawn. Derived from the publication type
  *Retracted Publication* **and** from a ``RetractionIn`` link, because NLM
  populates the two at different times and either alone is enough.
* ``corrected`` -- an erratum or expression of concern applies to this paper. A
  correction is not a retraction and never sets ``retracted``.
* ``is_notice`` -- this record *is* the retraction/erratum/concern notice about
  some other paper. Direction matters: ``RetractionIn`` happened *to* this
  article, ``RetractionOf`` is this article happening to another one. A notice
  is not evidence for anything and callers must never cite one as support.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .http import CATEGORIES, Fetcher, ProviderError

HOST = "eutils.ncbi.nlm.nih.gov"
ESEARCH_PATH = "/entrez/eutils/esearch.fcgi"
EFETCH_PATH = "/entrez/eutils/efetch.fcgi"

# A body that arrived but cannot be read. Distinct from an HTTP failure on
# purpose: "the provider is down" and "the provider sent something we refuse to
# parse" are different facts for whoever is looking at a failed check.
MALFORMED = "malformed_response"
PROVIDER_CATEGORIES = CATEGORIES | {MALFORMED}

MAX_QUERY_CHARS = 200

# Identifiers per efetch call. Keeps a refresh batch the size of an ordinary
# search page, so the body cap in .http is the same limit either way.
EFETCH_BATCH = 50

# Letters, digits, spaces, and the punctuation a PubMed term actually needs:
# hyphenated drug names, decimals, field tags in brackets, truncation, and
# quoted phrases. Boolean AND/OR/NOT are letters and need no special case.
_QUERY_ALLOWED = re.compile(r"^[A-Za-z0-9 \-_.,()'\"\[\]:/*+]+$")

_PMID_ONLY = re.compile(r"^[0-9]+$")

PUBMED_URL_PREFIX = "https://pubmed.ncbi.nlm.nih.gov/"

PRIORITIES: tuple[str, ...] = ("guideline", "trial", "major_journal", "other")

_GUIDELINE_TYPES = frozenset({"guideline", "practice guideline"})
_TRIAL_TYPES = frozenset({"randomized controlled trial", "clinical trial"})

# ISO abbreviations, lowercased and stripped of stops. A small frozen list:
# a longer one would start to read like an opinion about which journals matter.
MAJOR_JOURNALS = frozenset(
    {
        "n engl j med",
        "lancet",
        "jama",
        "bmj",
        "ann intern med",
        "circulation",
    }
)

# --- status vocabulary -------------------------------------------------------
#
# RefType direction is the whole point. "...In"/"...Of" and "...For" are not
# synonyms: one says something happened *to this article*, the other says *this
# article is the notice*. The same distinction appears again in publication
# types, and NLM fills the two in at different times, so both are read.
#
# storage/literature.py holds a copy of the two NOTICE_* sets (it must decide
# the same question from a stored row without importing this package); a test
# pins the copies together.

# Something happened to THIS article.
_RETRACTION_REFTYPES = frozenset({"retractionin", "partialretractionin"})
_CORRECTION_REFTYPES = frozenset(
    {
        "erratumin",
        "correctionin",
        "expressionofconcernin",
        "correctedandrepublishedin",
    }
)

# THIS article is the notice about another one.
NOTICE_REF_TYPES = frozenset(
    {
        "retractionof",
        "partialretractionof",
        "erratumfor",
        "expressionofconcernfor",
        "correctedandrepublishedfrom",
    }
)

# Publication types, lowercased. "Retracted Publication" is the article that was
# withdrawn and often carries no CommentsCorrections entry at all -- reading
# only the links was the bug.
RETRACTED_PUBLICATION_TYPES = frozenset({"retracted publication"})
NOTICE_PUBLICATION_TYPES = frozenset(
    {
        "retraction of publication",
        "published erratum",
        "expression of concern",
    }
)

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


class QueryError(ValueError):
    """A topic query was rejected. The message is about shape, never content."""


@dataclass(frozen=True)
class CorrectionNote:
    """One raw CommentsCorrections entry, kept as the provider stated it."""

    ref_type: str
    pmid: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class Article:
    pmid: str = ""
    doi: str = ""
    title: str = ""
    journal: str = ""
    # "" means the provider returned no abstract. Nothing writes a stand-in.
    abstract: str = ""
    published_on: str | None = None
    provider_date: str | None = None
    publication_types: tuple[str, ...] = ()
    # Separate flags: a correction is not a retraction, and collapsing the two
    # would turn "one figure was wrong" into "this paper was withdrawn".
    retracted: bool = False
    corrected: bool = False
    # This record is itself a retraction/erratum/concern notice about another
    # paper. Never usable as supporting evidence.
    is_notice: bool = False
    correction_notes: tuple[CorrectionNote, ...] = ()
    url: str = ""
    priority: str = "other"

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["publication_types"] = list(self.publication_types)
        data["correction_notes"] = [note.as_dict() for note in self.correction_notes]
        return data

    def as_row(self) -> dict[str, Any]:
        """The literature_records columns this record owns.

        ``pmid``/``doi`` become NULL rather than ``''`` so the partial unique
        indexes treat "no identifier" as absent instead of as a shared value.

        ``is_notice`` has no column of its own and needs none: it is a pure
        function of the two blobs written here, so a stored row carries it
        losslessly and ``storage.literature.is_notice_row`` reads it back. The
        publication types and RefTypes that decide it are written verbatim,
        including the notice-only ones, precisely so that stays true.
        """
        return {
            "pmid": self.pmid or None,
            "doi": self.doi or None,
            "title": self.title,
            "journal": self.journal,
            "abstract": self.abstract,
            "published_on": self.published_on,
            "provider_date": self.provider_date,
            "publication_types": json.dumps(list(self.publication_types)),
            "retracted": 1 if self.retracted else 0,
            "corrected": 1 if self.corrected else 0,
            "correction_notes": json.dumps(
                [note.as_dict() for note in self.correction_notes]
            ),
            "url": self.url,
            "priority": self.priority,
        }


def validate_query(query: str) -> str:
    """Return the cleaned query, or raise :class:`QueryError`.

    Deliberately strict. Anything that could carry clinical text -- a newline,
    an essay's worth of characters, a character outside the conservative set --
    is refused rather than escaped, because the only legitimate input here is a
    short public topic.
    """
    cleaned = query.strip()
    if not cleaned:
        raise QueryError("a topic query cannot be empty")
    if "\n" in cleaned or "\r" in cleaned:
        raise QueryError("a topic query must be a single line")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise QueryError(
            f"a topic query may be at most {MAX_QUERY_CHARS} characters "
            f"(got {len(cleaned)})"
        )
    if _QUERY_ALLOWED.match(cleaned) is None:
        raise QueryError(
            "a topic query may contain only letters, digits, spaces and - _ . , "
            "( ) ' \" [ ] : / * +"
        )
    return cleaned


class PubMedProvider:
    """Search PubMed through an injected :class:`~.http.Fetcher`."""

    def __init__(self, fetcher: Fetcher, *, max_results: int) -> None:
        self._fetcher = fetcher
        self._max_results = max(1, int(max_results))

    def search(self, query: str) -> list[Article]:
        term = validate_query(query)
        identifiers = self._esearch(term)
        if not identifiers:
            return []
        return self._efetch(identifiers)

    def fetch_by_pmid(self, pmids: Sequence[str]) -> list[Article]:
        """Re-read named records, with no search and no date window.

        A search is date-sorted top-N, so a paper linked as evidence two years
        ago never appears in one again and its retraction is never noticed.
        This is the path that notices it. It goes through the same fetcher as
        :meth:`search`, so it inherits the same allowlist, throttle, retry and
        body-size limits without restating any of them.
        """
        wanted: list[str] = []
        seen: set[str] = set()
        for value in pmids:
            text = str(value).strip()
            # Digits only: these go straight into the efetch id parameter.
            if _PMID_ONLY.match(text) is None or text in seen:
                continue
            seen.add(text)
            wanted.append(text)
        if not wanted:
            return []
        articles: list[Article] = []
        # Batched so one call's URL and reply stay the size of an ordinary page.
        for start in range(0, len(wanted), EFETCH_BATCH):
            articles.extend(self._efetch(wanted[start : start + EFETCH_BATCH]))
        return articles

    # -- calls ----------------------------------------------------------------

    def _esearch(self, term: str) -> list[str]:
        payload = self._fetcher.get(
            ESEARCH_PATH,
            {
                "db": "pubmed",
                "retmode": "json",
                "retmax": str(self._max_results),
                "sort": "date",
                "term": term,
            },
        )
        try:
            document = json.loads(payload.decode("utf-8", "replace"))
        except (ValueError, AttributeError) as exc:
            raise ProviderError(MALFORMED) from exc
        if not isinstance(document, dict):
            raise ProviderError(MALFORMED)
        result = document.get("esearchresult")
        if not isinstance(result, dict):
            raise ProviderError(MALFORMED)
        raw = result.get("idlist") or []
        if not isinstance(raw, list):
            raise ProviderError(MALFORMED)
        # Digits only, and no more than asked for: these go straight into the
        # efetch id parameter and into a URL prefix.
        seen: set[str] = set()
        identifiers: list[str] = []
        for value in raw:
            text = str(value).strip()
            if _PMID_ONLY.match(text) is None or text in seen:
                continue
            seen.add(text)
            identifiers.append(text)
        return identifiers[: self._max_results]

    def _efetch(self, identifiers: list[str]) -> list[Article]:
        payload = self._fetcher.get(
            EFETCH_PATH,
            {"db": "pubmed", "retmode": "xml", "id": ",".join(identifiers)},
        )
        root = _parse_xml(payload)
        articles: list[Article] = []
        for element in root.iter("PubmedArticle"):
            article = _article(element)
            # Neither identifier means nothing to deduplicate on, and a row
            # that cannot be deduplicated is a new row on every single check.
            if not article.pmid and not article.doi:
                continue
            articles.append(article)
        return articles


# An ENTITY declaration, anywhere, in any spacing. Legitimate article text
# cannot contain this sequence unescaped, so a whole-payload scan is safe and
# needs no knowledge of where the internal subset ends.
_ENTITY_DECLARATION = re.compile(rb"<!\s*ENTITY", re.IGNORECASE)


def _parse_xml(payload: bytes) -> Element:
    """Parse a real EFetch reply, with every entity mechanism refused.

    A DOCTYPE is *allowed*. Genuine NLM replies open with

        <!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle..."
                  "https://dtd.nlm.nih.gov/...">

    so ``forbid_dtd=True`` rejected every real response the provider sends. A
    DOCTYPE on its own expands nothing and fetches nothing: expat never parses
    external parameter entities unless asked, and it is not asked here.

    What is dangerous is entity *declarations*, and those are refused twice:

    1. the payload is rejected outright if it declares any entity, general or
       parameter, internal or external -- so billion-laughs and XXE payloads
       never reach the parser at all; and
    2. the parser still runs with ``forbid_entities`` and ``forbid_external``,
       so a declaration this scan somehow missed raises rather than expands,
       and any external reference raises rather than opening a file or a
       socket.
    """
    if _ENTITY_DECLARATION.search(payload) is not None:
        # No expansion, no I/O, and no provider text in the error.
        raise ProviderError(MALFORMED)
    try:
        return ElementTree.fromstring(
            payload,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, ParseError, ValueError, TypeError) as exc:
        # No provider text in the error: a category, like every other failure.
        raise ProviderError(MALFORMED) from exc


# -- record extraction --------------------------------------------------------


def _article(element: Element) -> Article:
    found = element.find("MedlineCitation")
    citation = element if found is None else found
    article_el = citation.find("Article")

    pmid = _pmid(citation)
    doi = _doi(element)
    journal = _journal(article_el)
    types = _publication_types(article_el)
    notes = _correction_notes(citation)
    retracted, corrected, is_notice = derive_status(
        types, (note.ref_type for note in notes)
    )

    return Article(
        pmid=pmid,
        doi=doi,
        title=_text(None if article_el is None else article_el.find("ArticleTitle")),
        journal=journal,
        abstract=_abstract(article_el),
        published_on=_published_on(article_el),
        provider_date=_provider_date(element),
        publication_types=types,
        retracted=retracted,
        corrected=corrected,
        is_notice=is_notice,
        correction_notes=notes,
        url=f"{PUBMED_URL_PREFIX}{pmid}/" if pmid else "",
        priority=derive_priority(types, journal),
    )


def _pmid(citation: Element) -> str:
    value = _text(citation.find("PMID"))
    return value if _PMID_ONLY.match(value) else ""


def _doi(element: Element) -> str:
    for article_id in element.iter("ArticleId"):
        if (article_id.get("IdType") or "").lower() == "doi":
            value = _text(article_id)
            if value:
                return value
    for elid in element.iter("ELocationID"):
        if (elid.get("EIdType") or "").lower() == "doi":
            value = _text(elid)
            if value:
                return value
    return ""


def _journal(article_el: Element | None) -> str:
    if article_el is None:
        return ""
    journal = article_el.find("Journal")
    if journal is None:
        return ""
    iso = _text(journal.find("ISOAbbreviation"))
    return iso or _text(journal.find("Title"))


def _abstract(article_el: Element | None) -> str:
    """Labelled sections keep their labels; no abstract stays no abstract."""
    if article_el is None:
        return ""
    abstract = article_el.find("Abstract")
    if abstract is None:
        return ""
    sections: list[str] = []
    for part in abstract.findall("AbstractText"):
        body = _text(part)
        if not body:
            continue
        label = (part.get("Label") or "").strip()
        sections.append(f"{label}: {body}" if label else body)
    return "\n\n".join(sections)


def _publication_types(article_el: Element | None) -> tuple[str, ...]:
    if article_el is None:
        return ()
    types: list[str] = []
    for node in article_el.iter("PublicationType"):
        value = _text(node)
        if value and value not in types:
            types.append(value)
    return tuple(types)


def _correction_notes(citation: Element) -> tuple[CorrectionNote, ...]:
    """Every CommentsCorrections entry, verbatim, direction included."""
    notes: list[CorrectionNote] = []
    for node in citation.iter("CommentsCorrections"):
        ref_type = (node.get("RefType") or "").strip()
        if not ref_type:
            continue
        notes.append(CorrectionNote(ref_type=ref_type, pmid=_text(node.find("PMID"))))
    return tuple(notes)


def derive_status(
    publication_types: Iterable[str], ref_types: Iterable[str]
) -> tuple[bool, bool, bool]:
    """``(retracted, corrected, is_notice)`` from types and link directions.

    Both inputs matter. A withdrawn paper is often typed *Retracted
    Publication* with no CommentsCorrections entry at all, and a link can
    arrive before the type is assigned; reading either one alone under-reports.
    """
    types = {value.strip().lower() for value in publication_types}
    refs = {value.strip().lower().replace(" ", "") for value in ref_types}

    retracted = bool(types & RETRACTED_PUBLICATION_TYPES) or bool(
        refs & _RETRACTION_REFTYPES
    )
    # Deliberately not `elif`: an erratum on a paper that is also retracted is
    # still an erratum, and a retraction is never reported as a mere correction.
    corrected = bool(refs & _CORRECTION_REFTYPES)
    is_notice = bool(types & NOTICE_PUBLICATION_TYPES) or bool(refs & NOTICE_REF_TYPES)
    return retracted, corrected, is_notice


def derive_priority(publication_types: tuple[str, ...], journal: str) -> str:
    """An ordering hint, in this order. Never a judgement about the findings."""
    lowered = [value.strip().lower() for value in publication_types]
    if any(value in _GUIDELINE_TYPES for value in lowered):
        return "guideline"
    for value in lowered:
        # "Clinical Trial, Phase III" and friends.
        if value in _TRIAL_TYPES or value.startswith("clinical trial"):
            return "trial"
    if _normalise_journal(journal) in MAJOR_JOURNALS:
        return "major_journal"
    return "other"


def _normalise_journal(journal: str) -> str:
    return " ".join(journal.replace(".", " ").split()).lower()


# -- dates --------------------------------------------------------------------


def _published_on(article_el: Element | None) -> str | None:
    """Best effort, and honest about how far it got: YYYY-MM-DD, YYYY-MM, YYYY."""
    if article_el is None:
        return None
    journal = article_el.find("Journal")
    pub_date = None if journal is None else journal.find("JournalIssue/PubDate")
    if pub_date is None:
        pub_date = article_el.find(".//PubDate")
    if pub_date is None:
        return None

    year = _digits(_text(pub_date.find("Year")))
    if not year:
        # MedlineDate is free text such as "2023 Nov-Dec"; take the year only.
        match = re.search(r"(1[0-9]{3}|2[0-9]{3})", _text(pub_date.find("MedlineDate")))
        return match.group(1) if match else None
    month = _month(_text(pub_date.find("Month")))
    if month is None:
        return year
    day = _digits(_text(pub_date.find("Day")))
    if not day:
        return f"{year}-{month:02d}"
    return f"{year}-{month:02d}-{int(day):02d}"


def _provider_date(element: Element) -> str | None:
    """When PubMed itself first had the record, if it says."""
    dates = {
        (node.get("PubStatus") or "").lower(): node
        for node in element.iter("PubMedPubDate")
    }
    for status in ("entrez", "pubmed", "medline"):
        node = dates.get(status)
        if node is None:
            continue
        year = _digits(_text(node.find("Year")))
        month = _month(_text(node.find("Month")))
        day = _digits(_text(node.find("Day")))
        if not year:
            continue
        if month is None:
            return year
        if not day:
            return f"{year}-{month:02d}"
        return f"{year}-{month:02d}-{int(day):02d}"
    return None


def _month(value: str) -> int | None:
    if not value:
        return None
    digits = _digits(value)
    if digits:
        number = int(digits)
        return number if 1 <= number <= 12 else None
    return _MONTHS.get(value[:3].lower())


def _digits(value: str) -> str:
    stripped = value.strip()
    return stripped if stripped.isdigit() else ""


def _text(node: Element | None) -> str:
    """All the text under a node, markup and all, collapsed to one string."""
    if node is None:
        return ""
    return " ".join("".join(node.itertext()).split())
