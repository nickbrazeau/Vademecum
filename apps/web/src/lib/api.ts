/**
 * The only thing in this app that talks to anything.
 *
 * Every request is same-origin, relative, and `cache: 'no-store'`: private
 * content must not sit in the HTTP cache any more than it may sit in the
 * service worker's (ADR 0002). There is no second base URL and no API key. Two
 * of these routes do cause content to leave this Mac -- building learning
 * material and grading an answer -- and both are reached only from a button
 * the owner presses after being shown what would be sent.
 */

import type {
  Attempt,
  BuildPreview,
  BuildState,
  CheckReport,
  CoverSheet,
  DeviceLogin,
  Flag,
  FlagStatus,
  GradeResult,
  Health,
  ImprovementMap,
  LearningItem,
  LearningPoint,
  LiteratureSettings,
  LoginCancelled,
  ModelStatus,
  Pile,
  RetiredMaterial,
  Run,
  SelfAssessedOutcome,
  Source,
  SourceDetail,
  Tier,
  Topic,
  TutorNext,
  TutorQuestion,
  TutorSummary,
  Update,
  UpdateState,
  UploadReport,
  WrittenFile,
  TopicSpecialty,
  MapPosition,
  ExamReport
} from './types'
import * as normalize from './normalize'
import { callTool, inChat } from './host'

export const API_ROOT = '/api'

export interface FieldProblem {
  field: string
  problem: string
}

/** Why a call failed, in terms the interface can be honest about. */
export type FailureKind =
  | 'unreachable'
  /** 401: no desk session. The gateway's sign-in page is the answer (ADR 0011). */
  | 'unauthenticated'
  | 'invalid'
  | 'not_found'
  /** 409: the thing you acted on is no longer what you saw. */
  | 'conflict'
  /** 503: the model could not be reached. Nothing was graded or built. */
  | 'unavailable'
  | 'server'

export class ApiError extends Error {
  readonly kind: FailureKind
  readonly status: number
  readonly code: string
  readonly fields: FieldProblem[]

  constructor(
    kind: FailureKind,
    message: string,
    status = 0,
    fields: FieldProblem[] = [],
    code = ''
  ) {
    super(message)
    this.name = 'ApiError'
    this.kind = kind
    this.status = status
    this.code = code
    this.fields = fields
  }
}

/** Whatever went wrong, as something the interface can say out loud. */
export function asApiError(error: unknown): ApiError {
  return error instanceof ApiError
    ? error
    : new ApiError('server', 'Something went wrong on this machine.')
}

export const UNREACHABLE_MESSAGE =
  'Vademecum is not answering on this Mac. The request’s outcome could not be confirmed. Reconnect and check its status before trying again.'

interface ErrorBody {
  error?: { code?: string; message?: string; fields?: FieldProblem[] }
}

/** Where the gateway's sign-in lives. Only ever reached behind the gateway. */
export const LOGIN_PATH = '/login'

function kindFor(status: number): FailureKind {
  if (status === 401) return 'unauthenticated'
  if (status === 404) return 'not_found'
  if (status === 409) return 'conflict'
  if (status === 503) return 'unavailable'
  if (status === 422 || status === 400) return 'invalid'
  return 'server'
}

export const IN_CHAT_FILES_MESSAGE =
  'In the conversation, files come in through your source folder on the Mac; ask the assistant to sync it.'
export const IN_CHAT_REMOVAL_MESSAGE =
  'Removing things is done in the browser dashboard, or by asking the assistant, which will confirm with you first.'

/** The host tool's reply: the API's status and body, or its refusal, as data. */
interface HostReply {
  ok: boolean
  status: number
  body?: unknown
  error?: { code?: string; message?: string; fields?: FieldProblem[] }
}

/**
 * Inside a conversation there is no network: the request becomes one call to
 * the server's `app_request` tool, which allows a fixed list of routes and
 * nothing else (ADR 0014). Uploads and removals are not on that list, and the
 * message says where they are done instead.
 */
async function requestThroughHost<T>(path: string, init: RequestInit): Promise<T> {
  const method = (init.method ?? 'GET').toUpperCase()
  if (init.body !== undefined && typeof init.body !== 'string') {
    throw new ApiError('invalid', IN_CHAT_FILES_MESSAGE)
  }
  if (method === 'DELETE') throw new ApiError('invalid', IN_CHAT_REMOVAL_MESSAGE)
  let result
  try {
    result = await callTool('app_request', {
      method,
      path: `${API_ROOT}${path}`,
      ...(typeof init.body === 'string' ? { body: JSON.parse(init.body) as unknown } : {})
    })
  } catch {
    throw new ApiError('unreachable', UNREACHABLE_MESSAGE)
  }
  const reply = result.structuredContent as HostReply | undefined
  if (reply === undefined || typeof reply.ok !== 'boolean') {
    const text = result.content?.find((block) => block.type === 'text')?.text
    throw new ApiError(result.isError ? 'invalid' : 'server', text ?? 'The host returned nothing usable.')
  }
  if (!reply.ok) {
    throw new ApiError(
      reply.status === 0 ? 'unreachable' : kindFor(reply.status),
      reply.error?.message ?? UNREACHABLE_MESSAGE,
      reply.status,
      reply.error?.fields ?? [],
      reply.error?.code ?? ''
    )
  }
  return reply.body as T
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  if (inChat()) return requestThroughHost<T>(path, init)
  let response: Response
  // Only a JSON string body gets a JSON content type; a FormData body has to
  // set its own multipart boundary.
  const isJsonBody = typeof init.body === 'string'
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...init,
      // Same-origin only, and never stored by the browser.
      cache: 'no-store',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        ...(isJsonBody ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers
      }
    })
  } catch {
    // A fetch that throws is a backend that is not there. Say that, rather
    // than showing something invented in its place.
    throw new ApiError('unreachable', UNREACHABLE_MESSAGE)
  }

  if (response.status === 204) return undefined as T

  if (!response.ok) {
    let body: ErrorBody = {}
    try {
      body = (await response.json()) as ErrorBody
    } catch {
      /* a non-JSON error body is still an error */
    }
    if (response.status === 401 && window.location.pathname !== LOGIN_PATH) {
      // Behind the gateway a 401 means the desk session ended. The sign-in
      // page is a server-rendered page, so this is a navigation, not a route.
      window.location.assign(LOGIN_PATH)
    }
    throw new ApiError(
      kindFor(response.status),
      body.error?.message ?? `The local service returned ${response.status}.`,
      response.status,
      body.error?.fields ?? [],
      body.error?.code ?? ''
    )
  }

  return (await response.json()) as T
}

function body(payload: unknown): RequestInit {
  return { body: JSON.stringify(payload) }
}

function query(params: Record<string, string | boolean | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value))
  }
  const text = search.toString()
  return text === '' ? '' : `?${text}`
}

export const api = {
  health: () => request<Health>('/health'),

  today: () => request<CoverSheet>('/today').then(normalize.coverSheet),
  improvementMap: () => request<ImprovementMap>('/improvement-map').then(normalize.improvementMap),
  /** The owner's call on a topic's specialty; `null` clears it. */
  setTopicSpecialty: (topic: string, specialtyId: string | null) =>
    request<{ topic: string; specialty: TopicSpecialty | null }>('/improvement-map/topics/specialty', {
      method: 'PUT',
      ...body({ topic, specialty_id: specialtyId })
    }),
  /** Every node the graph drew, so the next open starts from the same picture. */
  saveMapPositions: (positions: MapPosition[]) =>
    request<{ saved: number }>('/improvement-map/positions', { method: 'PUT', ...body({ positions }) }),

  listPiles: () => request<Pile[]>('/piles').then(normalize.piles),
  createPile: (input: { title: string; tier: Tier; description?: string }) =>
    request<Pile>('/piles', { method: 'POST', ...body(input) }).then(normalize.pile),
  updatePile: (pileId: string, input: { title?: string; tier?: Tier; description?: string }) =>
    request<Pile>(`/piles/${pileId}`, { method: 'PATCH', ...body(input) }).then(normalize.pile),
  // May refuse with 409 `pile_in_use`.
  deletePile: (pileId: string) => request<void>(`/piles/${pileId}`, { method: 'DELETE' }),

  // The older plain-text notes. Unchanged.
  listItems: (pileId: string) => request<LearningItem[]>(`/piles/${pileId}/items`).then(normalize.items),
  createItem: (pileId: string, input: { title: string; body?: string; source?: string }) =>
    request<LearningItem>(`/piles/${pileId}/items`, { method: 'POST', ...body(input) }).then(normalize.item),
  deleteItem: (itemId: string) => request<void>(`/items/${itemId}`, { method: 'DELETE' }),

  // --- uploaded sources ---
  listSources: (pileId: string) => request<Source[]>(`/piles/${pileId}/sources`).then(normalize.sources),
  uploadSources: (pileId: string, files: File[], confidence: Tier) => {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    form.append('confidence', confidence)
    return request<UploadReport>(`/piles/${pileId}/sources`, { method: 'POST', body: form }).then(normalize.uploadReport)
  },
  getSource: (sourceId: string) => request<SourceDetail>(`/sources/${sourceId}`).then(normalize.sourceDetail),
  updateSource: (sourceId: string, input: { confidence?: Tier; excluded?: boolean }) =>
    request<Source>(`/sources/${sourceId}`, { method: 'PATCH', ...body(input) }).then(normalize.source),
  // May refuse with 409 `source_in_use`.
  deleteSource: (sourceId: string) => request<void>(`/sources/${sourceId}`, { method: 'DELETE' }),

  // --- building learning material ---
  //
  // The preview is a read. Only `startBuild` transmits, and it can only be
  // called with a batch and selection the owner has already been shown.
  buildPreview: (pileId: string) => request<BuildPreview>(`/piles/${pileId}/build/preview`).then(normalize.buildPreview),
  startBuild: (pileId: string, input: { batch_id: string; selection_hash: string }) =>
    request<{ run: Run }>(`/piles/${pileId}/build`, { method: 'POST', ...body(input) }).then(normalize.startedRun),
  buildStatus: (pileId: string) => request<BuildState>(`/piles/${pileId}/build/status`).then(normalize.buildState),
  cancelBuild: (pileId: string) =>
    request(`/piles/${pileId}/build/cancel`, { method: 'POST' }).then(normalize.cancelResult),
  recheckPile: (pileId: string) =>
    request(`/piles/${pileId}/recheck`, { method: 'POST' }).then(normalize.recheckResult),

  // --- builds on a timer (ADR 0018) ---
  //
  // Setting `enabled` is a standing consent to what every run sends; the
  // disclosure comes back with the schedule and is shown before the switch.
  buildSchedule: () => request<unknown>('/build/schedule').then(normalize.buildSchedule),
  saveBuildSchedule: (input: { enabled: boolean; times: string[]; batches_per_run: number }) =>
    request<unknown>('/build/schedule', { method: 'PUT', ...body(input) }).then(normalize.buildSchedule),
  /** Transmits, for every pile with unbuilt passages, exactly as Build would. */
  runBuildsNow: () => request<unknown>('/build/schedule/run', { method: 'POST' }).then(normalize.buildSchedule),

  // --- exam reports for the map (ADR 0020) ---
  listExamReports: () => request<unknown>('/improvement-map/reports').then((value) => (Array.isArray(value) ? (value as ExamReport[]) : [])),
  uploadExamReport: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ExamReport>('/improvement-map/reports', { method: 'POST', body: form })
  },
  parseExamReport: (reportId: string) => request<ExamReport>(`/improvement-map/reports/${reportId}/parse`, { method: 'POST' }),
  deleteExamReport: (reportId: string) => request<void>(`/improvement-map/reports/${reportId}`, { method: 'DELETE' }),

  listPoints: (params: { pile_id?: string; held?: boolean } = {}) =>
    request<LearningPoint[]>(`/points${query(params)}`).then(normalize.points),
  retireMaterial: (pileId: string) =>
    request<RetiredMaterial>(`/piles/${pileId}/material`, { method: 'DELETE' }).then(normalize.retiredMaterial),

  // --- tutor ---
  tutorSummary: () => request<TutorSummary>('/tutor').then(normalize.tutorSummary),
  /** Idempotent: refreshing the page gives back the same question. */
  tutorNext: () => request<TutorNext>('/tutor/next').then(normalize.tutorNext),
  tutorAdvance: (questionId: string) =>
    request<TutorNext>('/tutor/advance', { method: 'POST', ...body({ question_id: questionId }) }).then(normalize.tutorNext),
  /** Transmits: this question, its reference answer and rubric, this answer. */
  tutorGrade: (questionId: string, answer: string) =>
    request<GradeResult>('/tutor/grade', {
      method: 'POST',
      ...body({ question_id: questionId, answer })
    }).then(normalize.gradeResult),
  tutorReveal: (questionId: string) =>
    request<{ question: TutorQuestion }>('/tutor/reveal', {
      method: 'POST',
      ...body({ question_id: questionId })
    }).then(normalize.revealed),
  tutorSelfAssess: (questionId: string, answer: string, outcome: SelfAssessedOutcome) =>
    request<{ attempt: Attempt }>('/tutor/self-assess', {
      method: 'POST',
      ...body({ question_id: questionId, answer, outcome })
    }).then(normalize.recordedAttempt),
  tutorHistory: (limit?: number) => request<Attempt[]>(`/tutor/history${query({ limit })}`).then(normalize.attempts),

  // --- literature ---
  literatureTopics: () => request<Topic[]>('/literature/topics').then(normalize.topics),
  literatureSuggestions: () => request('/literature/suggestions').then(normalize.topicSuggestions),
  createLiteratureTopic: (input: { label: string; query: string }) =>
    request<Topic>('/literature/topics', { method: 'POST', ...body(input) }).then(normalize.topic),
  updateLiteratureTopic: (
    topicId: string,
    input: { label?: string; query?: string; enabled?: boolean }
  ) => request<Topic>(`/literature/topics/${topicId}`, { method: 'PATCH', ...body(input) }).then(normalize.topic),
  deleteLiteratureTopic: (topicId: string) =>
    request<void>(`/literature/topics/${topicId}`, { method: 'DELETE' }),
  /** Transmits: the topic words only. Never material, notes or answers. */
  literatureCheck: (topicId?: string) =>
    request<CheckReport>('/literature/check', {
      method: 'POST',
      ...body(topicId === undefined ? {} : { topic_id: topicId })
    }).then(normalize.checkReport),
  literatureUpdates: (state?: UpdateState) =>
    request<Update[]>(`/literature/updates${query({ state })}`).then(normalize.updates),
  setUpdateState: (updateId: string, state: UpdateState) =>
    request<Update>(`/literature/updates/${updateId}`, { method: 'PATCH', ...body({ state }) }).then(normalize.update),
  literatureSettings: () => request<LiteratureSettings>('/literature/settings').then(normalize.literatureSettings),
  saveLiteratureSettings: (input: {
    weekly_enabled?: boolean
    interval_hours?: number
    preferred_journals?: string[]
    guidelines_first?: boolean
  }) => request<LiteratureSettings>('/literature/settings', { method: 'PUT', ...body(input) }).then(normalize.literatureSettings),

  listFlags: (status?: FlagStatus) =>
    request<Flag[]>(status === undefined ? '/flags' : `/flags?status=${status}`).then(normalize.flags),
  createFlag: (input: { text: string; topic?: string }) =>
    request<Flag>('/flags', { method: 'POST', ...body(input) }).then(normalize.flag),
  updateFlag: (flagId: string, input: { status?: FlagStatus; topic?: string; text?: string }) =>
    request<Flag>(`/flags/${flagId}`, { method: 'PATCH', ...body(input) }).then(normalize.flag),
  deleteFlag: (flagId: string) => request<void>(`/flags/${flagId}`, { method: 'DELETE' }),
  /** Transmits: the unfiled flags' text, once, to the Mac's model connection (ADR 0021). */
  fileFlags: () => request<{ started: boolean }>('/flags/file', { method: 'POST' }),

  // The encyclopedia and the board bank (ADR 0023). Reading and answering are
  // local; Compile now starts the two model turns the page's disclosure names.
  encyclopediaList: (q?: string) => request<unknown>(`/encyclopedia${query({ q })}`).then(normalize.encyclopediaList),
  encyclopediaPage: (options: { random?: boolean; not_id?: string } = {}) =>
    request<unknown>(`/encyclopedia/page${query(options)}`).then(normalize.encyclopediaPage),
  encyclopediaEntry: (entryId: string) => request<unknown>(`/encyclopedia/${entryId}`).then(normalize.encyclopediaEntry),
  compileEncyclopedia: () => request<unknown>('/encyclopedia/compile', { method: 'POST' }).then(normalize.encyclopediaList),
  // The Socratic tutor and the podcast generator (ADR 0025). Answering a
  // Socratic question and writing an episode are the two that send; the
  // disclosure on each page says what. Rendering audio sends nothing.
  socraticOverview: () => request<unknown>('/socratic').then(normalize.socraticOverview),
  socraticStart: (entryId?: string) =>
    request<unknown>('/socratic', { method: 'POST', ...body(entryId ? { entry_id: entryId } : {}) }).then(normalize.socraticReply),
  socraticAnswer: (sessionId: string, answer: string) =>
    request<unknown>(`/socratic/${sessionId}/answer`, { method: 'POST', ...body({ answer }) }).then(normalize.socraticReply),
  socraticAbandon: (sessionId: string) => request<unknown>(`/socratic/${sessionId}/abandon`, { method: 'POST' }).then(normalize.socraticReply),
  podcasts: () => request<unknown>('/podcasts').then(normalize.podcastList),
  podcastVoices: () => request<unknown>('/podcasts/voices').then(normalize.podcastVoices),
  createPodcast: (input: { entry_ids?: string[]; pick: 'chosen' | 'today' | 'improvement'; title?: string }) =>
    request<unknown>('/podcasts', { method: 'POST', ...body(input) }).then(normalize.podcastEpisode),
  podcast: (episodeId: string) => request<unknown>(`/podcasts/${episodeId}`).then(normalize.podcastEpisode),
  rewritePodcast: (episodeId: string) => request<{ started: boolean }>(`/podcasts/${episodeId}/script`, { method: 'POST' }),
  renderPodcast: (episodeId: string, voices: { voice_a: string; voice_b: string }) =>
    request<unknown>(`/podcasts/${episodeId}/render`, { method: 'POST', ...body(voices) }).then(normalize.podcastEpisode),
  deletePodcast: (episodeId: string) => request<void>(`/podcasts/${episodeId}`, { method: 'DELETE' }),

  // The feedback of 4 October (ADR 0026): local reads and writes, no model turn.
  scorecard: () => request<unknown>('/tutor/scorecard').then(normalize.scorecard),
  markPageReviewed: (entryId: string) =>
    request<unknown>('/activity/page', { method: 'POST', ...body({ entry_id: entryId }) }).then(normalize.dashboard),
  strengths: () => request<unknown>('/improvement-map/strengths').then(normalize.strengths),
  editPage: (entryId: string, bodyMd: string) =>
    request<unknown>(`/encyclopedia/${entryId}`, { method: 'PUT', ...body({ body_md: bodyMd }) }).then(normalize.encyclopediaEntry),
  revertPage: (entryId: string) => request<unknown>(`/encyclopedia/${entryId}/edit`, { method: 'DELETE' }).then(normalize.encyclopediaEntry),
  acknowledgeCase: (caseId: string) => request<{ acknowledged: boolean }>(`/cases/${caseId}/acknowledge`, { method: 'POST' }),

  // Flashcards and preferences (ADR 0024): local, no model turn.
  flashcardsOverview: () => request<unknown>('/flashcards').then(normalize.flashcardOverview),
  flashcardNext: (notId?: string) => request<unknown>(`/flashcards/next${query({ not_id: notId })}`).then(normalize.flashcardDraw),
  flashcardReview: (cardId: string, rating: 'again' | 'good') =>
    request<{ review: unknown; next: unknown }>('/flashcards/review', { method: 'POST', ...body({ card_id: cardId, rating }) }).then((data) =>
      normalize.flashcardDraw(data.next)
    ),
  preferences: () => request<unknown>('/preferences').then(normalize.preferencesPayload),
  savePreferences: (visibleTabs: string[], order?: string[]) =>
    request<unknown>('/preferences', { method: 'PUT', ...body(order ? { visible_tabs: visibleTabs, order } : { visible_tabs: visibleTabs }) }).then(
      normalize.preferencesPayload
    ),
  saveDailyGoal: (goal: number) =>
    request<unknown>('/preferences', { method: 'PUT', ...body({ daily_goal: goal }) }).then(normalize.preferencesPayload),
  dissection: () => request<unknown>('/encyclopedia/dissection').then(normalize.dissection),
  startDissection: (pileId: string) =>
    request<unknown>('/encyclopedia/dissection', { method: 'POST', ...body({ pile_id: pileId }) }).then(normalize.dissection),
  stopDissection: () => request<unknown>('/encyclopedia/dissection/stop', { method: 'POST' }).then(normalize.dissection),
  boardOverview: () => request<unknown>('/tutor/board').then(normalize.boardOverview),
  boardNext: () => request<unknown>('/tutor/board/next').then(normalize.boardNext),
  boardAnswer: (questionId: string, choice: number) =>
    request<unknown>('/tutor/board/answer', { method: 'POST', ...body({ question_id: questionId, choice }) }).then(normalize.boardAnswer),
  boardAdvance: (questionId: string) =>
    request<unknown>('/tutor/board/advance', { method: 'POST', ...body({ question_id: questionId }) }).then(normalize.boardNext),

  // The Case Series hub (ADR 0022). Listing reads this Mac; the switch and
  // "Refresh now" are the explicit acts behind the fixed public requests the
  // disclosure on the page describes.
  listCases: (params: { series?: string; q?: string; specialty?: string } = {}) =>
    request<unknown>(`/cases${query(params)}`).then(normalize.caseList),
  caseSettings: () => request<unknown>('/cases/settings').then(normalize.caseSettings),
  saveCaseSettings: (input: { enabled: boolean; interval_hours: number; series?: Record<string, boolean> }) =>
    request<unknown>('/cases/settings', { method: 'PUT', ...body(input) }).then(normalize.caseSettings),
  refreshCases: () => request<unknown>('/cases/refresh', { method: 'POST' }).then(normalize.caseSettings),

  createExport: () => request<WrittenFile>('/export', { method: 'POST' }).then(normalize.writtenFile),
  createBackup: () => request<WrittenFile>('/backup', { method: 'POST' }).then(normalize.writtenFile),

  // The model connection (ADR 0006). None of these sends learning content:
  // they read sign-in and usage state and perform ChatGPT device-code sign-in.
  modelStatus: () => request<ModelStatus>('/model/status'),
  startModelLogin: () => request<DeviceLogin>('/model/login', { method: 'POST' }),
  // No body: the pending sign-in is held by the backend, so the browser never
  // has to keep a login id in order to cancel one.
  cancelModelLogin: () => request<LoginCancelled>('/model/login/cancel', { method: 'POST' }),
  restartModel: () => request<ModelStatus>('/model/restart', { method: 'POST' })
}
