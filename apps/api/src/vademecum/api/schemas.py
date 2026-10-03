"""Request and response models.

All input validation happens here (AGENTS.md, engineering expectations). Length
limits are deliberate and generous; nothing attempts to detect identifiers --
a detector that is wrong in either direction is worse than a clear rule and an
honest warning (ADR 0002, rule 4).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Tier = Literal["low", "mid", "high"]
FlagStatus = Literal["open", "addressed"]

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ItemTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
Body = Annotated[str, StringConstraints(max_length=20000)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
FlagText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
RecordId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class Strict(BaseModel):
    """Unknown fields are a client bug, not something to silently accept."""

    model_config = ConfigDict(extra="forbid")


class PileCreate(Strict):
    title: Title
    tier: Tier
    description: Description = ""


class PileUpdate(Strict):
    title: Title | None = None
    tier: Tier | None = None
    description: Description | None = None


class PileCoverage(BaseModel):
    """Public aggregate coverage uses the same character cursor as Build."""

    chars_total: int = Field(ge=0)
    chars_covered: int = Field(ge=0)
    percent: int = Field(ge=0, le=100)
    complete: bool
    segments_total: int = Field(ge=0)
    segments_covered: int = Field(ge=0)


class Pile(BaseModel):
    id: str
    title: str
    tier: Tier
    description: str
    created_at: str
    updated_at: str
    item_count: int
    confidence_label: str
    source_count: int = Field(ge=0)
    point_count: int = Field(ge=0)
    question_count: int = Field(ge=0)
    coverage: PileCoverage


class ItemCreate(Strict):
    title: ItemTitle
    body: Body = ""
    source: Source = ""


class ItemUpdate(Strict):
    title: ItemTitle | None = None
    body: Body | None = None
    source: Source | None = None
    pile_id: RecordId | None = None


class LearningItem(BaseModel):
    id: str
    pile_id: str
    title: str
    body: str
    source: str
    content_hash: str
    created_at: str
    updated_at: str


class FlagCreate(Strict):
    """`text` is the only required field: capture must cost one action."""

    text: FlagText
    topic: Topic | None = None
    pile_id: RecordId | None = None


class FlagUpdate(Strict):
    text: FlagText | None = None
    topic: Topic | None = None
    status: FlagStatus | None = None
    pile_id: RecordId | None = None


class Flag(BaseModel):
    id: str
    text: str
    topic: str | None
    pile_id: str | None
    status: FlagStatus
    created_at: str
    updated_at: str
    addressed_at: str | None


class CuratedArticles(BaseModel):
    articles: list[dict[str, Any]]
    source_configured: bool
    message: str


class TierSummary(BaseModel):
    tier: Tier
    pile_count: int
    item_count: int


class CoverSheet(BaseModel):
    curated: CuratedArticles
    worth_a_look: list[LearningItem]
    recent_flags: list[Flag]
    tiers: list[TierSummary]
    open_flag_count: int


class TopicGap(BaseModel):
    topic: str | None
    open_flags: int
    addressed_flags: int
    last_flagged_at: str | None


class TopicSpecialtyUpdate(Strict):
    """The owner's call on which specialty a topic belongs to. `null` clears it."""

    topic: Topic
    specialty_id: RecordId | None = None


class MapPosition(Strict):
    topic: Topic
    x: float
    y: float


class MapPositionsUpdate(Strict):
    """Every node the client drew. A topic left out has left the map."""

    positions: list[MapPosition] = Field(max_length=2000)


class ImprovementMap(BaseModel):
    topics: list[TopicGap]
    tiers: list[TierSummary]
    unfiled_flag_count: int


class WrittenFile(BaseModel):
    """Deliberately has no path field (ADR 0002, rule 6)."""

    model_config = ConfigDict(extra="allow")

    filename: str
    directory: str
    created_at: str
    byte_size: int


class StoredFile(BaseModel):
    filename: str
    directory: str
    byte_size: int


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    schema_version: int
    database: Literal["ok"]
    loopback_only: bool
    model_bridge_configured: bool = Field(
        default=False,
        description=(
            "A Codex App Server bridge exists and can report sign-in state. It is started "
            "lazily, on first use, and never at startup."
        ),
    )
    model_calls_configured: bool = Field(
        default=False,
        description=(
            "Model turns exist and are reachable. They run only when the owner presses "
            "Build learning material or Grade, and each is preceded by a disclosure of "
            "exactly what would be sent."
        ),
    )
    literature_configured: bool = Field(
        default=False,
        description=(
            "The public-literature watch is available. It sends short public topic words "
            "to PubMed (NCBI E-utilities) and nothing from the owner's material."
        ),
    )
    model_mode: Literal["codex", "host", "claude"] = Field(
        default="codex",
        description=(
            "Who does the model work. `codex`: the local Codex child on this Mac. "
            "`claude`: the Claude Code CLI on this Mac, on the owner's Claude sign-in (ADR 0019). "
            "`host`: the learner's assistant through pending/submit tools; this process "
            "never calls a model (ADR 0009)."
        ),
    )
    tenancy: Literal["single", "multi"] = Field(
        default="single",
        description=(
            "`single`: one owner, one workspace, no identity. `multi`: a workspace per "
            "learner, every request identified by its token (ADR 0010)."
        ),
    )


# --- model bridge (ADR 0006) -------------------------------------------------
#
# Everything below is already sanitised by the bridge. These models exist so the
# shape is declared at the HTTP boundary as well: an account email or identifier
# has no field to arrive in, whatever a future refactor does upstream.

ModelState = Literal["connecting", "signed_out", "signed_in", "rate_limited", "unavailable"]


class UsageWindowOut(BaseModel):
    """How full one rate-limit window is. No amounts, no balances, no ids."""

    used_percent: int = Field(ge=0, le=100)
    resets_at: str | None = Field(
        default=None,
        description="UTC ISO 8601, when the App Server reported a plausible reset time.",
    )
    window_minutes: int | None = None


class RateLimitsOut(BaseModel):
    primary: UsageWindowOut | None = None
    secondary: UsageWindowOut | None = None
    limited: bool = False
    limit_reason: str | None = None


class ModelStatusOut(BaseModel):
    state: ModelState
    signed_in: bool
    plan: str | None = Field(
        default=None, description="Codex plan label, when the account reports one."
    )
    rate_limits: RateLimitsOut | None = None
    login_pending: bool = False
    detail: str = Field(description="Plain language. Never an App Server error string.")
    reason: str | None = Field(
        default=None, description="A closed-set failure category, for the interface to branch on."
    )
    checked_at: str


class DeviceLoginOut(BaseModel):
    """Returned once, to a live ``no-store`` response, and stored nowhere.

    ADR 0006: the verification URL and one-time code are shown on screen and
    then forgotten. They are not logged, not written to the database, and the
    interface is forbidden from putting them in browser storage.
    """

    verification_url: str
    user_code: str
    login_id: str
    status: Literal["pending"] = "pending"


class LoginCancelOut(BaseModel):
    status: Literal["canceled", "notFound", "nothing_pending"]


# --- source intake, build, Tutor and literature ------------------------------
#
# Input models are exhaustive because input is where validation belongs. The
# richer read shapes (a learning point with its citations and evidence, a Tutor
# question with its anchors) are assembled by the storage layer's `as_dict` and
# returned as plain JSON: duplicating them here would be a second definition to
# keep in step, and the security-relevant half is the input.

Confidence = Literal["low", "mid", "high"]
Quote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=64)]
LearnerAnswer = Annotated[str, StringConstraints(max_length=8000)]
TopicLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# A literature query is the only thing derived from the owner's world that
# leaves for a provider. Short and single-line by construction; the provider
# validates the character set again before it is sent.
TopicQuery = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]


class SourceUpdate(Strict):
    confidence: Confidence | None = None
    excluded: bool | None = None


class BuildStart(Strict):
    """Consent to a specific, previewed set of passages.

    Both fields are required. The id alone would let a stale preview authorise a
    send of different material; the hash is what ties the consent to the exact
    passages the owner read.
    """

    batch_id: RecordId
    selection_hash: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=64, max_length=64)
    ]


class SchematicCreate(Strict):
    """An SVG the assistant drew for a learning point (ADR 0013)."""

    title: Title
    svg: Annotated[str, StringConstraints(min_length=1, max_length=512 * 1024)]


class TurnSubmission(Strict):
    """A result for one pending host turn (ADR 0009).

    ``result`` is validated against the turn's own output schema in
    ``model.host``, not here: which schema applies is a property of the turn.
    """

    turn_id: RecordId
    result: dict[str, Any]


class TutorAdvance(Strict):
    question_id: RecordId


class TutorGrade(Strict):
    question_id: RecordId
    answer: LearnerAnswer


class TutorReveal(Strict):
    question_id: RecordId


class TutorSelfAssess(Strict):
    question_id: RecordId
    answer: LearnerAnswer
    outcome: Literal["correct", "partially_correct", "incorrect"]


class LiteratureTopicCreate(Strict):
    label: TopicLabel
    query: TopicQuery


class LiteratureTopicUpdate(Strict):
    label: TopicLabel | None = None
    query: TopicQuery | None = None
    enabled: bool | None = None


class LiteratureCheckRequest(Strict):
    topic_id: RecordId | None = None


class LiteratureSettings(Strict):
    weekly_enabled: bool
    interval_hours: Annotated[float, Field(ge=1, le=8760)] = 168.0
    # Which records come first (ADR 0007): practice guidelines, and these
    # journals, by PubMed title abbreviation. Omitted means "leave as is".
    preferred_journals: Annotated[list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]], Field(max_length=12)] | None = None
    guidelines_first: bool | None = None


class UpdateState(Strict):
    state: Literal["unread", "acknowledged", "dismissed"]


class ErrorDetail(BaseModel):
    field: str
    problem: str


class ErrorBody(BaseModel):
    code: str
    message: str
    correlation_id: str | None = None
    fields: list[ErrorDetail] = []


class ErrorResponse(BaseModel):
    error: ErrorBody
