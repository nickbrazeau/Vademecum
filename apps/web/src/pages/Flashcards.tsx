/**
 * Flashcards (ADR 0024): a front, then the back, then how it went.
 *
 * The next card is a weighted draw, not a queue: topics you flagged, areas an
 * exam report put below the mark, pages whose board question you missed, and
 * cards you asked to see again come up more often, and the card says so.
 * Checking is yours and local. Nothing is due and nothing is counted.
 */

import { useState } from 'react'
import type { MouseEvent } from 'react'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { RouteName } from '../lib/router'
import type { FlashcardDraw } from '../lib/types'
import { openPageLater } from '../lib/pageLink'
import { useLoad } from '../lib/useLoad'

export function Flashcards({ onNavigate }: { onNavigate?: (name: RouteName) => void }) {
  const { result, reload } = useLoad(() => api.flashcardNext(), [])
  const [draw, setDraw] = useState<FlashcardDraw | null>(null)
  const [revealed, setRevealed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  // Past what is ready, by choice: spaced repetition otherwise rests (feedback of 5 October).
  const [practise, setPractise] = useState(false)

  const current = draw ?? (result.state === 'ready' ? result.value : null)

  // The card's page, opened in the Encyclopedia tab.
  const openPage = (entryId: string) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    openPageLater(entryId)
    onNavigate('encyclopedia')
  }

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  if (result.state === 'loading' && draw === null) return <p className="muted">Reading from this computer…</p>
  if (result.state === 'failed' && draw === null) return <Unavailable error={result.error} onRetry={reload} />
  if (current === null) return null

  const rate = async (rating: 'again' | 'good') => {
    if (!current.card || busy) return
    setBusy(true)
    setFailure(null)
    try {
      setDraw(await api.flashcardReview(current.card.id, rating, practise))
      setRevealed(false)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const skip = async () => {
    if (!current.card || busy) return
    setBusy(true)
    setFailure(null)
    try {
      setDraw(await api.flashcardNext(current.card.id, practise))
      setRevealed(false)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const keepPractising = async () => {
    setBusy(true)
    setFailure(null)
    setPractise(true)
    try {
      setDraw(await api.flashcardNext(undefined, true))
      setRevealed(false)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const counts = current.counts
  const tally = (
    <p className="muted small flashcard-tally" aria-live="polite">
      {counts.ready} ready now · {counts.new_left_today} new left today (of {counts.new_per_day} a day) · {counts.learned} of {current.deck} started
    </p>
  )

  if (current.card === null && current.kind === 'rest') {
    return (
      <section className="card" aria-labelledby="flashcards-rest-heading">
        <h2 id="flashcards-rest-heading">All caught up</h2>
        {tally}
        <p className="body">
          Nothing is ready yet.
          {counts.next_ready_at ? ` The next card is ready ${momentLabel(counts.next_ready_at)}.` : ''} Cards come back on a spacing schedule: a
          day after you first get one right, then three days, then longer each time; one you ask to see again returns in ten minutes.
        </p>
        <div className="actions">
          <button type="button" className="button primary" disabled={busy} onClick={() => void keepPractising()}>
            Keep practising
          </button>
        </div>
      </section>
    )
  }

  if (current.card === null) {
    return (
      <section className="card" aria-labelledby="flashcards-empty-heading">
        <h2 id="flashcards-empty-heading">No flashcards yet</h2>
        <p className="body">{current.empty_reason}</p>
        <p className="muted">
          Cards are written from the{' '}
          <a href="/encyclopedia" onClick={go('encyclopedia')}>
            Encyclopedia
          </a>
          , which is compiled from what a Build makes in{' '}
          <a href="/sources" onClick={go('sources')}>
            Sources
          </a>
          .
        </p>
      </section>
    )
  }

  const card = current.card
  return (
    <div className="stack">
      <section className="card flashcard" aria-labelledby="flashcard-heading">
        <h2 id="flashcard-heading">Flashcard</h2>
        {tally}
        {current.reasons.length > 0 ? <p className="muted small flashcard-why">{current.reasons.join(' ')}</p> : null}
        <p className="prompt flashcard-front">{card.front}</p>
        {revealed ? (
          <>
            <p className="body flashcard-back">{card.back}</p>
            <p className="muted small">
              From the encyclopedia page{' '}
              <a href={`/encyclopedia?page=${encodeURIComponent(card.entry_id)}`} onClick={openPage(card.entry_id)}>
                {card.title || card.topic}
              </a>
            </p>
            {current.citations.length > 0 ? (
              <details className="support-details">
                <summary>
                  Where this comes from: {current.citations.length} learning point{current.citations.length === 1 ? '' : 's'} from your sources
                </summary>
                <ul className="list small">
                  {current.citations.map((citation) => (
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
              </details>
            ) : null}
            <div className="actions">
              <button type="button" className="button" disabled={busy} onClick={() => void rate('again')}>
                Again{current.intervals.again ? <span className="muted small"> · {current.intervals.again}</span> : null}
              </button>
              <button type="button" className="button primary" disabled={busy} onClick={() => void rate('good')}>
                Got it{current.intervals.good ? <span className="small"> · next in {current.intervals.good}</span> : null}
              </button>
            </div>
          </>
        ) : (
          <div className="actions">
            <button type="button" className="button primary" disabled={busy} onClick={() => setRevealed(true)}>
              Show the answer
            </button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => void skip()}>
              Another card
            </button>
          </div>
        )}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>
    </div>
  )
}
