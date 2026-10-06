/**
 * Construction (ADR 0026): what is being built, and what is held back.
 *
 * The dissection agent working through the piles, the compile that turns their
 * points into pages, questions and cards, and the points and questions held
 * for review -- the workshop, kept off Today.
 */

import { useState } from 'react'
import type { MouseEvent } from 'react'
import { FolderDrop } from '../components/FolderDrop'
import { Unavailable } from '../components/Unavailable'
import { api } from '../lib/api'
import type { RouteName } from '../lib/router'
import { useLoad } from '../lib/useLoad'
import { CompileCard, DissectionCard } from './Encyclopedia'

export function Construction({ onNavigate }: { onNavigate?: (name: RouteName) => void }) {
  const [token, setToken] = useState(0)
  const pages = useLoad(() => api.encyclopediaList(), [token])
  const sheet = useLoad(() => api.today(), [token])
  const changed = () => setToken((value) => value + 1)

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  return (
    <div className="stack">
      {/* On the Mac, where the source folder is. */}
      {pages.result.state === 'ready' && pages.result.value.can_compile ? <FolderDrop onPlaced={changed} /> : null}
      <DissectionCard onChanged={changed} />
      {pages.result.state === 'ready' ? <CompileCard state={pages.result.value} onChanged={changed} /> : null}

      <section className="card" aria-labelledby="held-heading">
        <h2 id="held-heading">Held for review</h2>
        {sheet.result.state === 'loading' ? <p className="muted">Reading from this Mac…</p> : null}
        {sheet.result.state === 'failed' ? <Unavailable error={sheet.result.error} onRetry={sheet.reload} /> : null}
        {sheet.result.state === 'ready' ? (
          <>
            {sheet.result.value.held.points === 0 && sheet.result.value.held.questions === 0 ? (
              <p className="muted">Nothing is being held back.</p>
            ) : (
              <>
                <p className="body">
                  {sheet.result.value.held.points} points and {sheet.result.value.held.questions} questions are held back and are not
                  being shown or asked.
                </p>
                {sheet.result.value.held.reasons.length > 0 ? (
                  <ul className="list small">
                    {sheet.result.value.held.reasons.map((reason) => (
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
              {sheet.result.value.encyclopedia.questions_eligible} board question
              {sheet.result.value.encyclopedia.questions_eligible === 1 ? '' : 's'} ready, {sheet.result.value.encyclopedia.questions_held} held.
            </p>
          </>
        ) : null}
      </section>
    </div>
  )
}
