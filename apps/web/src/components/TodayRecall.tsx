/**
 * Today's one thing to recall (feedback of 9 October, replacing "worth a look"): a
 * flashcard from where the learner model says the next few minutes help most (ADR 0031).
 * Think of the answer, show it, say how it went; the answer is a flashcard review like any
 * other, so it feeds the spacing schedule and the model. Then the suggested step.
 */

import { useState } from 'react'
import type { MouseEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { openPageLater } from '../lib/pageLink'
import type { RouteName } from '../lib/router'
import type { TodayRecall as Recall } from '../lib/types'
import { StateBadge, StepButton } from './StudyNext'

export function TodayRecall({
  recall,
  onNavigate,
  onAnswered
}: {
  recall: Recall
  onNavigate?: (name: RouteName) => void
  /** Told once the card is answered, so Today can bring a fresh one when the app comes back. */
  onAnswered?: () => void
}) {
  const [shown, setShown] = useState(false)
  const [answered, setAnswered] = useState<'again' | 'good' | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const { card, unit } = recall

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (!onNavigate) return
    event.preventDefault()
    onNavigate(name)
  }
  const openPage = (entryId: string) => (event: MouseEvent) => {
    if (!onNavigate) return
    event.preventDefault()
    openPageLater(entryId)
    onNavigate('encyclopedia')
  }

  const answer = async (rating: 'again' | 'good') => {
    if (!card || busy) return
    setBusy(true)
    setFailure(null)
    try {
      await api.flashcardReview(card.id, rating)
      setAnswered(rating)
      onAnswered?.()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  if (unit === null) {
    return (
      <p className="muted">
        Nothing to recall yet. Once pages and their flashcards are built from your{' '}
        <a href="/foundation" onClick={go('foundation')}>
          sources
        </a>
        , one appears here each day, from where it helps most.
      </p>
    )
  }

  return (
    <div className="today-recall">
      <p className="small">
        <strong>{unit.title}</strong>
        {unit.state === 'untried' ? null : <StateBadge state={unit.state} label={unit.state_label} />}
      </p>
      {card ? (
        <>
          <p className="recall-front">{card.front}</p>
          {!shown ? (
            <button type="button" className="button primary" onClick={() => setShown(true)}>
              Show answer
            </button>
          ) : (
            <>
              <p className="recall-back">{card.back}</p>
              {recall.citations.length > 0 ? (
                <p className="muted small">
                  From{' '}
                  {card.entry_id ? (
                    <a href={`/encyclopedia?page=${encodeURIComponent(card.entry_id)}`} onClick={openPage(card.entry_id)}>
                      {card.title || unit.title}
                    </a>
                  ) : (
                    unit.title
                  )}
                  , resting on {recall.citations.length} learning point{recall.citations.length === 1 ? '' : 's'} from your sources.
                </p>
              ) : null}
              {answered === null ? (
                <div className="actions">
                  <button type="button" className="button" disabled={busy} onClick={() => void answer('again')}>
                    Again{recall.intervals.again ? <span className="muted small"> · {recall.intervals.again}</span> : null}
                  </button>
                  <button type="button" className="button primary" disabled={busy} onClick={() => void answer('good')}>
                    Got it{recall.intervals.good ? <span className="small"> · next in {recall.intervals.good}</span> : null}
                  </button>
                </div>
              ) : (
                <p className="ok small" role="status">
                  {answered === 'good' ? `Saved. It comes back in ${recall.intervals.good || 'a while'}.` : 'Saved. It comes back shortly.'}{' '}
                  <a href="/flashcards" onClick={go('flashcards')}>
                    More flashcards
                  </a>
                </p>
              )}
            </>
          )}
        </>
      ) : null}
      {failure ? (
        <p className="failure small" role="alert">
          {failure.message}
        </p>
      ) : null}
      <p className="small recall-next">{unit.next.why}</p>
      <StepButton unit={unit} onNavigate={onNavigate} />
    </div>
  )
}
