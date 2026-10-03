"""The public-literature watch: the whole of Vademecum's own network egress.

Four files, in the order a reviewer should read them:

* :mod:`.http` -- three allowlisted hosts, one verb, no redirects, bounded
  bodies, categorised failures. Nothing else in the backend opens a socket.
* :mod:`.pubmed` -- validates a short public topic string, calls E-utilities,
  and parses the reply with entities and DTDs refused.
* :mod:`.cases` -- the Case Series hub's fixed requests (ADR 0022): the NEJM
  case series through PubMed, two podcast sites through their public JSON.
* :mod:`.scheduler` -- the in-app asyncio watcher that decides when to do it.

What leaves the machine is a topic query, or a fixed request with nothing of
the owner's in it, and nothing else. No source passage, no note, no learning
point, no learner answer and no filename has a path into any of these modules.
"""

from .http import ALLOWED_HOSTS, Fetcher, HttpsFetcher, ProviderError
from .pubmed import (
    Article,
    CorrectionNote,
    PubMedProvider,
    QueryError,
    derive_status,
    validate_query,
)
from .scheduler import LiteratureWatcher

__all__ = [
    "ALLOWED_HOSTS",
    "Article",
    "CorrectionNote",
    "Fetcher",
    "HttpsFetcher",
    "LiteratureWatcher",
    "ProviderError",
    "PubMedProvider",
    "QueryError",
    "derive_status",
    "validate_query",
]
