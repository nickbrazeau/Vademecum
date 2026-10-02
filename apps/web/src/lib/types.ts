/**
 * The shapes this interface renders.
 *
 * These are *view* types, not wire types. Everything the API sends is passed
 * through `lib/normalize.ts` first, which fills in every array, object, number
 * and string. Rendering code may therefore call `.map` and `.length` freely:
 * the guarantee is made once, at the boundary, rather than at every use.
 */

/** The wire values never change; only the words shown for them do. */
export type Tier = 'low' | 'mid' | 'high'
export type FlagStatus = 'open' | 'addressed'

export const TIERS: readonly Tier[] = ['low', 'mid', 'high']

/**
 * How much the owner trusts the material. Not mastery, not priority, not
 * difficulty, and not whether anything in it has been verified.
 */
export const TIER_LABEL: Record<Tier, string> = {
  low: 'Low',
  mid: 'Medium',
  high: 'High'
}

/** Said wherever a tier is named, so the word cannot drift into a ranking. */
export const CONFIDENCE_MEANING =
  'Source confidence is your judgment of the material’s accuracy and usefulness for learning. It is not mastery, priority, difficulty, or evidence that a claim has been verified.'

// The API's own limits (`schemas.py`). Enforced here too, so an over-long
// value is refused where it is typed rather than after a round trip.
export const MAX_PILE_TITLE_LENGTH = 200
export const MAX_ITEM_TITLE_LENGTH = 300
export const MAX_ITEM_BODY_LENGTH = 20000
export const MAX_ITEM_SOURCE_LENGTH = 500
export const MAX_ANSWER_LENGTH = 8000

/**
 * How much material has been through a build.
 *
 * The API counts characters, not passages: a single page can be longer than one
 * batch may send, so counting pages would call a 12,000-character page finished
 * after 2,400 of it had gone. One older summary (a pile row) still counts
 * passages, so the unit travels with the number and is always printed.
 */
export type CoverageUnit = 'characters' | 'passages'

export interface Coverage {
  total: number
  covered: number
  /** 0-100, floored below 100 so "100%" never sits next to "not complete". */
  percent: number
  complete: boolean
  unit: CoverageUnit
}

/** The same question asked of one batch, before it is sent. */
export interface BatchCoverage {
  chars_total: number
  chars_covered: number
  chars_in_batch: number
  chars_remaining_after: number
  excerpts_in_batch: number
  complete: boolean
  /** Whether this batch would finish the pile. */
  complete_after: boolean
}

export interface Pile {
  id: string
  title: string
  tier: Tier
  confidence_label: string
  description: string
  created_at: string
  updated_at: string
  item_count: number
  source_count: number
  point_count: number
  question_count: number
  coverage: Coverage
}

/**
 * The older plain-text notes.
 *
 * They are kept and shown, but generation reads uploaded source segments only —
 * nothing here is used to build material. The interface says that plainly
 * rather than implying otherwise.
 */
export interface LearningItem {
  id: string
  pile_id: string
  title: string
  body: string
  source: string
  content_hash: string
  created_at: string
  updated_at: string
}

export type SourceStatus = 'stored' | 'extracted' | 'needs_ocr' | 'encrypted' | 'unreadable'

/** What the parser managed to get out of the file, unit by unit. */
export interface ExtractionCoverage {
  units_total: number
  units_with_text: number
  units_image_only: number
  units_failed: number
  units_dropped: number
  document_truncated: boolean
}

export interface Source {
  id: string
  pile_id: string
  display_name: string
  media_type: string
  byte_size: number
  sha256: string
  confidence: Tier
  confidence_label: string
  status: SourceStatus
  status_detail: string
  unit_kind: string
  unit_count: number
  char_count: number
  excluded: boolean
  point_count: number
  coverage: Coverage
  extraction_coverage: ExtractionCoverage
  created_at: string
  updated_at: string
  extracted_at: string | null
}

export interface Segment {
  id: string
  source_id: string
  ordinal: number
  kind: string
  locator: string
  text: string
  char_count: number
  /** How far into this passage a build has read. */
  covered_upto: number
  fully_covered: boolean
}

export type SourceDetail = Source & { segments_preview: Segment[] }

export type TopicSuggestion = {
  topic: string
  query: string
  point_count: number
  supported_count: number
  already_watched: boolean
}

export type UploadOutcome = 'stored' | 'duplicate' | 'rejected'

export interface UploadResult {
  filename: string
  outcome: UploadOutcome
  source: Source | null
  message: string
  warnings: string[]
}

export interface UploadReport {
  accepted: number
  rejected: number
  results: UploadResult[]
}

// --- building learning material ---

export interface Excerpt {
  source_id: string
  segment_id: string
  display_name: string
  confidence: Tier
  confidence_label: string
  locator: string
  /** "page 3", or "page 3 (characters 2400–4800)" for part of a page. */
  range_label: string
  text: string
  start: number
  end: number
  char_count: number
}

export interface BuildDisclosure {
  headline: string
  bullets: string[]
  destination: string
}

export interface SourceAttention {
  source_id: string
  display_name: string
  status: SourceStatus
  status_detail: string
}

export interface SourceSummary {
  total: number
  usable: number
  needs_attention: number
  excluded: number
  coverage: Coverage
}

export interface BuildPreview {
  batch_id: string
  selection_hash: string
  excerpts: Excerpt[]
  excerpt_count: number
  excerpt_chars: number
  coverage: BatchCoverage
  sources: SourceSummary
  disclosure: BuildDisclosure
  blocked_reason: string
  attention: SourceAttention[]
}

export type RunStatus = 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Run {
  id: string
  pile_id: string
  kind: string
  status: RunStatus
  stage: string
  failure_category: string
  failure_detail: string
  source_count: number
  excerpt_count: number
  excerpt_chars: number
  point_count: number
  question_count: number
  held_count: number
  started_at: string
  heartbeat_at: string
  finished_at: string | null
}

export interface BankSummary {
  points: number
  points_held: number
  questions_total: number
  questions_eligible: number
  questions_held: number
  questions_retired: number
  points_needing_re_review: number
  hold_reasons: string[]
}

export type ModelMode = 'codex' | 'host'

export interface BuildState {
  run: Run | null
  running: boolean
  coverage: Coverage
  bank: BankSummary
  /** Who does the model work (ADR 0009). */
  mode: ModelMode
  /**
   * True while the run is parked on work only the learner's ChatGPT can do.
   * The desk says so instead of showing a spinner that never stops.
   */
  awaiting_host: boolean
  pending_kinds: string[]
}

/** Cancel reports what actually happened rather than what was asked for. */
export interface CancelResult {
  stopped: boolean
  run: Run | null
}

/** Offering already-processed material to a build again. */
export interface RecheckResult {
  segments_reopened: number
  coverage: Coverage
  /** The API's own wording. Shown verbatim: it says nothing was released. */
  note: string
}

// --- generated learning material ---

export type Support = 'source_supported' | 'evidence_supported' | 'uncertain' | 'conflicting'

export interface Citation {
  source_id: string
  segment_id: string | null
  display_name: string
  confidence: Tier
  locator: string
  quote: string
}

export interface EvidenceRef {
  record_id: string
  relation: string
  quote: string
  evidence_grade: string
  pmid: string
  doi: string
  title: string
  journal: string
  published_on: string
  retracted: boolean
  corrected: boolean
  url: string
}

export interface LearningPoint {
  id: string
  pile_id: string
  generation_id: string | null
  claim: string
  detail: string
  support: Support
  support_label: string
  support_meaning: string
  evidence_grade: string
  evidence_grade_label: string
  review_state: string
  held: boolean
  hold_reason: string
  topics: string[]
  citations: Citation[]
  evidence: EvidenceRef[]
  question_count: number
  created_at: string
  updated_at: string
}

export interface RetiredMaterial {
  retired: { points: number; questions: number; attempts_kept: number }
}

// --- tutor ---

export interface Cycle {
  cycle_number: number
  position: number
  total: number
  remaining: number
  exhausted: boolean
}

export interface Anchor {
  source_id: string
  segment_id: string | null
  display_name: string
  locator: string
  quote: string
}

export interface TutorQuestion {
  id: string
  learning_point_id: string
  pile_id: string
  prompt: string
  status: string
  assessment: string
  version: number
  hold_reason: string
  created_at: string
  claim: string
  support: Support
  support_label: string
  support_meaning: string
  evidence_grade: string
  evidence_grade_label: string
  topics: string[]
  anchors: Anchor[]
  /** Only present after Grade or Show reference answer. */
  reference_answer?: string
  rubric?: string
}

export type AttemptOutcome =
  | 'correct'
  | 'partially_correct'
  | 'incorrect'
  | 'unable_to_grade'
  | 'self_assessed'

export type SelfAssessedOutcome = 'correct' | 'partially_correct' | 'incorrect'

export interface Attempt {
  id: string
  question_id: string | null
  outcome: AttemptOutcome
  graded_by: 'model' | 'self'
  feedback: string
  strengths: string
  missing_or_unsafe: string
  improved_answer: string
  uncertainty: string
  created_at: string
  question_version: number
  asked_prompt: string
}

export interface TutorSummary {
  eligible: number
  held: number
  answered_total: number
  cycle: Cycle
  reasons: string[]
  disclosure: BuildDisclosure
}

export interface TutorNext {
  question: TutorQuestion | null
  cycle: Cycle
  last_attempt: Attempt | null
  history_count: number
  empty_reason: string
}

/** Why the backend declined to grade. Not a failure, and not a grade either. */
export type GradeRefusal = 'patient_specific' | 'not_eligible' | 'stale'

/**
 * Grading answers 200 either way.
 *
 * A refusal carries `attempt: null` and a reason. It must never be shown as a
 * verdict, and the typed answer must survive it untouched.
 */
export type GradeResult =
  | { graded: true; attempt: Attempt; question: TutorQuestion }
  | { graded: false; refused: GradeRefusal; message: string; question: TutorQuestion }

// --- literature ---

export interface Topic {
  id: string
  label: string
  query: string
  enabled: boolean
  last_checked_at: string | null
  last_status: string
  last_failure: string
  consecutive_failures: number
  next_due_at: string | null
  created_at: string
  updated_at: string
  backoff_multiplier: number
}

/**
 * The result of a check, flattened.
 *
 * The API answers this route with one of several unrelated shapes depending on
 * whether a watcher is installed, so nothing here indexes into it blindly: it
 * is reduced to what can be said truthfully in every case.
 */
export interface CheckReport {
  status: string
  message: string
  unread: number | null
  /** One line per topic the check touched, when the API said anything at all. */
  outcomes: string[]
}

export type UpdateState = 'unread' | 'acknowledged' | 'dismissed'

export interface CorrectionNote {
  ref_type: string
  pmid: string
}

/**
 * One paper, as it reached your library.
 *
 * `retracted` and `corrected` are separate facts and are never merged: a
 * retraction withdraws the paper, a correction leaves it standing.
 */
export interface Update {
  id: string
  topic_id: string
  topic_label: string
  record_id: string
  state: UpdateState
  why_relevant: string
  first_seen_at: string
  checked_at: string | null
  pmid: string
  doi: string
  title: string
  journal: string
  abstract: string
  published_on: string
  publication_types: string[]
  retracted: boolean
  corrected: boolean
  /** This record *is* the retraction notice or erratum about another paper. */
  is_notice: boolean
  correction_notes: CorrectionNote[]
  url: string
  priority: string
}

export interface LiteratureSettings {
  weekly_enabled: boolean
  interval_hours: number
  enabled: boolean
  running: boolean
  provider: string
  unread: number
}

// --- flags, the cover sheet and the map ---

export interface Flag {
  id: string
  text: string
  topic: string | null
  pile_id: string | null
  status: FlagStatus
  created_at: string
  updated_at: string
  addressed_at: string | null
}

export interface ConfidenceSummary {
  confidence: Tier
  label: string
  meaning: string
  pile_count: number
  source_count: number
}

export interface CoverSheet {
  worth_a_look: LearningPoint[]
  worth_a_look_message: string
  held: { points: number; questions: number; needs_re_review: number; reasons: string[] }
  literature: { unread: number; updates: Update[]; topic_count: number; message: string }
  tutor: { eligible: number; held: number; answered_total: number; cycle: Cycle; message: string }
  recent_flags: Flag[]
  open_flag_count: number
  sources: SourceSummary
  confidences: ConfidenceSummary[]
}

/** The pile most of a topic's points came from. */
export interface TopicCluster {
  id: string
  title: string
  tier: Tier
}

/** One of the fourteen seeded specialties (migration 0004). */
export interface Specialty {
  id: string
  name: string
}

/**
 * Which specialty a topic belongs to. `owner` is a stored decision; `name` is a
 * match on the topic's own wording, offered on read and never written.
 */
export interface TopicSpecialty {
  id: string
  name: string
  assigned_by: 'owner' | 'name'
}

export interface TopicGap {
  topic: string | null
  open_flags: number
  addressed_flags: number
  last_flagged_at: string | null
  cluster: TopicCluster | null
  specialty: TopicSpecialty | null
}

export interface CoveredTopic {
  topic: string
  point_count: number
  cluster: TopicCluster | null
  specialty: TopicSpecialty | null
}

/** Where a node sat last time, in the graph's own units. */
export interface MapPosition {
  topic: string
  x: number
  y: number
}

/** Two topics one generated learning point carries; weight is how many points do. */
export interface TopicLink {
  a: string
  b: string
  weight: number
}

export interface ImprovementMap {
  topics: TopicGap[]
  covered_topics: CoveredTopic[]
  links: TopicLink[]
  specialties: Specialty[]
  positions: MapPosition[]
  confidences: ConfidenceSummary[]
  unfiled_flag_count: number
  bank: BankSummary
}

export interface WrittenFile {
  filename: string
  directory: string
  created_at: string
  byte_size: number
}

export interface BackupCheck {
  ok: boolean
  problems: string[]
}

export interface Health {
  status: string
  version: string
  schema_version: number
  database: string
  loopback_only: boolean
  /** A Codex App Server bridge exists and can report sign-in state. */
  model_bridge_configured: boolean
  /** Model turns (build, grade) can actually run. */
  model_calls_configured: boolean
  /** A literature provider is reachable and configured. */
  literature_configured: boolean
  /** Who does the model work (ADR 0009). */
  model_mode?: ModelMode
  /** One owner on this Mac, or a workspace per learner behind the gateway (ADR 0010). */
  tenancy?: 'single' | 'multi'
}

// --- the model connection (ADR 0006) ---
//
// Mirrors `apps/api/.../api/schemas.py`. Note what has no field here: an
// account email, an account identifier, and -- outside `DeviceLogin`, which is
// the one-time response to starting a sign-in -- a verification URL or code.

export type ModelState =
  | 'connecting'
  | 'signed_out'
  | 'signed_in'
  | 'rate_limited'
  | 'unavailable'

export interface UsageWindow {
  used_percent: number
  resets_at: string | null
  window_minutes: number | null
}

export interface RateLimits {
  primary: UsageWindow | null
  secondary: UsageWindow | null
  limited: boolean
  limit_reason: string | null
}

export interface ModelStatus {
  state: ModelState
  signed_in: boolean
  plan: string | null
  rate_limits: RateLimits | null
  login_pending: boolean
  detail: string
  reason: string | null
  checked_at: string
}

/**
 * Shown once and then forgotten.
 *
 * This never goes into localStorage, sessionStorage, IndexedDB or the service
 * worker cache — it lives in component state for as long as the sign-in is on
 * screen, and the tests assert exactly that.
 */
export interface DeviceLogin {
  verification_url: string
  user_code: string
  login_id: string
  status: 'pending'
}

export interface LoginCancelled {
  status: 'canceled' | 'notFound' | 'nothing_pending'
}

export const PLAN_LABEL: Record<string, string> = {
  free: 'Free',
  go: 'Go',
  plus: 'Plus',
  pro: 'Pro',
  prolite: 'Pro Lite',
  team: 'Team',
  business: 'Business',
  enterprise: 'Enterprise',
  edu: 'Education',
  unknown: 'Unknown plan'
}
