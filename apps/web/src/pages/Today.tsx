/**
 * The cover sheet, and the first thing the owner sees.
 *
 * What is new in the literature first, then a page to review, what the
 * model made of your own material, and what is being held back. It counts
 * nothing down, asks for nothing back, and the same day gives the same page
 * however often you open it.
 */

import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { EncyclopediaPage } from '../components/EncyclopediaPage'
import { PointCard } from '../components/PointCard'
import { PaperLink } from '../components/PaperLink'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { dateLabel, momentLabel } from '../lib/format'
import { ReviewDashboard } from '../components/ReviewDashboard'
import type { CaseEntry, CoverSheet, Dashboard, EncyclopediaEntry, Update, UpdateState } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import type { RouteName } from '../lib/router'

function UpdateEntry({
  update,
  onSettled
}: {
  update: Update
  onSettled: (id: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const paper = update

  const settle = async (state: UpdateState) => {
    setBusy(true)
    setFailure(null)
    try {
      await api.setUpdateState(update.id, state)
      onSettled(update.id)
    } catch (error) {
      // It stays in the list, because it is still unread.
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="update">
      <p className="title"><PaperLink pmid={paper.pmid} title={paper.title} /></p>
      <p className="muted small">
        {paper.journal}
        {paper.published_on ? ` · published ${dateLabel(paper.published_on)}` : null}
        {paper.pmid ? ` · PMID ${paper.pmid}` : null}
        {paper.doi ? ` · DOI ${paper.doi}` : null}
      </p>

      <p className="badges">
        {/* Two different facts. A paper can be old to the world and new to you. */}
        {update.first_seen_at ? <span className="badge">First seen here {dateLabel(update.first_seen_at)}</span> : null}
        {paper.is_notice ? <span className="badge">Publication notice — review its linked record</span> : null}
        {paper.retracted ? (
          <span className="badge badge-retracted">Retracted — this paper has been withdrawn</span>
        ) : null}
        {paper.corrected ? (
          <span className="badge badge-corrected">
            Correction or concern notice — not a retraction
          </span>
        ) : null}
      </p>

      {paper.retracted ? (
        <p className="warn small">
          A retraction means the paper has been withdrawn. Do not rely on it.
        </p>
      ) : null}
      {paper.corrected && !paper.retracted ? (
        <p className="warn small">
          A correction or concern notice is associated with this paper. Review the notice before
          relying on the paper. This flag does not itself mean the paper was retracted.
        </p>
      ) : null}

      {update.why_relevant ? <p className="body">{update.why_relevant}</p> : null}
      <p className="muted small">
        Matched your topic <strong>{update.topic_label}</strong> · checked{' '}
        {momentLabel(update.checked_at)}
      </p>

      {failure ? (
        <p className="failure" role="alert">
          {failure.message} This is still unread.
        </p>
      ) : null}

      <div className="actions">
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => void settle('acknowledged')}
        >
          Acknowledge
        </button>
        <button
          type="button"
          className="button ghost"
          disabled={busy}
          onClick={() => void settle('dismissed')}
        >
          Dismiss
        </button>
      </div>
    </li>
  )
}

/** One page a day, the same page all day; another on request. Nothing is owed on it. */
function PageToReview({
  sheet,
  onNavigate,
  onReviewed
}: {
  sheet: CoverSheet
  onNavigate?: (name: RouteName) => void
  onReviewed: (dashboard: Dashboard) => void
}) {
  const [page, setPage] = useState<EncyclopediaEntry | null>(sheet.page)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [reviewed, setReviewed] = useState<string[]>([])

  const markReviewed = async (entryId: string) => {
    setBusy(true)
    setFailure(null)
    try {
      onReviewed(await api.markPageReviewed(entryId))
      setReviewed((current) => [...current, entryId])
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => setPage(sheet.page), [sheet.page])

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  const another = async () => {
    setBusy(true)
    setFailure(null)
    try {
      const next = await api.encyclopediaPage({ random: true, not_id: page?.id })
      if (next.page) setPage(next.page)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <details className="card toggle-card" open aria-labelledby="page-heading">
      <summary>
        <h2 id="page-heading">A page to review</h2>
        <span className="muted small">{page ? page.title : 'none today'}</span>
      </summary>
      {page === null ? (
        <p className="muted">
          {sheet.encyclopedia.message || 'No page today.'}{' '}
          <a href="/encyclopedia" onClick={go('encyclopedia')}>
            Open the Encyclopedia
          </a>{' '}
          to compile pages from what a Build has made.
        </p>
      ) : (
        <>
          <EncyclopediaPage page={page} />
          {failure ? (
            <p className="failure" role="alert">
              {failure.message}
            </p>
          ) : null}
          <div className="actions">
            <button
              type="button"
              className="button primary"
              disabled={busy || reviewed.includes(page.id)}
              onClick={() => void markReviewed(page.id)}
            >
              {reviewed.includes(page.id) ? 'Reviewed' : 'I reviewed this page'}
            </button>
            <button type="button" className="button" disabled={busy || sheet.encyclopedia.entries < 2} onClick={() => void another()}>
              Another page
            </button>
            <a href="/encyclopedia" className="button ghost" onClick={go('encyclopedia')}>
              All {sheet.encyclopedia.entries} page{sheet.encyclopedia.entries === 1 ? '' : 's'}
            </a>
          </div>
        </>
      )}
    </details>
  )
}

/** A case just published by one of the series the hub follows, with its notes (ADR 0026). */
function NewCase({ entry, onDone }: { entry: CaseEntry; onDone: (id: string) => void }) {
  const [busy, setBusy] = useState(false)
  const done = async () => {
    setBusy(true)
    try {
      await api.acknowledgeCase(entry.id)
      onDone(entry.id)
    } catch {
      setBusy(false)
    }
  }
  return (
    <li className="case-entry">
      <p className="badges">
        <span className="badge">{entry.series_short}</span>
        {entry.subseries ? <span className="badge">{entry.subseries}</span> : null}
      </p>
      <p className="title">
        <a href={entry.url} target="_blank" rel="noopener noreferrer">
          {entry.title}
        </a>
      </p>
      <p className="muted small">
        {entry.published_on ? `${dateLabel(entry.published_on)} · ` : null}
        {entry.credit ? `By ${entry.credit} · ` : null}
        {entry.publisher}
      </p>
      {entry.one_liner ? <p className="body">{entry.one_liner}</p> : null}
      {entry.points.length > 0 ? (
        <ul className="case-points">
          {entry.points.map((point) => (
            <li key={point.point}>{point.point}</li>
          ))}
        </ul>
      ) : null}
      {entry.points.length === 0 && entry.think_first.length > 0 ? (
        <ul className="case-prompts">
          {entry.think_first.map((prompt) => (
            <li key={prompt}>{prompt}</li>
          ))}
        </ul>
      ) : null}
      <div className="actions">
        <button type="button" className="button ghost small" disabled={busy} onClick={() => void done()}>
          Seen it
        </button>
      </div>
    </li>
  )
}

export function Today({
  reloadToken,
  onNavigate
}: {
  reloadToken: number
  onNavigate?: (name: RouteName) => void
}) {
  const { result, reload } = useLoad(() => api.today(), [reloadToken])
  // Acknowledging removes an entry from this pass without re-reading the page
  // underneath the owner's hands.
  const [settled, setSettled] = useState<string[]>([])
  const [seenCases, setSeenCases] = useState<string[]>([])
  const [board, setBoard] = useState<Dashboard | null>(null)

  useEffect(() => {
    setSettled([])
    setBoard(null)
  }, [reloadToken])

  if (result.state === 'loading') {
    return <p className="muted">Reading from this Mac…</p>
  }
  if (result.state === 'failed') {
    return <Unavailable error={result.error} onRetry={reload} />
  }

  const sheet: CoverSheet = result.value
  const unread = sheet.literature.updates.filter(
    (update) => update.state === 'unread' && !settled.includes(update.id)
  )
  const cases = sheet.new_cases.filter((entry) => !seenCases.includes(entry.id))
  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  return (
    <div className="stack">
      <ReviewDashboard dashboard={board ?? sheet.dashboard} />

      <PageToReview sheet={sheet} onNavigate={onNavigate} onReviewed={setBoard} />

      <details className="card toggle-card" open aria-labelledby="literature-heading">
        <summary>
          <h2 id="literature-heading">New in the literature</h2>
          <span className="muted small">{unread.length === 0 ? 'nothing unread' : `${unread.length} unread`}</span>
        </summary>
        {unread.length === 0 ? (
          <p className="muted">
            {sheet.literature.message === ''
              ? 'Nothing unread. New papers appear here only when a topic check finds them.'
              : sheet.literature.message}{' '}
            Topics and checks are in{' '}
            <a href="/settings" onClick={go('settings')}>
              Settings
            </a>
            .
          </p>
        ) : (
          <ul className="list">
            {unread.map((update) => (
              <UpdateEntry
                key={update.id}
                update={update}
                onSettled={(id) => setSettled((current) => [...current, id])}
              />
            ))}
          </ul>
        )}
      </details>

      {cases.length > 0 ? (
        <details className="card toggle-card" open aria-labelledby="new-cases-heading">
          <summary>
            <h2 id="new-cases-heading">New in the case series</h2>
            <span className="muted small">{cases.length} new</span>
          </summary>
          <ul className="list">
            {cases.map((entry) => (
              <NewCase key={entry.id} entry={entry} onDone={(id) => setSeenCases((current) => [...current, id])} />
            ))}
          </ul>
        </details>
      ) : null}

      <details className="card toggle-card" open={sheet.worth_a_look.length > 0} aria-labelledby="worth-a-look-heading">
        <summary>
          <h2 id="worth-a-look-heading">Worth a look</h2>
          <span className="muted small">{sheet.worth_a_look.length} point{sheet.worth_a_look.length === 1 ? '' : 's'}</span>
        </summary>
        {sheet.worth_a_look.length === 0 ? (
          <p className="muted">
            Nothing here yet. Learning points are built from files you add in{' '}
            <a href="/sources" onClick={go('sources')}>
              Sources
            </a>
            , and only when you press Build learning material.
          </p>
        ) : (
          <ul className="list">
            {sheet.worth_a_look.map((point) => (
              <PointCard key={point.id} point={point} />
            ))}
          </ul>
        )}
      </details>
    </div>
  )
}
