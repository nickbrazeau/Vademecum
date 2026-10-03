"""The Case Series hub's fetchers (ADR 0022): fixed public requests, nothing of the owner's.

Three publishers of teaching cases, two routes to them:

* The NEJM's two case series -- Case Records of the Massachusetts General
  Hospital and Clinical Problem-Solving -- come from PubMed, through the same
  fetcher the literature watch uses. One fixed query names the journal and the
  publication type; the DOI prefix tells the two series apart (``NEJMcpc``,
  ``NEJMcps``) and everything else the query returns is dropped. PubMed carries
  no abstract for these, so an entry is a title, its authors, a date and a link.
* The Clinical Problem Solvers and The Curbsiders publish their episodes on
  WordPress sites whose public JSON endpoint lists posts by category. One fixed
  request each: a category id, a page size and a field list. The show notes
  come back as HTML and are reduced to text here.

What is sent is the same request every time, with no owner text in it: no
topic, no flag, no passage, no answer. What comes back is parsed defensively:
JSON and XML with entities refused, every field length-bounded, and a link kept
only when it points back at the publisher that sent it.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Any
from xml.etree.ElementTree import Element

from .http import Fetcher, HttpsFetcher, ProviderError
from .pubmed import (
    EFETCH_BATCH,
    EFETCH_PATH,
    ESEARCH_PATH,
    MALFORMED,
    _PMID_ONLY,
    _article,
    _parse_xml,
)

MAX_TITLE = 300
MAX_TEXT = 12_000
MAX_URL = 500
MAX_CREDIT = 300
MAX_ITEMS = 100

# --- the NEJM, through PubMed --------------------------------------------------

NEJM_QUERY = '"N Engl J Med"[ta] AND "Case Reports"[pt]'
# The DOI prefix is the series: Case Records are clinicopathological
# conferences (cpc); Clinical Problem-Solving is cps. Images in Clinical
# Medicine (icm) and Clinical Decisions (cld) share the query and are dropped.
NEJM_SERIES_BY_PREFIX = {"nejmcpc": "nejm_cpc", "nejmcps": "nejm_cps"}
NEJM_ARTICLE_URL = "https://www.nejm.org/doi/full/"
_NEJM_DOI = re.compile(r"\A10\.1056/(nejm[a-z]+)\d", re.IGNORECASE)


def classify_nejm(doi: str) -> str | None:
    """Which NEJM case series a DOI belongs to, or None when it is neither."""
    match = _NEJM_DOI.match(doi.strip())
    if match is None:
        return None
    return NEJM_SERIES_BY_PREFIX.get(match.group(1).lower())


# --- the podcasts, through WordPress -------------------------------------------

WORDPRESS_POSTS_PATH = "/wp-json/wp/v2/posts"
WORDPRESS_FIELDS = "id,date,link,title,content"


@dataclass(frozen=True)
class WordPressSeries:
    series: str
    host: str
    category: int
    per_page: int = 30


# The category ids are the sites' own, read once from their public category
# lists: "Episodes" on the Clinical Problem Solvers, "Curbsiders Podcast" on
# The Curbsiders (which keeps the paediatric Cribsiders out).
WORDPRESS_SERIES: tuple[WordPressSeries, ...] = (
    WordPressSeries("cps", "clinicalproblemsolving.com", 8253),
    WordPressSeries("curbsiders", "thecurbsiders.com", 36),
)

_SUBSERIES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"clinical unknown", re.IGNORECASE), "Clinical Unknown"),
    (re.compile(r"\bschema\b", re.IGNORECASE), "Schema"),
    (re.compile(r"spaced learning", re.IGNORECASE), "Spaced Learning"),
    (re.compile(r"\bRLR\b"), "RLR"),
    (re.compile(r"\bWDx\b", re.IGNORECASE), "WDx"),
    (re.compile(r"\bVMR\b|virtual morning report", re.IGNORECASE), "Virtual Morning Report"),
    (re.compile(r"anti-?racism", re.IGNORECASE), "Anti-Racism in Medicine"),
    (re.compile(r"hotcakes", re.IGNORECASE), "Hotcakes"),
    (re.compile(r"\bdigest\b", re.IGNORECASE), "Digest"),
    (re.compile(r"\brecap\b", re.IGNORECASE), "Recap"),
    (re.compile(r"\blive\b", re.IGNORECASE), "Live"),
)


def subseries_of(title: str) -> str:
    """The strand of a podcast an episode belongs to, read from its title."""
    for pattern, label in _SUBSERIES:
        if pattern.search(title):
            return label
    return ""


# --- what a fetch returns ------------------------------------------------------


@dataclass(frozen=True)
class CaseItem:
    series: str
    external_id: str
    title: str
    url: str
    published_on: str | None
    credit: str = ""
    subseries: str = ""
    # The publisher's public text for the case: show notes, or nothing for a
    # journal article PubMed carries no abstract for. Bounded here.
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FetchGroup:
    """One request that may feed more than one series (the NEJM query feeds two)."""

    name: str
    series: frozenset[str]
    fetch: Callable[[], list[CaseItem]]


# --- parsing -------------------------------------------------------------------

_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote", "table", "hr"}
)
_MEDIA_URL = re.compile(r"https?://\S+\.(?:mp3|wav|m4a|mp4|ogg)\S*", re.IGNORECASE)
_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}")


class _TextOnly(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)


def strip_html(markup: str) -> str:
    """Show notes as plain text: tags gone, entities resolved, media links dropped."""
    parser = _TextOnly()
    parser.feed(markup)
    parser.close()
    text = _MEDIA_URL.sub(" ", "".join(parser.parts))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _clean(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit].strip()


def _rendered(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("rendered")
    return value if isinstance(value, str) else ""


def _own_link(link: str, host: str) -> bool:
    return link.startswith(f"https://{host}/") and len(link) <= MAX_URL


def _authors(element: Element) -> str:
    names: list[str] = []
    for author in element.iter("Author"):
        collective = author.find("CollectiveName")
        if collective is not None and (collective.text or "").strip():
            names.append(_clean(collective.text or "", 120))
            continue
        last = (author.findtext("LastName") or "").strip()
        fore = (author.findtext("ForeName") or "").strip()
        if last:
            names.append(_clean(f"{fore} {last}", 80))
    return ", ".join(names)[:MAX_CREDIT]


def _identifiers(payload: bytes, limit: int) -> list[str]:
    try:
        document = json.loads(payload.decode("utf-8", "replace"))
    except (ValueError, AttributeError) as exc:
        raise ProviderError(MALFORMED) from exc
    result = document.get("esearchresult") if isinstance(document, dict) else None
    raw = result.get("idlist") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        raise ProviderError(MALFORMED)
    seen: set[str] = set()
    identifiers: list[str] = []
    for value in raw:
        text = str(value).strip()
        if _PMID_ONLY.match(text) is None or text in seen:
            continue
        seen.add(text)
        identifiers.append(text)
    return identifiers[:limit]


# --- the fetches ---------------------------------------------------------------


def fetch_nejm(fetcher: Fetcher, *, max_results: int = 60) -> list[CaseItem]:
    """The newest NEJM case reports, kept only where the DOI names a case series."""
    limit = max(1, min(int(max_results), MAX_ITEMS))
    payload = fetcher.get(
        ESEARCH_PATH,
        {"db": "pubmed", "retmode": "json", "retmax": str(limit), "sort": "date", "term": NEJM_QUERY},
    )
    identifiers = _identifiers(payload, limit)
    items: list[CaseItem] = []
    for start in range(0, len(identifiers), EFETCH_BATCH):
        batch = identifiers[start : start + EFETCH_BATCH]
        xml = fetcher.get(EFETCH_PATH, {"db": "pubmed", "retmode": "xml", "id": ",".join(batch)})
        root = _parse_xml(xml)
        for element in root.iter("PubmedArticle"):
            article = _article(element)
            series = classify_nejm(article.doi)
            title = _clean(article.title.rstrip("."), MAX_TITLE)
            if series is None or not title:
                continue
            items.append(
                CaseItem(
                    series=series,
                    external_id=article.doi,
                    title=title,
                    url=f"{NEJM_ARTICLE_URL}{article.doi}",
                    published_on=article.published_on or article.provider_date,
                    credit=_authors(element),
                )
            )
    return items[:MAX_ITEMS]


def fetch_wordpress(fetcher: Fetcher, spec: WordPressSeries) -> list[CaseItem]:
    """The newest posts in one category, as a title, a link, a date and plain text."""
    payload = fetcher.get(
        WORDPRESS_POSTS_PATH,
        {"per_page": str(spec.per_page), "categories": str(spec.category), "_fields": WORDPRESS_FIELDS},
    )
    try:
        posts = json.loads(payload.decode("utf-8", "replace"))
    except (ValueError, AttributeError) as exc:
        raise ProviderError(MALFORMED) from exc
    if not isinstance(posts, list):
        raise ProviderError(MALFORMED)
    items: list[CaseItem] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        identifier = post.get("id")
        if type(identifier) is not int or identifier <= 0:
            continue
        title = _clean(html.unescape(_rendered(post.get("title"))), MAX_TITLE)
        link = str(post.get("link") or "")
        if not title or not _own_link(link, spec.host):
            continue
        date = str(post.get("date") or "")[:10]
        items.append(
            CaseItem(
                series=spec.series,
                external_id=str(identifier),
                title=title,
                url=link,
                published_on=date if _DATE.match(date) else None,
                subseries=subseries_of(title),
                text=strip_html(_rendered(post.get("content")))[:MAX_TEXT],
            )
        )
    return items[:MAX_ITEMS]


def build_fetch_groups(
    *,
    pubmed_fetcher: Fetcher | None,
    timeout: float,
    max_results: int,
    contact_email: str = "",
) -> list[FetchGroup]:
    """One group per request: the NEJM query through the shared PubMed fetcher
    (its throttle is NCBI's allowance for this whole process), and one fetcher
    per podcast site. No NCBI key goes anywhere but NCBI."""
    groups: list[FetchGroup] = []
    if pubmed_fetcher is not None:
        groups.append(
            FetchGroup(
                "nejm",
                frozenset(NEJM_SERIES_BY_PREFIX.values()),
                lambda: fetch_nejm(pubmed_fetcher, max_results=max_results),
            )
        )
    for spec in WORDPRESS_SERIES:
        site = HttpsFetcher(spec.host, timeout=timeout, contact_email=contact_email)
        groups.append(
            FetchGroup(spec.series, frozenset({spec.series}), lambda f=site, s=spec: fetch_wordpress(f, s))
        )
    return groups
