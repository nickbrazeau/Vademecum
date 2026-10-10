/**
 * The boundary where a wire payload becomes something safe to render.
 *
 * One rule: after this file, no array, object, number or string that the
 * interface reads is ever absent. A component that calls `.map` on a field the
 * backend happened not to send is a blank page, and a blank page is the failure
 * mode this whole product is built to avoid (ADR 0001) — so the defaulting
 * happens once, here, instead of being remembered at three hundred call sites.
 *
 * Nothing is invented. Absent means empty, zero, or `null`; it never means a
 * plausible-looking stand-in.
 */

import type {
  TodayRecall,
  KnowledgeState,
  LearnerModel,
  LearnerStep,
  LearnerStepKind,
  LearnerUnit,
  MapPage,
  Anchor,
  Attempt,
  AttemptOutcome,
  BankSummary,
  BatchCoverage,
  BuildDisclosure,
  BuildPreview,
  BuildState,
  CancelResult,
  CheckReport,
  Citation,
  ConfidenceSummary,
  CorrectionNote,
  Coverage,
  CoverageUnit,
  CoverSheet,
  CoveredTopic,
  Cycle,
  EvidenceRef,
  Excerpt,
  Flag,
  GradeRefusal,
  GradeResult,
  ImprovementMap,
  LearningItem,
  LearningPoint,
  LiteratureSettings,
  Pile,
  RecheckResult,
  Run,
  RunStatus,
  Segment,
  Source,
  SourceAttention,
  SourceStatus,
  SourceSummary,
  Support,
  Tier,
  Topic,
  TopicSuggestion,
  TopicGap,
  TutorNext,
  TutorQuestion,
  Update,
  UpdateState,
  UploadReport,
  UploadResult,
  WrittenFile,
  TopicCluster,
  TopicLink,
  Specialty,
  TopicSpecialty,
  MapPosition,
  CaseCatalogueEntry,
  CaseCounts,
  CaseEntry,
  CaseList,
  CaseSettings,
  BoardAnswer,
  BoardAttempt,
  BoardNext,
  BoardQuestion,
  Dashboard,
  Scorecard,
  Standing,
  Strengths,
  TopicTally,
  Dissection,
  FlashcardDraw,
  Preferences,
  PodcastEpisode,
  PodcastList,
  PodcastVoice,
  SocraticOverview,
  SocraticReply,
  SocraticSession,
  EncyclopediaCounts,
  EncyclopediaEntry,
  EncyclopediaList,
  EncyclopediaPage,
  EncyclopediaRefresh,
  PageCitation
} from './types'

type Dict = Record<string, unknown>

function obj(value: unknown): Dict {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Dict)
    : {}
}

function arr(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

/** A nullable string field: a missing value and an explicit null are the same. */
function nullableStr(value: unknown): string | null {
  return typeof value === 'string' && value !== '' ? value : null
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function bool(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function strings(value: unknown): string[] {
  return arr(value).filter((entry): entry is string => typeof entry === 'string')
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback
}

function list<T>(value: unknown, each: (entry: Dict) => T): T[] {
  return arr(value).map((entry) => each(obj(entry)))
}

const TIERS = ['low', 'mid', 'high'] as const
const TIER_WORD: Record<Tier, string> = { low: 'Low', mid: 'Medium', high: 'High' }

function tier(value: unknown): Tier {
  return oneOf(value, TIERS, 'mid')
}

const SUPPORTS = ['source_supported', 'evidence_supported', 'uncertain', 'conflicting'] as const
const SOURCE_STATUSES = ['stored', 'extracted', 'needs_ocr', 'encrypted', 'unreadable'] as const
const RUN_STATUSES = ['running', 'succeeded', 'failed', 'cancelled'] as const
const UPDATE_STATES = ['unread', 'acknowledged', 'dismissed'] as const
const OUTCOMES = [
  'correct',
  'partially_correct',
  'incorrect',
  'unable_to_grade',
  'self_assessed'
] as const

// --- coverage ----------------------------------------------------------------

/**
 * Coverage, whichever way the backend counted it.
 *
 * Characters is the real unit. One older summary still reports passages, so the
 * unit is carried alongside the number rather than assumed — a passage count
 * printed as characters would be a quiet lie about how much is left.
 */
export function coverage(value: unknown): Coverage {
  const raw = obj(value)
  const passages = raw['segments_total'] !== undefined && raw['chars_total'] === undefined
  const unit: CoverageUnit = passages ? 'passages' : 'characters'
  const total = num(passages ? raw['segments_total'] : raw['chars_total'])
  const covered = Math.min(total, num(passages ? raw['segments_covered'] : raw['chars_covered']))
  const complete = bool(raw['complete'], total > 0 && covered >= total)
  // Floored below 100 so "100%" can never appear beside "not complete".
  const derived = total <= 0 ? 0 : complete ? 100 : Math.min(99, Math.floor((100 * covered) / total))
  return { total, covered, percent: num(raw['percent'], derived), complete, unit }
}

export function batchCoverage(value: unknown): BatchCoverage {
  const raw = obj(value)
  const total = num(raw['chars_total'])
  const covered = num(raw['chars_covered'])
  const inBatch = num(raw['chars_in_batch'])
  return {
    chars_total: total,
    chars_covered: covered,
    chars_in_batch: inBatch,
    chars_remaining_after: num(raw['chars_remaining_after'], Math.max(0, total - covered - inBatch)),
    excerpts_in_batch: num(raw['excerpts_in_batch']),
    complete: bool(raw['complete'], total > 0 && covered >= total),
    complete_after: bool(raw['complete_after'], total > 0 && covered + inBatch >= total)
  }
}

// --- piles, sources, segments ------------------------------------------------

export function pile(value: unknown): Pile {
  const raw = obj(value)
  const t = tier(raw['tier'])
  return {
    id: str(raw['id']),
    title: str(raw['title']),
    tier: t,
    confidence_label: str(raw['confidence_label'], TIER_WORD[t]),
    description: str(raw['description']),
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at']),
    item_count: num(raw['item_count']),
    source_count: num(raw['source_count']),
    point_count: num(raw['point_count']),
    question_count: num(raw['question_count']),
    coverage: coverage(raw['coverage'])
  }
}

export const piles = (value: unknown): Pile[] => list(value, pile)

export function item(value: unknown): LearningItem {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    pile_id: str(raw['pile_id']),
    title: str(raw['title']),
    body: str(raw['body']),
    source: str(raw['source']),
    content_hash: str(raw['content_hash']),
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at'])
  }
}

export const items = (value: unknown): LearningItem[] => list(value, item)

export function source(value: unknown): Source {
  const raw = obj(value)
  const t = tier(raw['confidence'])
  const extraction = obj(raw['extraction_coverage'])
  return {
    id: str(raw['id']),
    pile_id: str(raw['pile_id']),
    display_name: str(raw['display_name'], 'Untitled file'),
    media_type: str(raw['media_type']),
    byte_size: num(raw['byte_size']),
    sha256: str(raw['sha256']),
    confidence: t,
    confidence_label: str(raw['confidence_label'], TIER_WORD[t]),
    status: oneOf<SourceStatus>(raw['status'], SOURCE_STATUSES, 'stored'),
    status_detail: str(raw['status_detail']),
    unit_kind: str(raw['unit_kind']),
    unit_count: num(raw['unit_count']),
    char_count: num(raw['char_count']),
    excluded: bool(raw['excluded']),
    point_count: num(raw['point_count']),
    coverage: coverage(raw['coverage']),
    extraction_coverage: {
      units_total: num(extraction['units_total']),
      units_with_text: num(extraction['units_with_text']),
      units_image_only: num(extraction['units_image_only']),
      units_failed: num(extraction['units_failed']),
      units_dropped: num(extraction['units_dropped']),
      document_truncated: bool(extraction['document_truncated'])
    },
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at']),
    extracted_at: nullableStr(raw['extracted_at'])
  }
}

export const sources = (value: unknown): Source[] => list(value, source)

export function segment(value: unknown): Segment {
  const raw = obj(value)
  const chars = num(raw['char_count'])
  const upto = num(raw['covered_upto'])
  return {
    id: str(raw['id']),
    source_id: str(raw['source_id']),
    ordinal: num(raw['ordinal']),
    kind: str(raw['kind']),
    locator: str(raw['locator']),
    text: str(raw['text']),
    char_count: chars,
    covered_upto: upto,
    fully_covered: bool(raw['fully_covered'], chars > 0 && upto >= chars)
  }
}

export const segments = (value: unknown): Segment[] => list(value, segment)

export function uploadReport(value: unknown): UploadReport {
  const raw = obj(value)
  return {
    accepted: num(raw['accepted']),
    rejected: num(raw['rejected']),
    results: list(raw['results'], (entry): UploadResult => {
      const stored = entry['source']
      return {
        filename: str(entry['filename'], 'Untitled file'),
        outcome: oneOf(entry['outcome'], ['stored', 'duplicate', 'rejected'] as const, 'rejected'),
        // Only a rejected file legitimately has no source row.
        source: stored === null || stored === undefined ? null : source(stored),
        message: str(entry['message']),
        warnings: strings(entry['warnings'])
      }
    })
  }
}

export function sourceSummary(value: unknown): SourceSummary {
  const raw = obj(value)
  return {
    total: num(raw['total']),
    usable: num(raw['usable']),
    needs_attention: num(raw['needs_attention']),
    excluded: num(raw['excluded']),
    coverage: coverage(raw['coverage'])
  }
}

// --- building ----------------------------------------------------------------

function excerpt(raw: Dict): Excerpt {
  const t = tier(raw['confidence'])
  const locator = str(raw['locator'])
  const text = str(raw['text'])
  return {
    source_id: str(raw['source_id']),
    segment_id: str(raw['segment_id']),
    display_name: str(raw['display_name'], 'Untitled file'),
    confidence: t,
    confidence_label: str(raw['confidence_label'], TIER_WORD[t]),
    locator,
    range_label: str(raw['range_label'], locator),
    text,
    start: num(raw['start']),
    end: num(raw['end']),
    char_count: num(raw['char_count'], text.length)
  }
}

export function disclosure(value: unknown): BuildDisclosure {
  const raw = obj(value)
  return {
    headline: str(raw['headline']),
    bullets: strings(raw['bullets']),
    destination: str(raw['destination'])
  }
}

export function buildPreview(value: unknown): BuildPreview {
  const raw = obj(value)
  const chosen = list(raw['excerpts'], excerpt)
  return {
    batch_id: str(raw['batch_id']),
    selection_hash: str(raw['selection_hash']),
    excerpts: chosen,
    excerpt_count: num(raw['excerpt_count'], chosen.length),
    excerpt_chars: num(
      raw['excerpt_chars'],
      chosen.reduce((total, one) => total + one.char_count, 0)
    ),
    coverage: batchCoverage(raw['coverage']),
    sources: sourceSummary(raw['sources']),
    disclosure: disclosure(raw['disclosure']),
    blocked_reason: str(raw['blocked_reason']),
    attention: list(
      raw['attention'],
      (entry): SourceAttention => ({
        source_id: str(entry['source_id']),
        display_name: str(entry['display_name'], 'Untitled file'),
        status: oneOf<SourceStatus>(entry['status'], SOURCE_STATUSES, 'stored'),
        status_detail: str(entry['status_detail'])
      })
    )
  }
}

export function run(value: unknown): Run {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    pile_id: str(raw['pile_id']),
    kind: str(raw['kind']),
    status: oneOf<RunStatus>(raw['status'], RUN_STATUSES, 'running'),
    stage: str(raw['stage']),
    failure_category: str(raw['failure_category']),
    failure_detail: str(raw['failure_detail']),
    source_count: num(raw['source_count']),
    excerpt_count: num(raw['excerpt_count']),
    excerpt_chars: num(raw['excerpt_chars']),
    point_count: num(raw['point_count']),
    question_count: num(raw['question_count']),
    held_count: num(raw['held_count']),
    started_at: str(raw['started_at']),
    heartbeat_at: str(raw['heartbeat_at']),
    finished_at: nullableStr(raw['finished_at'])
  }
}

function maybeRun(value: unknown): Run | null {
  return value === null || value === undefined ? null : run(value)
}

export const runs = (value: unknown): Run[] => list(value, run)

export const startedRun = (value: unknown): { run: Run } => ({ run: run(obj(value)['run']) })

export function bank(value: unknown): BankSummary {
  const raw = obj(value)
  return {
    points: num(raw['points']),
    points_held: num(raw['points_held']),
    questions_total: num(raw['questions_total']),
    questions_eligible: num(raw['questions_eligible']),
    questions_held: num(raw['questions_held']),
    questions_retired: num(raw['questions_retired']),
    points_needing_re_review: num(raw['points_needing_re_review']),
    hold_reasons: strings(raw['hold_reasons'])
  }
}

export function buildState(value: unknown): BuildState {
  const raw = obj(value)
  const latest = maybeRun(raw['run'])
  const kinds = Array.isArray(raw['pending_kinds']) ? raw['pending_kinds'] : []
  return {
    run: latest,
    running: bool(raw['running'], latest?.status === 'running'),
    coverage: coverage(raw['coverage']),
    bank: bank(raw['bank']),
    mode: raw['mode'] === 'host' ? 'host' : 'codex',
    awaiting_host: bool(raw['awaiting_host'], false),
    pending_kinds: kinds.filter((kind): kind is string => typeof kind === 'string')
  }
}

export function cancelResult(value: unknown): CancelResult {
  const raw = obj(value)
  // `stopped` is the atomic fact from the API. Absent is not "it worked".
  return { stopped: bool(raw['stopped']), run: maybeRun(raw['run']) }
}

export function recheckResult(value: unknown): RecheckResult {
  const raw = obj(value)
  return {
    segments_reopened: num(raw['segments_reopened']),
    coverage: coverage(raw['coverage']),
    note: str(raw['note'])
  }
}

// --- generated material ------------------------------------------------------

function citation(raw: Dict): Citation {
  return {
    source_id: str(raw['source_id']),
    segment_id: nullableStr(raw['segment_id']),
    display_name: str(raw['display_name'], 'Untitled file'),
    confidence: tier(raw['confidence']),
    locator: str(raw['locator']),
    quote: str(raw['quote'])
  }
}

function evidenceRef(raw: Dict): EvidenceRef {
  return {
    record_id: str(raw['record_id']),
    relation: str(raw['relation']),
    quote: str(raw['quote']),
    evidence_grade: str(raw['evidence_grade']),
    pmid: str(raw['pmid']),
    doi: str(raw['doi']),
    title: str(raw['title'], 'Untitled paper'),
    journal: str(raw['journal']),
    published_on: str(raw['published_on']),
    retracted: bool(raw['retracted']),
    corrected: bool(raw['corrected']),
    url: str(raw['url'])
  }
}

export function point(value: unknown): LearningPoint {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    pile_id: str(raw['pile_id']),
    generation_id: nullableStr(raw['generation_id']),
    claim: str(raw['claim']),
    detail: str(raw['detail']),
    support: oneOf<Support>(raw['support'], SUPPORTS, 'uncertain'),
    support_label: str(raw['support_label']),
    support_meaning: str(raw['support_meaning']),
    evidence_grade: str(raw['evidence_grade']),
    evidence_grade_label: str(raw['evidence_grade_label']),
    review_state: str(raw['review_state']),
    held: bool(raw['held']),
    hold_reason: str(raw['hold_reason']),
    topics: strings(raw['topics']),
    citations: list(raw['citations'], citation),
    evidence: list(raw['evidence'], evidenceRef),
    question_count: num(raw['question_count']),
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at'])
  }
}

export const points = (value: unknown): LearningPoint[] => list(value, point)

// --- tutor -------------------------------------------------------------------

export function cycle(value: unknown): Cycle {
  const raw = obj(value)
  const total = num(raw['total'])
  const position = num(raw['position'])
  return {
    cycle_number: num(raw['cycle_number'], 1),
    position,
    total,
    remaining: num(raw['remaining'], Math.max(0, total - position)),
    exhausted: bool(raw['exhausted'])
  }
}

function anchor(raw: Dict): Anchor {
  return {
    source_id: str(raw['source_id']),
    segment_id: nullableStr(raw['segment_id']),
    display_name: str(raw['display_name'], 'Untitled file'),
    locator: str(raw['locator']),
    quote: str(raw['quote'])
  }
}

export function question(value: unknown): TutorQuestion {
  const raw = obj(value)
  const shaped: TutorQuestion = {
    id: str(raw['id']),
    learning_point_id: str(raw['learning_point_id']),
    pile_id: str(raw['pile_id']),
    prompt: str(raw['prompt']),
    status: str(raw['status']),
    assessment: str(raw['assessment']),
    version: num(raw['version'], 1),
    hold_reason: str(raw['hold_reason']),
    created_at: str(raw['created_at']),
    claim: str(raw['claim']),
    support: oneOf<Support>(raw['support'], SUPPORTS, 'uncertain'),
    support_label: str(raw['support_label']),
    support_meaning: str(raw['support_meaning']),
    evidence_grade: str(raw['evidence_grade']),
    evidence_grade_label: str(raw['evidence_grade_label']),
    topics: strings(raw['topics']),
    anchors: list(raw['anchors'], anchor)
  }
  // Present only once the owner has earned them: after Grade or Reveal. The
  // key stays absent otherwise so "revealed" is never inferred from an "".
  if (typeof raw['reference_answer'] === 'string') shaped.reference_answer = raw['reference_answer']
  if (typeof raw['rubric'] === 'string') shaped.rubric = raw['rubric']
  return shaped
}

function maybeQuestion(value: unknown): TutorQuestion | null {
  return value === null || value === undefined ? null : question(value)
}

export function attempt(value: unknown): Attempt {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    question_id: nullableStr(raw['question_id']),
    outcome: oneOf<AttemptOutcome>(raw['outcome'], OUTCOMES, 'unable_to_grade'),
    // Anything the API did not explicitly call a model grade is shown as the
    // owner's own judgement. The safe default is the weaker claim.
    graded_by: raw['graded_by'] === 'model' ? 'model' : 'self',
    feedback: str(raw['feedback']),
    strengths: str(raw['strengths']),
    missing_or_unsafe: str(raw['missing_or_unsafe']),
    improved_answer: str(raw['improved_answer']),
    uncertainty: str(raw['uncertainty']),
    created_at: str(raw['created_at']),
    question_version: num(raw['question_version'], 1),
    asked_prompt: str(raw['asked_prompt'])
  }
}

export function tutorNext(value: unknown): TutorNext {
  const raw = obj(value)
  return {
    question: maybeQuestion(raw['question']),
    cycle: cycle(raw['cycle']),
    last_attempt: raw['last_attempt'] == null ? null : attempt(raw['last_attempt']),
    history_count: num(raw['history_count']),
    empty_reason: str(raw['empty_reason'])
  }
}

const REFUSALS = ['patient_specific', 'not_eligible', 'stale'] as const

/**
 * Grading answers 200 whether or not it graded.
 *
 * A body with `attempt: null` is a refusal, not a verdict, and the two are
 * separated here so no caller can accidentally render an empty attempt as one.
 */
export function gradeResult(value: unknown): GradeResult {
  const raw = obj(value)
  const shown = question(raw['question'])
  const refusedAs = raw['refused']
  if (raw['attempt'] == null || typeof refusedAs === 'string') {
    return {
      graded: false,
      refused: oneOf<GradeRefusal>(refusedAs, REFUSALS, 'not_eligible'),
      message: str(raw['message'], 'The model was not asked to grade this answer.'),
      question: shown
    }
  }
  return { graded: true, attempt: attempt(raw['attempt']), question: shown }
}

export const revealed = (value: unknown): { question: TutorQuestion } => ({
  question: question(obj(value)['question'])
})

export const recordedAttempt = (value: unknown): { attempt: Attempt } => ({
  attempt: attempt(obj(value)['attempt'])
})

// --- literature --------------------------------------------------------------

export function topic(value: unknown): Topic {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    label: str(raw['label'], 'Untitled topic'),
    query: str(raw['query']),
    enabled: bool(raw['enabled'], true),
    last_checked_at: nullableStr(raw['last_checked_at']),
    last_status: str(raw['last_status']),
    last_failure: str(raw['last_failure']),
    consecutive_failures: num(raw['consecutive_failures']),
    next_due_at: nullableStr(raw['next_due_at']),
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at']),
    backoff_multiplier: num(raw['backoff_multiplier'], 1)
  }
}

export const topics = (value: unknown): Topic[] => list(value, topic)

export const topicSuggestions = (value: unknown): TopicSuggestion[] => list(obj(value)['suggestions'], (raw) => ({
  topic: str(raw['topic']), query: str(raw['query']),
  point_count: num(raw['point_count']), supported_count: num(raw['supported_count']),
  already_watched: bool(raw['already_watched'])
}))

export function update(value: unknown): Update {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    rating: raw['rating'] === 1 ? 1 : raw['rating'] === -1 ? -1 : 0,
    topic_id: str(raw['topic_id']),
    topic_label: str(raw['topic_label']),
    record_id: str(raw['record_id']),
    state: oneOf<UpdateState>(raw['state'], UPDATE_STATES, 'unread'),
    why_relevant: str(raw['why_relevant']),
    first_seen_at: str(raw['first_seen_at']),
    checked_at: nullableStr(raw['checked_at']),
    pmid: str(raw['pmid']),
    doi: str(raw['doi']),
    title: str(raw['title'], 'Untitled paper'),
    journal: str(raw['journal']),
    abstract: str(raw['abstract']),
    published_on: str(raw['published_on']),
    publication_types: strings(raw['publication_types']),
    // Two independent facts. A retraction withdraws a paper; a correction
    // leaves it standing. Merging them would misreport both.
    retracted: bool(raw['retracted']),
    corrected: bool(raw['corrected']),
    is_notice: bool(raw['is_notice']),
    correction_notes: list(
      raw['correction_notes'],
      (entry): CorrectionNote => ({ ref_type: str(entry['ref_type']), pmid: str(entry['pmid']) })
    ),
    url: str(raw['url']),
    priority: str(raw['priority'], 'other')
  }
}

export const updates = (value: unknown): Update[] => list(value, update)

export function literatureSettings(value: unknown): LiteratureSettings {
  const raw = obj(value)
  return {
    weekly_enabled: bool(raw['weekly_enabled']),
    interval_hours: num(raw['interval_hours'], 168),
    preferred_journals: Array.isArray(raw['preferred_journals']) ? raw['preferred_journals'].map(String) : [],
    suggested_journals: Array.isArray(raw['suggested_journals']) ? raw['suggested_journals'].map(String) : [],
    guidelines_first: raw['guidelines_first'] !== false,
    enabled: bool(raw['enabled']),
    running: bool(raw['running']),
    provider: str(raw['provider']),
    unread: num(raw['unread'])
  }
}

/**
 * The check route answers with one of several unrelated shapes depending on
 * whether a watcher is installed. Rather than guess which one arrived, this
 * reduces all of them to the few things that are true in every case.
 */
export function checkReport(value: unknown): CheckReport {
  const raw = obj(value)
  const results = arr(raw['results'])
  const checks = arr(raw['checks'])
  const outcomes = [...results, ...checks].map((entry) => {
    const one = obj(entry)
    const parts = [
      str(one['topic_id'] ?? one['id'], 'a topic'),
      str(one['status'], 'no status reported'),
      str(one['reason'] ?? one['failure_category'])
    ].filter((part) => part !== '')
    return parts.join(' · ')
  })
  return {
    status: str(raw['status'], outcomes.length > 0 ? 'ok' : ''),
    message: str(raw['message']),
    unread: typeof raw['unread'] === 'number' ? raw['unread'] : null,
    outcomes
  }
}

// --- flags, cover sheet, map, files -------------------------------------------

export function flag(value: unknown): Flag {
  const raw = obj(value)
  return {
    id: str(raw['id']),
    text: str(raw['text']),
    topic: nullableStr(raw['topic']),
    pile_id: nullableStr(raw['pile_id']),
    status: oneOf(raw['status'], ['open', 'addressed'] as const, 'open'),
    created_at: str(raw['created_at']),
    updated_at: str(raw['updated_at']),
    addressed_at: nullableStr(raw['addressed_at'])
  }
}

export const flags = (value: unknown): Flag[] => list(value, flag)

function confidences(value: unknown): ConfidenceSummary[] {
  return list(value, (raw): ConfidenceSummary => {
    const t = tier(raw['confidence'])
    return {
      confidence: t,
      label: str(raw['label'], TIER_WORD[t]),
      meaning: str(raw['meaning']),
      pile_count: num(raw['pile_count']),
      source_count: num(raw['source_count'])
    }
  })
}

export function coverSheet(value: unknown): CoverSheet {
  const raw = obj(value)
  const held = obj(raw['held'])
  const literature = obj(raw['literature'])
  const tutor = obj(raw['tutor'])
  const encyclopedia = obj(raw['encyclopedia'])
  return {
    dashboard: dashboard(raw['dashboard']),
    new_cases: arr(raw['new_cases']).map(caseEntry),
    page: raw['page'] && typeof raw['page'] === 'object' ? encyclopediaEntry(raw['page']) : null,
    encyclopedia: { ...encyclopediaCounts(encyclopedia), message: str(encyclopedia['message']) },
    recall: todayRecall(raw['recall']),
    held: {
      points: num(held['points']),
      questions: num(held['questions']),
      needs_re_review: num(held['needs_re_review']),
      reasons: strings(held['reasons'])
    },
    literature: {
      unread: num(literature['unread']),
      updates: updates(literature['updates']),
      topic_count: num(literature['topic_count']),
      message: str(literature['message'])
    },
    tutor: {
      eligible: num(tutor['eligible']),
      held: num(tutor['held']),
      answered_total: num(tutor['answered_total']),
      cycle: cycle(tutor['cycle']),
      message: str(tutor['message'])
    },
    recent_flags: flags(raw['recent_flags']),
    open_flag_count: num(raw['open_flag_count']),
    sources: sourceSummary(raw['sources']),
    confidences: confidences(raw['confidences'])
  }
}

function topicCluster(value: unknown): TopicCluster | null {
  if (value === null || typeof value !== 'object') return null
  const raw = obj(value)
  const id = str(raw['id'], '')
  if (id === '') return null
  return { id, title: str(raw['title'], ''), tier: tier(raw['tier']) }
}

function topicSpecialty(value: unknown): TopicSpecialty | null {
  if (value === null || typeof value !== 'object') return null
  const raw = obj(value)
  const id = str(raw['id'], '')
  if (id === '') return null
  return {
    id,
    name: str(raw['name'], id),
    assigned_by: raw['assigned_by'] === 'owner' ? 'owner' : 'name'
  }
}

export function improvementMap(value: unknown): ImprovementMap {
  const raw = obj(value)
  return {
    topics: list(
      raw['topics'],
      (entry): TopicGap => ({
        topic: nullableStr(entry['topic']),
        open_flags: num(entry['open_flags']),
        addressed_flags: num(entry['addressed_flags']),
        last_flagged_at: nullableStr(entry['last_flagged_at']),
        cluster: topicCluster(entry['cluster']),
        specialty: topicSpecialty(entry['specialty'])
      })
    ),
    covered_topics: list(
      raw['covered_topics'],
      (entry): CoveredTopic => ({
        topic: str(entry['topic'], 'Not filed yet'),
        point_count: num(entry['point_count']),
        cluster: topicCluster(entry['cluster']),
        specialty: topicSpecialty(entry['specialty'])
      })
    ),
    report_areas: Array.isArray(raw['report_areas']) ? (raw['report_areas'] as ImprovementMap['report_areas']) : [],
    links: list(raw['links'], (entry): TopicLink => ({
      a: str(entry['a'], ''),
      b: str(entry['b'], ''),
      weight: num(entry['weight'])
    })).filter((link) => link.a !== '' && link.b !== ''),
    specialties: list(raw['specialties'], (entry): Specialty => ({
      id: str(entry['id'], ''),
      name: str(entry['name'], '')
    })).filter((entry) => entry.id !== ''),
    positions: list(raw['positions'], (entry): MapPosition => ({
      topic: str(entry['topic'], ''),
      x: num(entry['x']),
      y: num(entry['y'])
    })).filter((entry) => entry.topic !== ''),
    confidences: confidences(raw['confidences']),
    unfiled_flag_count: num(raw['unfiled_flag_count']),
    bank: bank(raw['bank']),
    can_file_flags: raw['can_file_flags'] !== false,
    filing_note: str(raw['filing_note']),
    pages: list(raw['pages'], (entry): MapPage => ({
      id: str(entry['id']),
      title: str(entry['title']),
      topic: str(entry['topic']),
      specialty_id: nullableStr(entry['specialty_id'])
    })),
    page_links: list(raw['page_links'], (entry) => ({ topic: str(entry['topic']), entry_id: str(entry['entry_id']) })),
    page_edges: list(raw['page_edges'], (entry): TopicLink => ({ a: str(entry['a']), b: str(entry['b']), weight: num(entry['weight']) }))
  }
}

export function writtenFile(value: unknown): WrittenFile {
  const raw = obj(value)
  return {
    filename: str(raw['filename']),
    directory: str(raw['directory']),
    created_at: str(raw['created_at']),
    byte_size: num(raw['byte_size'])
  }
}

/** Builds on a timer (ADR 0018): a reply that lacks a field reads as "off, nothing known". */
export function buildSchedule(raw: unknown): import('./types').BuildSchedule {
  const data = (raw ?? {}) as Record<string, unknown>
  const times = Array.isArray(data.times) ? data.times.map(String) : []
  const lastRun = data.last_run && typeof data.last_run === 'object' ? (data.last_run as import('./types').ScheduledRun) : null
  return {
    enabled: data.enabled === true,
    times,
    batches_per_run: typeof data.batches_per_run === 'number' ? data.batches_per_run : 3,
    consent_at: typeof data.consent_at === 'string' ? data.consent_at : null,
    model_mode: data.model_mode === 'codex' ? 'codex' : 'host',
    can_run: data.can_run === true,
    blocked_reason: typeof data.blocked_reason === 'string' ? data.blocked_reason : '',
    running: data.running === true,
    next_run_at: typeof data.next_run_at === 'string' ? data.next_run_at : null,
    last_run: lastRun && Array.isArray(lastRun.piles) ? lastRun : null,
    disclosure: typeof data.disclosure === 'string' ? data.disclosure : '',
    continuous: data.continuous !== false,
    paused_until: typeof data.paused_until === 'string' ? data.paused_until : null,
    builder: data.builder && typeof data.builder === 'object' ? (data.builder as import('./types').BuilderStatus) : null
  }
}

/** The encyclopedia and the board bank (ADR 0023). */
function pageCitation(raw: unknown): PageCitation {
  const data = obj(raw)
  return {
    id: str(data.id),
    claim: str(data.claim),
    support: str(data.support),
    support_label: str(data.support_label),
    held: data.held === true,
    sources: arr(data.sources).map((item) => {
      const source = obj(item)
      return { source_id: str(source.source_id), display_name: str(source.display_name), locator: str(source.locator), quote: str(source.quote) }
    })
  }
}

export function encyclopediaCounts(raw: unknown): EncyclopediaCounts {
  const data = obj(raw)
  return {
    entries: num(data.entries),
    stale: num(data.stale),
    questions_eligible: num(data.questions_eligible),
    questions_held: num(data.questions_held),
    questions_total: num(data.questions_total)
  }
}

export function encyclopediaEntry(raw: unknown): EncyclopediaEntry {
  const data = obj(raw)
  return {
    file_path: typeof data.file_path === 'string' ? data.file_path : null,
    id: str(data.id),
    topic: str(data.topic),
    title: str(data.title, str(data.topic)),
    specialty_id: typeof data.specialty_id === 'string' ? data.specialty_id : null,
    summary: str(data.summary),
    sections: arr(data.sections).map((item) => {
      const section = obj(item)
      return {
        heading: str(section.heading),
        paragraphs: arr(section.paragraphs).map((p) => {
          const paragraph = obj(p)
          return {
            text: str(paragraph.text),
            point_ids: strings(paragraph.point_ids),
            figures: arr(paragraph.figures)
              .map((item) => {
                const f = obj(item)
                return { image_id: str(f.image_id), source: str(f.source), locator: str(f.locator), width: num(f.width), height: num(f.height) }
              })
              .filter((f) => /^[A-Za-z0-9_-]{1,64}$/.test(f.image_id))
          }
        })
      }
    }),
    point_count: num(data.point_count),
    question_count: num(data.question_count),
    status: str(data.status, 'current'),
    status_detail: str(data.status_detail),
    version: num(data.version),
    compiled_at: typeof data.compiled_at === 'string' ? data.compiled_at : null,
    literature_checked_at: typeof data.literature_checked_at === 'string' ? data.literature_checked_at : null,
    literature_note: str(data.literature_note),
    body_md: str(data.body_md),
    markdown: str(data.markdown),
    edited: data.edited === true,
    edit_outdated: data.edit_outdated === true,
    edited_at: typeof data.edited_at === 'string' ? data.edited_at : null,
    citations: arr(data.citations).map(pageCitation),
    literature: arr(data.literature).map((item) => {
      const record = obj(item)
      return {
        record_id: str(record.record_id),
        cited: record.cited === true,
        pmid: str(record.pmid),
        doi: str(record.doi),
        title: str(record.title),
        journal: str(record.journal),
        published_on: typeof record.published_on === 'string' ? record.published_on : null,
        url: str(record.url),
        priority: str(record.priority),
        retracted: record.retracted === true,
        corrected: record.corrected === true
      }
    })
  }
}

/** The Socratic tutor and the podcast (ADR 0025). */
function socraticSession(raw: unknown): SocraticSession {
  const data = obj(raw)
  const assessment = obj(data.assessment)
  const status = data.status === 'done' || data.status === 'abandoned' ? data.status : 'open'
  return {
    id: str(data.id),
    entry_id: typeof data.entry_id === 'string' ? data.entry_id : null,
    topic: str(data.topic),
    title: str(data.title),
    mode: str(data.mode),
    status,
    transcript: arr(data.transcript).map((item) => {
      const turn = obj(item)
      return { role: turn.role === 'tutor' ? 'tutor' : 'learner', text: str(turn.text), probe: str(turn.probe) }
    }),
    assessment: {
      differential: str(assessment.differential),
      treatment: str(assessment.treatment),
      knowledge_strengths: str(assessment.knowledge_strengths),
      knowledge_gaps: strings(assessment.knowledge_gaps),
      summary: str(assessment.summary)
    },
    exchanges: num(data.exchanges),
    origin: str(data.origin),
    waiting: data.waiting === true,
    relay_error: str(data.relay_error),
    assessed: data.assessed === true || (data.assessed === undefined && status === 'done' && Boolean(str(assessment.summary) || str(assessment.differential))),
    created_at: str(data.created_at),
    finished_at: typeof data.finished_at === 'string' ? data.finished_at : null
  }
}

export function socraticOverview(raw: unknown): SocraticOverview {
  const data = obj(raw)
  return {
    open: data.open && typeof data.open === 'object' ? socraticSession(data.open) : null,
    recent: arr(data.recent).map(socraticSession),
    mode: str(data.mode),
    can_answer_here: data.can_answer_here === true,
    note: str(data.note),
    disclosure: str(data.disclosure),
    import_disclosure: str(data.import_disclosure),
    relay: (() => {
      const relay = obj(data.relay)
      return { available: relay.available === true, live: relay.live === true }
    })()
  }
}

export function socraticReply(raw: unknown): SocraticReply {
  const data = obj(raw)
  return { session: socraticSession(data.session), note: str(data.note), gaps_filed: num(data.gaps_filed) }
}

export function podcastEpisode(raw: unknown): PodcastEpisode {
  const data = obj(raw)
  const status = data.status === 'scripted' || data.status === 'rendered' || data.status === 'failed' ? data.status : 'draft'
  const voices = obj(data.voices)
  return {
    id: str(data.id),
    title: str(data.title),
    status,
    status_detail: str(data.status_detail),
    entry_ids: strings(data.entry_ids),
    script: arr(data.script).map((item) => {
      const line = obj(item)
      return { speaker: line.speaker === 'B' ? 'B' : 'A', text: str(line.text) }
    }),
    takeaways: strings(data.takeaways),
    voices: Object.fromEntries(Object.entries(voices).map(([key, value]) => [key, str(value)])),
    has_audio: data.has_audio === true,
    audio_elsewhere: data.audio_elsewhere === true,
    archived: data.archived === true,
    listened_at: typeof data.listened_at === 'string' ? data.listened_at : null,
    request: str(data.request),
    progress: (() => {
      if (!data.progress || typeof data.progress !== 'object') return null
      const p = obj(data.progress)
      return { stage: str(p.stage), percent: Math.max(0, Math.min(100, num(p.percent))), label: str(p.label) }
    })(),
    sources: arr(data.sources).map((item) => {
      const source = obj(item)
      return {
        kind: str(source.kind),
        title: str(source.title),
        journal: str(source.journal),
        year: str(source.year),
        pmid: str(source.pmid),
        entry_id: str(source.entry_id)
      }
    }),
    audio_bytes: num(data.audio_bytes),
    duration_seconds: num(data.duration_seconds),
    words: num(data.words),
    created_at: str(data.created_at),
    ...(typeof data.note === 'string' ? { note: data.note } : {})
  }
}

export function podcastList(raw: unknown): PodcastList {
  const data = obj(raw)
  return {
    episodes: arr(data.episodes).map(podcastEpisode),
    can_write: data.can_write === true,
    can_render: data.can_render === true,
    note: str(data.note),
    disclosure: str(data.disclosure),
    waiting: num(data.waiting),
    max_hosted: num(data.max_hosted) || 10
  }
}

export function podcastVoices(raw: unknown): { voices: PodcastVoice[]; default: Record<string, string> } {
  const data = obj(raw)
  const fallback = obj(data.default)
  return {
    voices: arr(data.voices).map((item) => {
      const voice = obj(item)
      return { name: str(voice.name), label: str(voice.label) || str(voice.name), locale: str(voice.locale) }
    }),
    default: Object.fromEntries(Object.entries(fallback).map(([key, value]) => [key, str(value)]))
  }
}

export function flashcardDraw(raw: unknown): FlashcardDraw {
  const data = obj(raw)
  const card = obj(data.card)
  return {
    card:
      data.card && typeof data.card === 'object' && typeof card.id === 'string'
        ? {
            id: card.id,
            entry_id: str(card.entry_id),
            topic: str(card.topic),
            title: str(card.title),
            front: str(card.front),
            back: str(card.back),
            point_ids: strings(card.point_ids)
          }
        : null,
    reasons: strings(data.reasons),
    citations: arr(data.citations).map(pageCitation),
    deck: num(data.deck),
    empty_reason: str(data.empty_reason),
    kind: str(data.kind) || (data.card ? 'new' : 'empty'),
    counts: (() => {
      const c = obj(data.counts)
      return {
        ready: num(c.ready),
        new_left_today: num(c.new_left_today),
        new_total: num(c.new_total),
        learned: num(c.learned),
        next_ready_at: typeof c.next_ready_at === 'string' ? c.next_ready_at : null,
        new_per_day: num(c.new_per_day, 20)
      }
    })(),
    intervals: Object.fromEntries(Object.entries(obj(data.intervals)).map(([key, value]) => [key, str(value)]))
  }
}

export function preferencesPayload(raw: unknown): Preferences {
  const data = obj(raw)
  return {
    visible_tabs: strings(data.visible_tabs),
    order: strings(data.order),
    daily_goal: num(data.daily_goal, 20),
    podcast_speed: num(data.podcast_speed, 1),
    tabs: arr(data.tabs).map((item) => {
      const tab = obj(item)
      return { name: str(tab.name), label: str(tab.label), fixed: tab.fixed === true }
    })
  }
}

export function dissection(raw: unknown): Dissection {
  const data = obj(raw)
  const when = (value: unknown) => (typeof value === 'string' ? value : null)
  return {
    status: str(data.status, 'idle'),
    phase: str(data.phase),
    pile_id: str(data.pile_id),
    pile_title: str(data.pile_title),
    coverage: data.coverage && typeof data.coverage === 'object' ? coverage(data.coverage) : null,
    started_at: when(data.started_at),
    consent_at: when(data.consent_at),
    completed_at: when(data.completed_at),
    batches_done: num(data.batches_done),
    points_built: num(data.points_built),
    pages_compiled: num(data.pages_compiled),
    questions_written: num(data.questions_written),
    failures: num(data.failures),
    last_error: str(data.last_error),
    last_activity_at: when(data.last_activity_at),
    next_retry_at: when(data.next_retry_at),
    running: data.running === true,
    can_run: data.can_run === true,
    blocked_reason: str(data.blocked_reason),
    encyclopedia: encyclopediaCounts(data.encyclopedia),
    disclosure: str(data.disclosure)
  }
}

function encyclopediaRefresh(raw: unknown): EncyclopediaRefresh | null {
  const data = obj(raw)
  if (typeof data.at !== 'string') return null
  const pages = obj(data.pages)
  const questions = obj(data.questions)
  return {
    at: data.at,
    reason: str(data.reason),
    pages: { compiled: num(pages.compiled), failed: num(pages.failed), remaining: num(pages.remaining) },
    questions: { entries: num(questions.entries), written: num(questions.written), held: num(questions.held), failed: num(questions.failed) }
  }
}

export function encyclopediaList(raw: unknown): EncyclopediaList {
  const data = obj(raw)
  return {
    entries: arr(data.entries).map(encyclopediaEntry),
    specialties: arr(data.specialties).map((item) => {
      const entry = obj(item)
      return { id: str(entry.id), name: str(entry.name) }
    }),
    counts: encyclopediaCounts(data.counts),
    can_compile: data.can_compile === true,
    can_edit: data.can_edit === true || data.can_compile === true,
    running: data.running === true,
    last_refresh: encyclopediaRefresh(data.last_refresh),
    note: str(data.note),
    disclosure: str(data.disclosure)
  }
}

export function encyclopediaPage(raw: unknown): EncyclopediaPage {
  const data = obj(raw)
  return {
    page: data.page && typeof data.page === 'object' ? encyclopediaEntry(data.page) : null,
    counts: encyclopediaCounts(data.counts),
    message: str(data.message)
  }
}

export function boardQuestion(raw: unknown): BoardQuestion {
  const data = obj(raw)
  const question: BoardQuestion = {
    id: str(data.id),
    entry_id: str(data.entry_id),
    topic: str(data.topic),
    title: str(data.title),
    stem: str(data.stem),
    options: arr(data.options).map((item) => {
      const option = obj(item)
      return { letter: str(option.letter), text: str(option.text) }
    }),
    objective: str(data.objective),
    status: str(data.status),
    point_ids: strings(data.point_ids)
  }
  if (typeof data.answer_index === 'number') question.answer_index = data.answer_index
  if (typeof data.answer_letter === 'string') question.answer_letter = data.answer_letter
  if (typeof data.explanation === 'string') question.explanation = data.explanation
  return question
}

function boardAttempt(raw: unknown): BoardAttempt | null {
  const data = obj(raw)
  if (typeof data.id !== 'string') return null
  return {
    id: data.id,
    question_id: typeof data.question_id === 'string' ? data.question_id : null,
    chosen_index: num(data.chosen_index),
    chosen_letter: str(data.chosen_letter),
    correct: data.correct === true,
    created_at: str(data.created_at)
  }
}

export function boardNext(raw: unknown): BoardNext {
  const data = obj(raw)
  return {
    question: data.question && typeof data.question === 'object' ? boardQuestion(data.question) : null,
    cycle: cycle(data.cycle),
    last_attempt: boardAttempt(data.last_attempt),
    history_count: num(data.history_count),
    empty_reason: str(data.empty_reason)
  }
}

export function boardAnswer(raw: unknown): BoardAnswer {
  const data = obj(raw)
  const attempt = boardAttempt(data.attempt)
  return {
    attempt: attempt ?? { id: '', question_id: null, chosen_index: 0, chosen_letter: '', correct: false, created_at: '' },
    question: boardQuestion(data.question),
    citations: arr(data.citations).map(pageCitation)
  }
}

/** The Case Series hub (ADR 0022). */
function caseCatalogue(raw: unknown): CaseCatalogueEntry[] {
  return arr(raw).map((item) => {
    const data = obj(item)
    return {
      id: str(data.id),
      name: str(data.name),
      short: str(data.short),
      publisher: str(data.publisher),
      home: str(data.home),
      custom: Boolean(data.custom),
      url: data.url === undefined ? undefined : str(data.url),
      host: data.host === undefined ? undefined : str(data.host)
    }
  })
}

function caseCounts(raw: unknown): CaseCounts {
  const data = obj(raw)
  const bySeries = obj(data.by_series)
  return {
    total: typeof data.total === 'number' ? data.total : 0,
    pending: typeof data.pending === 'number' ? data.pending : 0,
    by_series: Object.fromEntries(Object.entries(bySeries).map(([key, value]) => [key, typeof value === 'number' ? value : 0]))
  }
}

export function caseEntry(raw: unknown): CaseEntry {
  const data = obj(raw)
  const status = data.status === 'synthesised' || data.status === 'failed' ? data.status : 'new'
  return {
    id: str(data.id),
    series: str(data.series),
    series_name: str(data.series_name, str(data.series)),
    series_short: str(data.series_short, str(data.series)),
    publisher: str(data.publisher),
    subseries: str(data.subseries),
    external_id: str(data.external_id),
    title: str(data.title),
    url: str(data.url),
    credit: str(data.credit),
    published_on: typeof data.published_on === 'string' ? data.published_on : null,
    status,
    status_detail: str(data.status_detail),
    one_liner: str(data.one_liner),
    points: arr(data.points).map((item) => {
      const point = obj(item)
      return { point: str(point.point), quote: str(point.quote) }
    }),
    think_first: arr(data.think_first).filter((item): item is string => typeof item === 'string'),
    specialty_id: typeof data.specialty_id === 'string' ? data.specialty_id : null,
    synthesised_at: typeof data.synthesised_at === 'string' ? data.synthesised_at : null,
    first_seen_at: str(data.first_seen_at),
    snippet: str(data.snippet)
  }
}

export function caseList(raw: unknown): CaseList {
  const data = obj(raw)
  return {
    entries: arr(data.entries).map(caseEntry),
    catalogue: caseCatalogue(data.catalogue),
    counts: caseCounts(data.counts),
    credit: str(data.credit)
  }
}

export function caseSettings(raw: unknown): CaseSettings {
  const data = obj(raw)
  const refresh = obj(data.last_refresh)
  const fetched = obj(refresh.fetched)
  return {
    enabled: data.enabled === true,
    interval_hours: typeof data.interval_hours === 'number' ? data.interval_hours : 6,
    series: Object.fromEntries(Object.entries(obj(data.series)).map(([key, value]) => [key, value === true])),
    fetches_here: data.fetches_here === true,
    can_synthesise: data.can_synthesise === true,
    running: data.running === true,
    last_refresh:
      typeof refresh.at === 'string'
        ? {
            at: refresh.at,
            reason: str(refresh.reason),
            fetched: Object.fromEntries(
              Object.entries(fetched).map(([key, value]) => {
                const group = obj(value)
                return [key, { new: typeof group.new === 'number' ? group.new : 0, error: str(group.error) }]
              })
            ),
            synthesised: typeof refresh.synthesised === 'number' ? refresh.synthesised : 0,
            failed: typeof refresh.failed === 'number' ? refresh.failed : 0
          }
        : null,
    counts: caseCounts(data.counts),
    catalogue: caseCatalogue(data.catalogue),
    note: str(data.note),
    disclosure: str(data.disclosure),
    credit: str(data.credit)
  }
}

/** The feedback of 4 October (ADR 0026). */
export function dashboard(raw: unknown): Dashboard {
  const data = obj(raw)
  const today = obj(data.today)
  return {
    daily_goal: num(data.daily_goal, 20),
    goal_met: data.goal_met === true,
    remaining_today: num(data.remaining_today),
    days_in_a_row: num(data.days_in_a_row),
    longest_run: num(data.longest_run),
    reviewed_today_already: data.reviewed_today_already === true,
    today: { question: num(today.question), card: num(today.card), socratic: num(today.socratic), page: num(today.page) },
    today_total: num(data.today_total),
    week_total: num(data.week_total),
    all_time_total: num(data.all_time_total),
    history: arr(data.history).map((item) => {
      const day = obj(item)
      return { day: str(day.day), count: num(day.count) }
    })
  }
}

function tallies(raw: unknown): TopicTally[] {
  return arr(raw).map((item) => {
    const t = obj(item)
    return { topic: str(t.topic), answered: num(t.answered), correct: num(t.correct) }
  })
}

export function scorecard(raw: unknown): Scorecard {
  const data = obj(raw)
  const board = obj(data.board)
  const recent = obj(board.last_7_days)
  const open = obj(data.open_answers)
  const cards = obj(data.flashcards)
  const socratic = obj(data.socratic)
  return {
    board: { answered: num(board.answered), correct: num(board.correct), last_7_days: { answered: num(recent.answered), correct: num(recent.correct) } },
    weakest_topics: tallies(data.weakest_topics),
    strongest_topics: tallies(data.strongest_topics),
    open_answers: { answered: num(open.answered), correct: num(open.correct) },
    flashcards: { reviewed: num(cards.reviewed), got_it: num(cards.got_it) },
    socratic: { sessions: num(socratic.sessions), exchanges: num(socratic.exchanges) },
    dashboard: dashboard(data.dashboard)
  }
}

function standing(value: unknown): Standing {
  return value === 'weak' || value === 'strong' ? value : 'mixed'
}

export function strengths(raw: unknown): Strengths {
  const data = obj(raw)
  return {
    topic_count: num(data.topic_count),
    specialties: arr(data.specialties).map((item) => {
      const group = obj(item)
      return {
        id: str(group.id),
        name: str(group.name),
        score: num(group.score),
        label: standing(group.label),
        weak: num(group.weak),
        strong: num(group.strong),
        topics: arr(group.topics).map((topicItem) => {
          const topic = obj(topicItem)
          const evidence = obj(topic.evidence)
          return {
            topic: str(topic.topic),
            specialty_id: typeof topic.specialty_id === 'string' ? topic.specialty_id : null,
            score: num(topic.score),
            label: standing(topic.label),
            reasons: strings(topic.reasons),
            evidence: {
              missed_questions: strings(evidence.missed_questions),
              flags: strings(evidence.flags),
              exam_areas: arr(evidence.exam_areas).map((area) => {
                const a = obj(area)
                return { standing: str(a.standing), quote: str(a.quote) }
              })
            },
            learner:
              topic.learner && typeof topic.learner === 'object'
                ? (() => {
                    const l = obj(topic.learner)
                    return { state: knowledgeState(l.state), label: str(l.label), recall: typeof l.recall === 'number' ? l.recall : null, next: learnerStep(l.next) }
                  })()
                : null
          }
        })
      }
    })
  }
}

// --- the learner model (ADR 0031) ---------------------------------------------------

const STATES: KnowledgeState[] = ['untried', 'forming', 'fading', 'holding']
const STEPS: LearnerStepKind[] = ['read', 'board', 'flashcards', 'socratic', 'add_source']

function knowledgeState(value: unknown): KnowledgeState {
  return STATES.includes(value as KnowledgeState) ? (value as KnowledgeState) : 'untried'
}

function learnerStep(raw: unknown): LearnerStep {
  const step = obj(raw)
  const kind = STEPS.includes(step.kind as LearnerStepKind) ? (step.kind as LearnerStepKind) : 'read'
  return { kind, label: str(step.label), why: str(step.why) }
}

function learnerUnit(raw: unknown): LearnerUnit {
  const unit = obj(raw)
  return {
    key: str(unit.key),
    title: str(unit.title),
    topic: str(unit.topic),
    entry_id: nullableStr(unit.entry_id),
    specialty_id: nullableStr(unit.specialty_id),
    state: knowledgeState(unit.state),
    state_label: str(unit.state_label),
    understood: num(unit.understood),
    recall: typeof unit.recall === 'number' ? unit.recall : null,
    half_life_days: num(unit.half_life_days),
    confidence: num(unit.confidence),
    need: num(unit.need),
    open_flags: num(unit.open_flags),
    board_ready: num(unit.board_ready),
    cards_ready: num(unit.cards_ready),
    evidence: strings(unit.evidence),
    next: learnerStep(unit.next)
  }
}

export function todayRecall(raw: unknown): TodayRecall {
  const data = obj(raw)
  const { counts: _counts, ...draw } = flashcardDraw(raw)
  return { ...draw, unit: data.unit && typeof data.unit === 'object' ? learnerUnit(data.unit) : null }
}

export function learnerModel(raw: unknown): LearnerModel {
  const data = obj(raw)
  const byName: Record<string, string> = {}
  for (const [name, key] of Object.entries(obj(data.by_name))) if (typeof key === 'string') byName[name] = key
  const byEntry: Record<string, KnowledgeState> = {}
  for (const [id, state] of Object.entries(obj(data.by_entry))) byEntry[id] = knowledgeState(state)
  return {
    plan: arr(data.plan).map(learnerUnit),
    units: arr(data.units).map(learnerUnit),
    states: arr(data.states).map((item) => {
      const entry = obj(item)
      return { state: knowledgeState(entry.state), label: str(entry.label), count: num(entry.count) }
    }),
    by_name: byName,
    by_entry: byEntry
  }
}

// --- notes (ADR 0032) ----------------------------------------------------------------

export function noteSummary(raw: unknown): import('./types').NoteSummary {
  const data = obj(raw)
  return {
    id: str(data.id),
    parent_id: nullableStr(data.parent_id),
    notebook: data.notebook === true,
    title: str(data.title),
    position: num(data.position),
    use_as_source: data.use_as_source === true,
    created_at: str(data.created_at),
    updated_at: str(data.updated_at),
    has_body: data.has_body === true
  }
}

export function note(raw: unknown): import('./types').Note {
  const data = obj(raw)
  const { has_body: _hasBody, ...summary } = noteSummary(raw)
  return { ...summary, body_md: str(data.body_md), path: strings(data.path) }
}
