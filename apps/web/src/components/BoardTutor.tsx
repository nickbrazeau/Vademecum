/**
 * The board-style Tutor (ADR 0023): a vignette, five options, one best answer.
 *
 * Choosing an option and checking it is local: the key is on this Mac, no
 * model turn is involved, and no disclosure is needed before the button. After the
 * check, the explanation says why the key is right and the others wrong, and
 * the page's points and sources the answer rests on are listed beneath it.
 * The position in the pass is a fact; nothing is owed.
 */

import { useState } from 'react'
import type { MouseEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { RouteName } from '../lib/router'
import type { BoardAnswer, BoardNext, BoardQuestion, Cycle } from '../lib/types'

function CyclePosition({ cycle }: { cycle: Cycle }) {
  return (
    <p className="muted small">
      {cycle.exhausted
        ? `You have seen all ${cycle.total} questions in this pass. Next question starts pass ${cycle.cycle_number + 1}.`
        : `${cycle.remaining} left in this pass · pass ${cycle.cycle_number}`}
    </p>
  )
}

export function BoardTutor({
  initial,
  onNavigate,
  entryId,
  need = false
}: {
  initial: BoardNext
  onNavigate?: (name: RouteName) => void
  entryId?: string
  /** Each next question from the page that needs it most (ADR 0031), not the shuffled pass. */
  need?: boolean
}) {
  const [view, setView] = useState<BoardNext>(initial)
  const [choice, setChoice] = useState<number | null>(null)
  const [result, setResult] = useState<BoardAnswer | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const question: BoardQuestion | null = view.question

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  if (question === null) {
    return (
      <section className="card" aria-labelledby="board-empty-heading">
        <h2 id="board-empty-heading">No board questions yet</h2>
        <p className="body">{view.empty_reason || 'There is no board bank on this Mac yet.'}</p>
        <p className="muted">
          Questions are written from the{' '}
          <a href="/encyclopedia" onClick={go('encyclopedia')}>
            Encyclopedia
          </a>
          , which is compiled from the learning points a Build makes in{' '}
          <a href="/sources" onClick={go('sources')}>
            Sources
          </a>
          .
        </p>
      </section>
    )
  }

  const check = async () => {
    if (choice === null || busy) return
    setBusy(true)
    setFailure(null)
    try {
      setResult(await api.boardAnswer(question.id, choice))
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
      const next = await api.boardAdvance(question.id, entryId, need ? 'need' : undefined)
      setView(next)
      setChoice(null)
      setResult(null)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const answered = result !== null
  const key = result?.question.answer_index

  return (
    <div className="stack">
      <section className="card" aria-labelledby="board-question-heading">
        <h2 id="board-question-heading">Question</h2>
        <CyclePosition cycle={view.cycle} />
        {/* The page is named only after answering: its title can give the answer away (feedback of 5 October). */}
        <p className="muted small">
          {answered ? (
            <>
              From the page <strong>{question.title || question.topic}</strong>
            </>
          ) : (
            'Vignette'
          )}
          {view.history_count > 0 ? ` · answered ${view.history_count} time${view.history_count === 1 ? '' : 's'} before` : null}
        </p>
        <p className="prompt board-stem">{question.stem}</p>
        <fieldset className="board-options" disabled={answered || busy}>
          <legend className="visually-hidden">Options</legend>
          {question.options.map((option, index) => {
            const state = answered ? (index === key ? ' is-key' : index === choice ? ' is-wrong' : '') : ''
            return (
              <label key={option.letter} className={`board-option${choice === index ? ' chosen' : ''}${state}`}>
                <input
                  type="radio"
                  name="board-choice"
                  value={index}
                  checked={choice === index}
                  onChange={() => setChoice(index)}
                />
                <span className="board-letter">{option.letter}</span>
                <span>{option.text}</span>
              </label>
            )
          })}
        </fieldset>
        {!answered ? (
          <>
            <p className="muted small">Checked on this Mac against the question’s key; no model is involved in checking it.</p>
            <div className="actions">
              <button type="button" className="button primary" disabled={busy || choice === null} onClick={() => void check()}>
                {busy ? 'Working…' : 'Check answer'}
              </button>
              <button type="button" className="button ghost" disabled={busy} onClick={() => void advance()}>
                Next question
              </button>
            </div>
          </>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>

      {result ? (
        <section className={`card attempt ${result.attempt.correct ? 'attempt-correct' : 'attempt-incorrect'}`} aria-labelledby="board-result-heading">
          <h3 id="board-result-heading">
            {result.attempt.correct ? 'Correct' : `Incorrect — the answer is ${result.question.answer_letter ?? ''}`}
          </h3>
          {result.question.explanation ? <p className="body">{result.question.explanation}</p> : null}
          {result.question.objective ? (
            <p className="muted small">
              <strong>Objective:</strong> {result.question.objective}
            </p>
          ) : null}
          {result.citations.length > 0 ? (
            <div className="provenance">
              <h4>
                Where this comes from: {result.citations.length} learning point{result.citations.length === 1 ? '' : 's'}, each with the passage
                in your sources it was taken from
              </h4>
              <ul className="list small">
                {result.citations.map((citation) => (
                  <li key={citation.id}>
                    <span className="title">{citation.claim}</span>
                    <span className="muted small"> · {citation.support_label}</span>
                    {citation.sources.slice(0, 2).map((source) => (
                      <blockquote key={`${source.source_id}-${source.locator}`} className="quote">
                        {source.quote}
                        <footer className="muted small">
                          {source.display_name}
                          {source.locator ? ` · ${source.locator}` : null}
                        </footer>
                      </blockquote>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="actions">
            <button type="button" className="button primary" disabled={busy} onClick={() => void advance()}>
              Next question
            </button>
          </div>
        </section>
      ) : null}
    </div>
  )
}
