/**
 * Foundation (feedback of 10 October): Sources and Construction as one page. What is
 * waiting, being read in and built, and why; adding a source; the builder; the piles;
 * what is held back; pages deleted; and the local data.
 */

import { useState } from 'react'
import { AddSourceDialog } from '../components/AddSourceDialog'
import { BuildSchedule } from '../components/BuildSchedule'
import { ConstructionProgress } from '../components/ConstructionProgress'
import { DeletedPages } from '../components/DeletedPages'
import { FolderDrop } from '../components/FolderDrop'
import { Loading } from '../components/Loading'
import { LocalData } from '../components/LocalData'
import { Unavailable } from '../components/Unavailable'
import { api } from '../lib/api'
import { useLoad } from '../lib/useLoad'
import { CompileCard, DissectionCard } from './Encyclopedia'
import { Piles } from './Sources'

export function Foundation() {
  const [token, setToken] = useState(0)
  const [adding, setAdding] = useState(false)
  const pages = useLoad(() => api.encyclopediaList(), [token])
  const sheet = useLoad(() => api.today(), [token])
  const changed = () => setToken((value) => value + 1)
  const onTheMac = pages.result.state === 'ready' && pages.result.value.can_compile

  return (
    <div className="stack">
      <ConstructionProgress reloadToken={token} />
      {/* On the Mac, straight into the source folder; elsewhere, uploaded to this copy. */}
      {onTheMac ? (
        <FolderDrop onPlaced={changed} />
      ) : pages.result.state === 'ready' ? (
        <section className="card" aria-labelledby="add-source-card-heading">
          <h2 id="add-source-card-heading">Add a source</h2>
          <p className="muted small">PDF, PowerPoint, Word, Markdown, text or a picture; or paste a note.</p>
          <button type="button" className="button primary" onClick={() => setAdding(true)}>
            Add a source
          </button>
          <AddSourceDialog
            open={adding}
            onClose={() => {
              setAdding(false)
              changed()
            }}
            onPhone
          />
        </section>
      ) : null}
      <BuildSchedule onBuilt={changed} />
      <DissectionCard onChanged={changed} />
      {pages.result.state === 'ready' ? <CompileCard state={pages.result.value} onChanged={changed} /> : null}
      <Piles reloadToken={token} />

      <section className="card" aria-labelledby="held-heading">
        <h2 id="held-heading">Held for review</h2>
        {sheet.result.state === 'loading' ? <Loading /> : null}
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
                  Open a pile below to see which file they came from.
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
      {onTheMac ? <DeletedPages onChanged={changed} /> : null}
      <LocalData />
    </div>
  )
}
