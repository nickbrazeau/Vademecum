/** Pages the owner deleted, which the encyclopedia does not write again; each can come back. */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import { useLoad } from '../lib/useLoad'

export function DeletedPages({ onChanged }: { onChanged?: () => void }) {
  const { result, reload } = useLoad(() => api.deletedPages(), [])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  if (result.state !== 'ready' || !Array.isArray(result.value?.deleted) || result.value.deleted.length === 0) return null
  const bringBack = async (topic: string) => {
    setBusy(true)
    setFailure(null)
    try {
      await api.restorePage(topic)
      reload()
      onChanged?.()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }
  return (
    <details className="card toggle-card" aria-labelledby="deleted-heading">
      <summary>
        <h2 id="deleted-heading">Deleted pages</h2>
        <span className="muted small">{result.value.deleted.length}</span>
      </summary>
      <p className="muted small">Not written again. Bring one back and it is written at the next compile.</p>
      <ul className="list small">
        {result.value.deleted.map((page) => (
          <li key={page.topic}>
            <span className="title">{page.title}</span> <span className="muted">· deleted {momentLabel(page.deleted_at)}</span>{' '}
            <button type="button" className="button small" disabled={busy} onClick={() => void bringBack(page.topic)}>
              Bring back
            </button>
          </li>
        ))}
      </ul>
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </details>
  )
}
