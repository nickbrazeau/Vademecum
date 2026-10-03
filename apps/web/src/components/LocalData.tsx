/**
 * Export and backup, written into the data directory on this Mac. Lives on
 * the Sources page beside the material it preserves.
 */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { WrittenFile } from '../lib/types'

export function LocalData() {
  const [written, setWritten] = useState<{ what: string; file: WrittenFile } | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (what: string, action: () => Promise<WrittenFile>) => {
    setBusy(true)
    setFailure(null)
    try {
      setWritten({ what, file: await action() })
    } catch (error) {
      setWritten(null)
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="local-data-heading">
      <h2 id="local-data-heading">Your local data</h2>
      <p className="muted">
        An export is readable JSON you can open in any editor. A backup is a consistent copy of the
        database. Both are written into your data directory on this Mac.
      </p>
      <div className="actions">
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => void run('Export', api.createExport)}
        >
          Export as JSON
        </button>
        <button
          type="button"
          className="button"
          disabled={busy}
          onClick={() => void run('Backup', api.createBackup)}
        >
          Back up the database
        </button>
      </div>
      {written ? (
        <p className="ok" role="status">
          {written.what} written: <code>{written.file.directory}/{written.file.filename}</code>
        </p>
      ) : null}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}
