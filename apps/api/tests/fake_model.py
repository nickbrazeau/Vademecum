"""A scripted stand-in for the App Server turn runner and the PubMed provider.

No process, no socket, no model. These satisfy the same interfaces the real
implementations do -- ``TurnRunner.run`` and ``PubMedProvider.search`` -- so the
build pipeline, the grading path and the storage rules all run against them
unchanged.

Replies are queued by output-schema shape, because one build makes three
different kinds of turn (synthesis, evidence, assessment) through the same
factory.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vademecum.appserver.errors import BridgeError
from vademecum.model import schemas as model_schemas


@dataclass(frozen=True)
class TurnResult:
    payload: dict[str, Any]
    raw_chars: int = 0
    turn_id: str = "turn_fake"
    duration_ms: float = 1.0


class ScriptedTurns:
    """A turn factory whose replies a test decides in advance.

    ``synthesis``/``evidence``/``assessment`` are queues consumed in order; when
    a queue runs out its last entry repeats, so a test that cares about one
    stage does not have to enumerate the others. A queued ``BridgeError`` is
    raised instead of returned, which is how a failing stage is scripted.
    """

    def __init__(
        self,
        *,
        synthesis: list[Any] | None = None,
        evidence: list[Any] | None = None,
        assessment: list[Any] | None = None,
    ) -> None:
        self.synthesis = list(synthesis or [])
        self.evidence = list(evidence or [])
        self.assessment = list(assessment or [])
        self.calls: list[dict[str, Any]] = []

    def __call__(self) -> "ScriptedTurns":
        # The factory hands back itself: one script, however many turns.
        return self

    async def run(
        self,
        *,
        instructions: str,
        developer_instructions: str,
        prompt: str,
        output_schema: dict[str, Any],
        max_output_chars: int = 200_000,
    ) -> TurnResult:
        kind = _kind_of(output_schema)
        self.calls.append(
            {
                "kind": kind,
                "prompt": prompt,
                "instructions": instructions,
                "developer_instructions": developer_instructions,
            }
        )
        queue = getattr(self, kind)
        if not queue:
            raise AssertionError(f"no scripted {kind} reply")
        reply = queue[0] if len(queue) == 1 else queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return TurnResult(payload=reply)

    async def cancel(self) -> None:  # pragma: no cover - nothing to cancel
        return None

    def prompts(self, kind: str) -> list[str]:
        return [call["prompt"] for call in self.calls if call["kind"] == kind]


def _kind_of(schema: dict[str, Any]) -> str:
    if schema is model_schemas.SYNTHESIS_SCHEMA:
        return "synthesis"
    if schema is model_schemas.EVIDENCE_SCHEMA:
        return "evidence"
    if schema is model_schemas.ASSESSMENT_SCHEMA:
        return "assessment"
    if schema is model_schemas.GRADING_SCHEMA:
        return "grading"
    raise AssertionError("unknown output schema")


class ScriptedGrader(ScriptedTurns):
    """A turn factory for the grading path only."""

    def __init__(self, replies: list[Any]) -> None:
        super().__init__()
        self.grading = list(replies)


@dataclass
class FakeArticle:
    """The shape ``model/build.py`` and ``storage/learning.py`` consume."""

    pmid: str
    title: str
    abstract: str
    doi: str | None = None
    journal: str = "Journal of Testing"
    published_on: str | None = "2025-01-15"
    provider_date: str | None = "2025-01-20"
    publication_types: tuple[str, ...] = ("Journal Article",)
    retracted: bool = False
    corrected: bool = False
    is_notice: bool = False
    url: str = ""
    priority: str = "other"

    def as_row(self) -> dict[str, Any]:
        import json

        return {
            "pmid": self.pmid,
            "doi": self.doi,
            "title": self.title,
            "journal": self.journal,
            "abstract": self.abstract,
            "published_on": self.published_on,
            "provider_date": self.provider_date,
            "publication_types": json.dumps(list(self.publication_types)),
            "retracted": self.retracted,
            "corrected": self.corrected,
            "correction_notes": "[]",
            "url": self.url or f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/",
            "priority": self.priority,
        }


@dataclass
class FakeProvider:
    """A provider that returns canned articles and records what it was asked."""

    articles: list[FakeArticle] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    error: BaseException | None = None

    def search(self, query: str) -> list[FakeArticle]:
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return list(self.articles)

    def fetch_by_pmid(self, pmids: list[str]) -> list[FakeArticle]:
        wanted = set(pmids)
        return [article for article in self.articles if article.pmid in wanted]
