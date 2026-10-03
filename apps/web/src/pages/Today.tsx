/**
 * The cover sheet, and the first thing the owner sees.
 *
 * Three things and nothing else: what is new in the literature, what the
 * model made of your own material, and what is being held back. It counts
 * nothing down, asks for nothing back, and the same day gives the same page
 * however often you open it.
 */

import { useEffect, useState } from 'react'
import type { MouseEvent } from 'react'
import { LiteratureSettings } from '../components/LiteratureSettings'
import { PointCard } from '../components/PointCard'
import { PaperLink } from '../components/PaperLink'
import { MachineReviewedNote } from '../components/SupportBadge'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { dateLabel, momentLabel } from '../lib/format'
import type { CoverSheet, Update, UpdateState } from '../lib/types'
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

  useEffect(() => setSettled([]), [reloadToken])

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
  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="literature-heading">
        <h2 id="literature-heading">New in the literature</h2>
        {unread.length === 0 ? (
          <p className="muted">
            {sheet.literature.message === ''
              ? 'Nothing unread. New papers appear here only when a topic check finds them.'
              : sheet.literature.message}{' '}
            <a href="#literature-settings">Literature settings</a>, below, is where you choose the
            topics and run a check.
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
        <details className="settings-details" id="literature-settings">
          <summary>Literature settings</summary>
          <LiteratureSettings onChecked={reload} />
        </details>
      </section>

      <section className="card" aria-labelledby="worth-a-look-heading">
        <h2 id="worth-a-look-heading">Worth a look</h2>
        {sheet.worth_a_look.length === 0 ? (
          <p className="muted">
            Nothing here yet. Learning points are built from files you add in{' '}
            <a href="/sources" onClick={go('sources')}>
              Sources
            </a>
            , and only when you press Build learning material.
          </p>
        ) : (
          <>
            <MachineReviewedNote />
            <ul className="list">
              {sheet.worth_a_look.map((point) => (
                <PointCard key={point.id} point={point} />
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="card" aria-labelledby="held-heading">
        <h2 id="held-heading">Held for review</h2>
        {sheet.held.points === 0 && sheet.held.questions === 0 ? (
          <p className="muted">Nothing is being held back.</p>
        ) : (
          <>
            <p className="body">
              {sheet.held.points} points and {sheet.held.questions} questions are held back and are
              not being shown or asked.
            </p>
            {sheet.held.reasons.length > 0 ? (
              <ul className="list small">
                {sheet.held.reasons.map((reason) => (
                  <li key={reason} className="muted">
                    {reason}
                  </li>
                ))}
              </ul>
            ) : null}
            <p className="muted small">
              <a href="/sources" onClick={go('sources')}>
                Open Sources
              </a>{' '}
              to see which pile they came from.
            </p>
          </>
        )}
        <p className="muted small">
          Tutor has {sheet.tutor.eligible} questions ready and {sheet.tutor.held} held.{' '}
          {sheet.tutor.message}
        </p>
      </section>

    </div>
  )
}
