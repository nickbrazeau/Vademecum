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
  RetiredMaterial,
  Run,
  RunStatus,
  Segment,
  Source,
  SourceAttention,
  SourceDetail,
  SourceStatus,
  SourceSummary,
  Support,
  Tier,
  Topic,
  TopicSuggestion,
  TopicGap,
  TutorNext,
  TutorQuestion,
  TutorSummary,
  Update,
  UpdateState,
  UploadReport,
  UploadResult,
  WrittenFile,
  TopicCluster,
  TopicLink,
  Specialty,
  TopicSpecialty,
  MapPosition
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

export function sourceDetail(value: unknown): SourceDetail {
  const raw = obj(value)
  return { ...source(raw), segments_preview: segments(raw['segments_preview']) }
}

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

export function retiredMaterial(value: unknown): RetiredMaterial {
  const retired = obj(obj(value)['retired'])
  return {
    retired: {
      points: num(retired['points']),
      questions: num(retired['questions']),
      attempts_kept: num(retired['attempts_kept'])
    }
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

export const attempts = (value: unknown): Attempt[] => list(value, attempt)

export function tutorSummary(value: unknown): TutorSummary {
  const raw = obj(value)
  return {
    eligible: num(raw['eligible']),
    held: num(raw['held']),
    answered_total: num(raw['answered_total']),
    cycle: cycle(raw['cycle']),
    reasons: strings(raw['reasons']),
    disclosure: disclosure(raw['disclosure'])
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
  return {
    worth_a_look: points(raw['worth_a_look']),
    worth_a_look_message: str(raw['worth_a_look_message']),
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
    bank: bank(raw['bank'])
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

export function backupCheck(value: unknown): { ok: boolean; problems: string[] } {
  const raw = obj(value)
  return { ok: bool(raw['ok']), problems: strings(raw['problems']) }
}
