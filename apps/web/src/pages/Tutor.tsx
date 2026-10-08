/**
 * Tutor: one question at a time, from your own material.
 *
 * Three rules shape this page.
 *
 * 1. **Grading requires an explicit action**, and what Grade would send is
 *    listed above the button, every time.
 * 2. **A grade that did not happen is never dressed up as one.** If the model
 *    cannot be reached, the page says so, reveals the reference answer, and
 *    offers a self-assessment that is recorded and labelled as your own
 *    judgement — not the model's.
 * 3. **No pressure.** The position in the pass is a neutral fact. Nothing is
 *    overdue, nothing is a run, and stopping costs nothing.
 *
 * Refreshing keeps the same question: the API hands back the current one until
 * you press Next question, so a reload is safe.
 */

import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { BoardTutor } from '../components/BoardTutor'
import { PhiWarning } from '../components/PhiWarning'
import { Scorecard } from '../components/Scorecard'
import { SocraticTutor } from '../components/SocraticTutor'
import { SupportBadge, SupportMeaning, TopicTags } from '../components/SupportBadge'
import {
  GRADING_DISCLOSURE,
  TransmissionDisclosure
} from '../components/TransmissionDisclosure'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { answerDraftKey, clearDraft, loadDraft, saveDraft } from '../lib/drafts'
import type { RouteName } from '../lib/router'
import { MAX_ANSWER_LENGTH } from '../lib/types'
import type { Attempt, AttemptOutcome, Cycle, TutorNext, TutorQuestion } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { takePendingTutor } from '../lib/pageLink'
import type { TutorFocus } from '../lib/pageLink'

const OUTCOME_LABEL: Record<AttemptOutcome, string> = {
  correct: 'Correct',
  partially_correct: 'Partially correct',
  incorrect: 'Incorrect',
  unable_to_grade: 'Unable to grade',
  self_assessed: 'You judged this yourself — the model did not grade it'
}

const SELF_OPTIONS = [
  { outcome: 'correct' as const, label: 'I had it' },
  { outcome: 'partially_correct' as const, label: 'Partly' },
  { outcome: 'incorrect' as const, label: 'I did not have it' }
]

/** Where you are in the pass, said as a fact and nothing more. */
function CyclePosition({ cycle }: { cycle: Cycle }) {
  return (
    <p className="muted small">
      {cycle.exhausted
        ? `You have seen all ${cycle.total} questions in this pass. Next question starts pass ${cycle.cycle_number + 1}.`
        : `${cycle.remaining} left in this pass · pass ${cycle.cycle_number}`}
    </p>
  )
}

function AttemptView({ attempt }: { attempt: Attempt }) {
  const self = attempt.graded_by === 'self'
  return (
    <section className={`card attempt ${self ? 'attempt-self' : ''}`} aria-labelledby="attempt-heading">
      <h3 id="attempt-heading">{OUTCOME_LABEL[attempt.outcome]}</h3>
      {self ? (
        <p className="warn" role="note">
          You judged this yourself — the model did not grade it. It is recorded as self-assessed.
        </p>
      ) : null}

      {attempt.feedback ? <p className="body">{attempt.feedback}</p> : null}

      {attempt.strengths ? (
        <>
          <h4>What you did well</h4>
          <p className="body">{attempt.strengths}</p>
        </>
      ) : null}

      {attempt.missing_or_unsafe ? (
        <>
          <h4>What is missing or unsafe</h4>
          <p className="body">{attempt.missing_or_unsafe}</p>
        </>
      ) : null}

      {attempt.improved_answer ? (
        <>
          <h4>A better answer</h4>
          <p className="body">{attempt.improved_answer}</p>
        </>
      ) : null}

      {attempt.uncertainty ? (
        <p className="warn small">
          <strong>Uncertain:</strong> {attempt.uncertainty}
        </p>
      ) : null}
    </section>
  )
}

function Reference({ question }: { question: TutorQuestion }) {
  return (
    <section className="card" aria-labelledby="reference-heading">
      <h3 id="reference-heading">Reference answer</h3>
      <p className="body">{question.reference_answer}</p>
      {question.rubric ? (
        <>
          <h4>Rubric</h4>
          <p className="muted small">{question.rubric}</p>
        </>
      ) : null}

      {question.anchors.length > 0 ? (
        <div className="provenance">
          <h4>Where this came from</h4>
          <ul className="list small">
            {question.anchors.map((anchor) => (
              <li key={`${anchor.source_id}-${anchor.locator}`}>
                <span className="title">{anchor.display_name}</span>
                <span className="muted small"> · {anchor.locator}</span>
                {anchor.quote ? <blockquote className="quote">{anchor.quote}</blockquote> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="muted small">No source anchor was recorded for this question.</p>
      )}
    </section>
  )
}

/**
 * The Tutor (ADR 0023): board-style questions from the encyclopedia when there
 * are any; otherwise the open-answer questions a Build made, so nothing that
 * was built before the encyclopedia goes unasked.
 */
export function Tutor({ onNavigate }: { onNavigate?: (name: RouteName) => void }) {
  // Opened on one page from the Improvement Map (feedback of 6 October), or on everything.
  const [focus, setFocus] = useState<TutorFocus | null>(() => takePendingTutor())
  const [mode, setMode] = useState<'home' | 'questions' | 'socratic'>(() => focus?.mode ?? 'home')
  const card = useLoad(() => api.scorecard(), [mode])
  // Board questions in the shuffled pass, or where the learner model says you need them most (ADR 0031).
  const [order, setOrder] = useState<'shuffled' | 'need'>('shuffled')

  if (mode === 'home') {
    return (
      <div className="stack">
        <section className="card tutor-home" aria-labelledby="tutor-home-heading">
          <h2 id="tutor-home-heading">Tutor</h2>
          <div className="tutor-choices">
            <button type="button" className="button primary tutor-choice" onClick={() => setMode('questions')}>
              <strong>Board questions</strong>
              <span className="small">ABIM-style vignettes from your encyclopedia, checked on this Mac</span>
            </button>
            <button type="button" className="button tutor-choice" onClick={() => setMode('socratic')}>
              <strong>Socratic tutor</strong>
              <span className="small">Open questions, by voice or text: the differential, treatment, knowledge</span>
            </button>
          </div>
        </section>
        {card.result.state === 'ready' ? <Scorecard card={card.result.value} /> : null}
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="tutor-bar">
        <div className="chips" role="group" aria-label="Tutor mode">
          <button type="button" className={`chip${mode === 'questions' ? ' on' : ''}`} onClick={() => setMode('questions')}>
            Board questions
          </button>
          <button type="button" className={`chip${mode === 'socratic' ? ' on' : ''}`} onClick={() => setMode('socratic')}>
            Socratic tutor
          </button>
        </div>
        <button type="button" className="button ghost small" onClick={() => setMode('home')}>
          Close
        </button>
      </div>
      {mode === 'questions' && !focus ? (
        <div className="chips" role="group" aria-label="Which questions">
          <button type="button" className={`chip${order === 'shuffled' ? ' on' : ''}`} aria-pressed={order === 'shuffled'} onClick={() => setOrder('shuffled')}>
            Shuffled through everything
          </button>
          <button type="button" className={`chip${order === 'need' ? ' on' : ''}`} aria-pressed={order === 'need'} onClick={() => setOrder('need')}>
            Where you need it most
          </button>
        </div>
      ) : null}
      {focus ? (
        <p className="muted small tutor-focus" role="status">
          On one page{focus.title ? `: ${focus.title}` : ''}.{' '}
          <button type="button" className="link-button" onClick={() => setFocus(null)}>
            Everything instead
          </button>
        </p>
      ) : null}
      {mode === 'socratic' ? (
        <SocraticTutor key={focus?.entryId ?? 'all'} onNavigate={onNavigate} entryId={focus?.entryId} />
      ) : (
        <Questions key={`${focus?.entryId ?? 'all'}-${order}`} onNavigate={onNavigate} entryId={focus?.entryId} need={order === 'need' && !focus} />
      )}
    </div>
  )
}

/** Board questions when there are any; otherwise the open-answer questions a Build made. */
function Questions({ onNavigate, entryId, need = false }: { onNavigate?: (name: RouteName) => void; entryId?: string; need?: boolean }) {
  const board = useLoad(() => api.boardNext(entryId, need ? 'need' : undefined), [entryId, need])
  if (board.result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  // A board question is asked only when it is whole: five options and a stem.
  // Anything less is not a question, and the open-answer bank is asked instead.
  const candidate = board.result.state === 'ready' ? board.result.value.question : null
  const whole = board.result.state === 'ready' && candidate !== null && candidate.options.length === 5 && candidate.stem !== ''
  const reason = board.result.state === 'ready' ? board.result.value.empty_reason : ''
  if (entryId && !whole) {
    return (
      <section className="card">
        <p className="muted">{reason || 'This page has no board questions ready yet.'} Try the Socratic tutor on it instead.</p>
      </section>
    )
  }
  return whole && board.result.state === 'ready' ? (
    <BoardTutor initial={board.result.value} onNavigate={onNavigate} entryId={entryId} need={need} />
  ) : (
    <OpenTutor onNavigate={onNavigate} boardReason={reason} />
  )
}

function OpenTutor({ onNavigate, boardReason }: { onNavigate?: (name: RouteName) => void; boardReason: string }) {
  const { result, reload } = useLoad(() => api.tutorNext(), [])
  const [view, setView] = useState<TutorNext | null>(null)
  const [answer, setAnswer] = useState('')
  const [attempt, setAttempt] = useState<Attempt | null>(null)
  const [question, setQuestion] = useState<TutorQuestion | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [modelDown, setModelDown] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [refusal, setRefusal] = useState('')

  // The loaded page and the working copy are the same thing until the owner
  // advances; advancing replaces both without a second GET.
  useEffect(() => {
    if (result.state !== 'ready') return
    setView(result.value)
    setQuestion(result.value.question)
    setAnswer(
      result.value.question === null ? '' : loadDraft(answerDraftKey(result.value.question.id))
    )
    setAttempt(null)
    setRevealed(false)
    setModelDown(false)
    setRefusal('')
  }, [result])

  if (result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />
  if (view === null) return <p className="muted">Reading from this Mac…</p>

  const goSources = (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate('sources')
  }

  if (question === null) {
    return (
      <div className="stack">
        <section className="card" aria-labelledby="tutor-empty-heading">
          <h2 id="tutor-empty-heading">No questions yet</h2>
          <p className="body">{boardReason || (view.empty_reason === '' ? 'There is no question bank on this Mac yet.' : view.empty_reason)}</p>
          <p className="muted">
            Board questions are written from the Encyclopedia, which is compiled from the learning
            points a Build makes from files you add in{' '}
            <a href="/sources" onClick={goSources}>
              Sources
            </a>
            .
          </p>
        </section>
      </div>
    )
  }

  const draftKey = answerDraftKey(question.id)

  const onChangeAnswer = (value: string) => {
    setAnswer(value)
    saveDraft(draftKey, value)
  }

  const grade = async () => {
    if (busy) return
    setBusy(true)
    setFailure(null)
    setRefusal('')
    try {
      const graded = await api.tutorGrade(question.id, answer)
      setQuestion(graded.question)
      setModelDown(false)
      if (graded.graded) {
        setAttempt(graded.attempt)
        setRevealed(true)
        clearDraft(draftKey)
      } else {
        setAttempt(null)
        setRefusal(graded.message || 'This answer was not graded. Restate it as a general learning answer or choose another question.')
        setRevealed(graded.question.reference_answer !== undefined)
      }
    } catch (error) {
      const problem = asApiError(error)
      if (problem.kind === 'unavailable') {
        // The model could not be reached. Nothing was graded, and the page has
        // to say that rather than showing an empty result as a verdict.
        setModelDown(true)
        setFailure(problem)
        await revealReference()
      } else {
        setFailure(problem)
      }
    } finally {
      setBusy(false)
    }
  }

  async function revealReference() {
    if (question === null) return
    try {
      const shown = await api.tutorReveal(question.id)
      setQuestion(shown.question)
      setRevealed(true)
    } catch (error) {
      setFailure(asApiError(error))
    }
  }

  const selfAssess = async (outcome: (typeof SELF_OPTIONS)[number]['outcome']) => {
    setBusy(true)
    setFailure(null)
    try {
      const recorded = await api.tutorSelfAssess(question.id, answer, outcome)
      setAttempt(recorded.attempt)
      clearDraft(draftKey)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const advance = async () => {
    setBusy(true)
    setFailure(null)
    try {
      const next = await api.tutorAdvance(question.id)
      setView(next)
      setQuestion(next.question)
      setAnswer(next.question === null ? '' : loadDraft(answerDraftKey(next.question.id)))
      setAttempt(null)
      setRevealed(false)
      setModelDown(false)
      setRefusal('')
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  // Once an attempt exists the question card collapses to the prompt, its support and a
  // read-only copy of the answer, so the feedback sits higher on a phone. Refusals and a
  // model outage leave the full card, because the answer is still being worked on.
  const collapsed = attempt !== null

  return (
    <div className="stack">
      {collapsed ? (
        <section className="card" aria-labelledby="question-heading">
          <h2 id="question-heading">Question</h2>
          <CyclePosition cycle={view.cycle} />
          <p className="prompt prompt-compact">{question.prompt}</p>
          <p className="badges">
            <SupportBadge support={question.support} label={question.support_label} />
          </p>
          <div className="field">
            <span>Your answer</span>
            <p className="answer-given">{answer}</p>
          </div>
        </section>
      ) : (
        <section className="card" aria-labelledby="question-heading">
          <h2 id="question-heading">Question</h2>
          <CyclePosition cycle={view.cycle} />
          <p className="prompt">{question.prompt}</p>
          {question.anchors.length > 0 ? (
            <details className="support-details">
              <summary>Show the passage this question is about</summary>
              <ul className="list small">
                {question.anchors.map((anchor) => (
                  <li key={`${anchor.source_id}-${anchor.locator}`}>
                    <span className="title">{anchor.display_name}</span>
                    <span className="muted small"> · {anchor.locator}</span>
                    {anchor.quote ? <blockquote className="quote">{anchor.quote}</blockquote> : null}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}

          <p className="badges">
            <SupportBadge support={question.support} label={question.support_label} />
            {question.evidence_grade_label ? (
              <span className="badge">{question.evidence_grade_label}</span>
            ) : null}
          </p>
          <SupportMeaning meaning={question.support_meaning} />
          <TopicTags topics={question.topics} />

          <label className="field">
            <span>Your answer</span>
            <textarea
              name="answer"
              rows={8}
              value={answer}
              maxLength={MAX_ANSWER_LENGTH}
              spellCheck
              placeholder="Answer in your own words."
              onChange={(event) => onChangeAnswer(event.target.value)}
            />
          </label>

          <PhiWarning />
          <TransmissionDisclosure disclosure={GRADING_DISCLOSURE} />
          {refusal ? <p className="warn" role="alert">{refusal} Your answer draft is still here. No attempt was recorded.</p> : null}

          {failure ? (
            <p className="failure" role="alert">
              {modelDown
                ? `No model grade was recorded. ${failure.message} Your answer is still here; you can compare it with the reference answer.`
                : `${failure.message} Your answer is still here.`}
            </p>
          ) : null}

          <div className="actions">
            <button
              type="button"
              className="button primary"
              disabled={busy || answer.trim() === ''}
              onClick={() => void grade()}
            >
              {busy ? 'Working…' : 'Grade with the model'}
            </button>
            <button
              type="button"
              className="button"
              disabled={busy || revealed}
              onClick={() => void revealReference()}
            >
              Show reference answer
            </button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => void advance()}>
              Next question
            </button>
          </div>
        </section>
      )}

      {modelDown && attempt === null ? (
        <section className="card" aria-labelledby="self-assess-heading">
          <h3 id="self-assess-heading">Judge it yourself instead</h3>
          <p className="body">
            The model did not grade this. If you want the attempt recorded, read the reference
            answer below and say how you did. It will be stored as{' '}
            <strong>self-assessed</strong>, not as a model grade.
          </p>
          <div className="actions">
            {SELF_OPTIONS.map((option) => (
              <button
                key={option.outcome}
                type="button"
                className="button"
                disabled={busy}
                onClick={() => void selfAssess(option.outcome)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {attempt ? <AttemptView attempt={attempt} /> : null}
      {revealed && question.reference_answer !== undefined ? (
        <Reference question={question} />
      ) : null}
      {collapsed ? (
        <div className="actions">
          <button type="button" className="button primary" disabled={busy} onClick={() => void advance()}>
            Next question
          </button>
        </div>
      ) : null}
    </div>
  )
}
