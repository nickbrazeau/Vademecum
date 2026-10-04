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
  'Tier confidence is your judgment of the material’s accuracy and usefulness for learning. It is not mastery, priority, difficulty, or evidence that a claim has been verified.'

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

export type ModelMode = 'codex' | 'host' | 'claude'

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
  /** Which records come first: practice guidelines, and these journals (PubMed title abbreviations). */
  preferred_journals: string[]
  guidelines_first: boolean
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

/** The encyclopedia and the board bank (ADR 0023). */
/** A picture from the owner's own source, placed beside the paragraph it belongs to (ADR 0026). */
export interface PageFigure {
  image_id: string
  source: string
  locator: string
  width: number
  height: number
}

export interface PageParagraph {
  text: string
  point_ids: string[]
  figures?: PageFigure[]
}

export interface PageSection {
  heading: string
  paragraphs: PageParagraph[]
}

export interface PageSource {
  source_id: string
  display_name: string
  locator: string
  quote: string
}

export interface PageCitation {
  id: string
  claim: string
  support: string
  support_label: string
  held: boolean
  sources: PageSource[]
}

export interface PageRecord {
  record_id: string
  cited: boolean
  pmid: string
  doi: string
  title: string
  journal: string
  published_on: string | null
  url: string
  priority: string
  retracted: boolean
  corrected: boolean
}

export interface EncyclopediaEntry {
  id: string
  topic: string
  title: string
  specialty_id: string | null
  summary: string
  sections: PageSection[]
  point_count: number
  question_count: number
  status: string
  status_detail: string
  version: number
  compiled_at: string | null
  literature_checked_at: string | null
  literature_note: string
  citations: PageCitation[]
  literature: PageRecord[]
  /** The owner's own edit, in Markdown (ADR 0026). */
  body_md: string
  /** What the page's file holds: the edit, or the compiled page as Markdown. */
  markdown: string
  edited: boolean
  edit_outdated: boolean
  edited_at: string | null
}

/** The Socratic tutor and the podcast generator (ADR 0025). */
export interface SocraticTurn {
  role: 'tutor' | 'learner'
  text: string
  probe: string
}

export interface SocraticAssessment {
  differential: string
  treatment: string
  knowledge_strengths: string
  knowledge_gaps: string[]
  summary: string
}

export interface SocraticSession {
  id: string
  entry_id: string | null
  topic: string
  title: string
  mode: string
  status: 'open' | 'done' | 'abandoned'
  transcript: SocraticTurn[]
  assessment: SocraticAssessment
  exchanges: number
  created_at: string
  finished_at: string | null
}

export interface SocraticOverview {
  open: SocraticSession | null
  recent: SocraticSession[]
  mode: string
  can_answer_here: boolean
  note: string
  disclosure: string
}

export interface SocraticReply {
  session: SocraticSession
  note: string
  gaps_filed: number
}

export interface PodcastLine {
  speaker: 'A' | 'B'
  text: string
}

export interface PodcastEpisode {
  id: string
  title: string
  status: 'draft' | 'scripted' | 'rendered' | 'failed'
  status_detail: string
  entry_ids: string[]
  script: PodcastLine[]
  takeaways: string[]
  voices: Record<string, string>
  has_audio: boolean
  sources: PodcastSource[]
  audio_bytes: number
  duration_seconds: number
  words: number
  created_at: string
  note?: string
}

export interface PodcastList {
  episodes: PodcastEpisode[]
  can_write: boolean
  can_render: boolean
  note: string
  disclosure: string
}

export interface PodcastVoice {
  name: string
  locale: string
}

/** Flashcards and preferences (ADR 0024). */
export interface Flashcard {
  id: string
  entry_id: string
  topic: string
  title: string
  front: string
  back: string
  point_ids: string[]
}

export interface FlashcardDraw {
  card: Flashcard | null
  reasons: string[]
  citations: PageCitation[]
  deck: number
  empty_reason: string
}

export interface FlashcardOverview {
  eligible: number
  held: number
  total: number
  reviews_total: number
  reviews_again: number
  improvement: { flagged_topics: number; areas_below: number; pages_missed: number }
}

export interface TabChoice {
  name: string
  label: string
  fixed: boolean
}

export interface Preferences {
  visible_tabs: string[]
  /** Every tab in the owner's order: Today first, Settings last (ADR 0026). */
  order: string[]
  tabs: TabChoice[]
  daily_goal: number
}

export interface Dissection {
  status: string
  phase: string
  pile_id: string
  pile_title: string
  coverage: Coverage | null
  started_at: string | null
  consent_at: string | null
  completed_at: string | null
  batches_done: number
  points_built: number
  pages_compiled: number
  questions_written: number
  failures: number
  last_error: string
  last_activity_at: string | null
  next_retry_at: string | null
  running: boolean
  can_run: boolean
  blocked_reason: string
  encyclopedia: EncyclopediaCounts
  disclosure: string
}

export interface EncyclopediaCounts {
  entries: number
  stale: number
  questions_eligible: number
  questions_held: number
  questions_total: number
}

export interface EncyclopediaRefresh {
  at: string
  reason: string
  pages: { compiled: number; failed: number; remaining: number }
  questions: { entries: number; written: number; held: number; failed: number }
}

export interface EncyclopediaList {
  entries: EncyclopediaEntry[]
  /** The subjects pages are shelved under, in order. */
  specialties: Specialty[]
  counts: EncyclopediaCounts
  can_compile: boolean
  running: boolean
  last_refresh: EncyclopediaRefresh | null
  note: string
  disclosure: string
}

export interface EncyclopediaPage {
  page: EncyclopediaEntry | null
  counts: EncyclopediaCounts
  message: string
}

export interface BoardOption {
  letter: string
  text: string
}

export interface BoardQuestion {
  id: string
  entry_id: string
  topic: string
  title: string
  stem: string
  options: BoardOption[]
  objective: string
  status: string
  point_ids: string[]
  /** Present only once answered. */
  answer_index?: number
  answer_letter?: string
  explanation?: string
}

export interface BoardAttempt {
  id: string
  question_id: string | null
  chosen_index: number
  chosen_letter: string
  correct: boolean
  created_at: string
}

export interface BoardNext {
  question: BoardQuestion | null
  cycle: Cycle
  last_attempt: BoardAttempt | null
  history_count: number
  empty_reason: string
}

export interface BoardAnswer {
  attempt: BoardAttempt
  question: BoardQuestion
  citations: PageCitation[]
}

export interface BoardOverview {
  eligible: number
  held: number
  total: number
  pages: number
  answered_total: number
  answered_correct: number
  cycle: Cycle
}

/** The feedback of 4 October (ADR 0026). */
export interface Dashboard {
  /** The owner's own number of reviews that makes a day enough (ADR 0026). */
  daily_goal: number
  goal_met: boolean
  remaining_today: number
  days_in_a_row: number
  longest_run: number
  reviewed_today_already: boolean
  today: { question: number; card: number; socratic: number; page: number }
  today_total: number
  week_total: number
  all_time_total: number
  history: { day: string; count: number }[]
}

export interface TopicTally {
  topic: string
  answered: number
  correct: number
}

export interface Scorecard {
  board: { answered: number; correct: number; last_7_days: { answered: number; correct: number } }
  weakest_topics: TopicTally[]
  strongest_topics: TopicTally[]
  open_answers: { answered: number; correct: number }
  flashcards: { reviewed: number; got_it: number }
  socratic: { sessions: number; exchanges: number }
  dashboard: Dashboard
}

export type Standing = 'weak' | 'mixed' | 'strong'

export interface TopicStrength {
  topic: string
  specialty_id: string | null
  score: number
  label: Standing
  reasons: string[]
  evidence: { missed_questions: string[]; flags: string[]; exam_areas: { standing: string; quote: string }[] }
}

export interface SpecialtyStrength {
  id: string
  name: string
  score: number
  label: Standing
  weak: number
  strong: number
  topics: TopicStrength[]
}

export interface Strengths {
  specialties: SpecialtyStrength[]
  topic_count: number
}

export interface PodcastSource {
  kind: string
  title: string
  journal?: string
  year?: string
  pmid?: string
  entry_id?: string
}

export interface CoverSheet {
  dashboard: Dashboard
  new_cases: CaseEntry[]
  page: EncyclopediaEntry | null
  encyclopedia: EncyclopediaCounts & { message: string }
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

/** One content area from an exam report (ADR 0020): what the report said, located. */
export interface ReportArea {
  topic: string
  specialty_id: string | null
  standing: 'below' | 'at' | 'above'
  note: string
  quote: string
  report_id: string
  report: string
  reported_at: string
}

export interface ExamArea {
  id: string
  report_id: string
  ordinal: number
  topic: string
  specialty_id: string | null
  standing: 'below' | 'at' | 'above'
  quote: string
  note: string
  created_at: string
}

export interface ExamReport {
  id: string
  display_name: string
  media_type: string
  byte_size: number
  status: 'uploaded' | 'parsed' | 'failed'
  status_detail: string
  created_at: string
  parsed_at: string | null
  text_chars: number
  areas: ExamArea[]
  note?: string
}

export interface ImprovementMap {
  topics: TopicGap[]
  covered_topics: CoveredTopic[]
  links: TopicLink[]
  report_areas: ReportArea[]
  specialties: Specialty[]
  positions: MapPosition[]
  confidences: ConfidenceSummary[]
  unfiled_flag_count: number
  bank: BankSummary
  /** Whether this node files flags itself (the Mac), or leaves it to the Mac (the cloud copy). */
  can_file_flags: boolean
  filing_note: string
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


/** Builds on a timer (ADR 0018): the Mac working through the piles by itself. */
export interface ScheduledPileOutcome {
  pile_id: string
  title: string
  batches: number
  points: number
  status: string
  detail: string
}

export interface ScheduledRun {
  at: string
  reason: string
  ran: boolean
  note: string
  piles: ScheduledPileOutcome[]
}

export interface BuildSchedule {
  enabled: boolean
  times: string[]
  batches_per_run: number
  consent_at: string | null
  model_mode: ModelMode
  can_run: boolean
  blocked_reason: string
  running: boolean
  next_run_at: string | null
  last_run: ScheduledRun | null
  disclosure: string
}

/** The Case Series hub (ADR 0022): other people's cases, by title and link. */
export type CaseSeriesId = 'nejm_cpc' | 'nejm_cps' | 'cps' | 'curbsiders'

export interface CasePoint {
  point: string
  /** The verbatim words in the publisher's notes the point rests on. */
  quote: string
}

export interface CaseEntry {
  id: string
  series: string
  series_name: string
  series_short: string
  publisher: string
  subseries: string
  external_id: string
  title: string
  url: string
  credit: string
  published_on: string | null
  status: 'new' | 'synthesised' | 'failed'
  status_detail: string
  one_liner: string
  points: CasePoint[]
  think_first: string[]
  specialty_id: string | null
  synthesised_at: string | null
  first_seen_at: string
  snippet: string
}

export interface CaseCatalogueEntry {
  id: string
  name: string
  short: string
  publisher: string
  home: string
}

export interface CaseCounts {
  total: number
  pending: number
  by_series: Record<string, number>
}

export interface CaseList {
  entries: CaseEntry[]
  catalogue: CaseCatalogueEntry[]
  counts: CaseCounts
  credit: string
}

export interface CaseRefresh {
  at: string
  reason: string
  fetched: Record<string, { new: number; error: string }>
  synthesised: number
  failed: number
}

export interface CaseSettings {
  enabled: boolean
  interval_hours: number
  series: Record<string, boolean>
  fetches_here: boolean
  can_synthesise: boolean
  running: boolean
  last_refresh: CaseRefresh | null
  counts: CaseCounts
  catalogue: CaseCatalogueEntry[]
  note: string
  disclosure: string
  credit: string
}
