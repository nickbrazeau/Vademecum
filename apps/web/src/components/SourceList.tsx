/**
 * The files inside one pile.
 *
 * Each line says what the file is, whether anything could be read from it, how
 * much of it the model has already seen, and how much you trust it. Excluding a
 * file keeps it on the Mac and out of every future batch; deleting can be
 * refused, and the refusal is shown rather than swallowed.
 */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { byteLabel } from '../lib/format'
import { TIERS, TIER_LABEL } from '../lib/types'
import type { Source, SourceStatus, Tier } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { ConfidenceBadge } from './ConfidenceBadge'
import { Unavailable } from './Unavailable'

const STATUS_LABEL: Record<SourceStatus, string> = {
  stored: 'Stored, not read yet',
  extracted: 'Text read',
  needs_ocr: 'Needs OCR — no text layer',
  encrypted: 'Password-protected — cannot be read',
  unreadable: 'Unreadable'
}

function SourceRow({ source, onChanged }: { source: Source; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className={source.excluded ? 'source file-row excluded' : 'source file-row'}>
      <span className="file-icon" aria-hidden="true">
        {fileKind(source.display_name)}
      </span>
      <div className="file-row-main">
      <span className="title">{source.display_name}</span>
      <ConfidenceBadge tier={source.confidence} label={source.confidence_label} />
      <p className="row-meta">
        {STATUS_LABEL[source.status]} · {byteLabel(source.byte_size)}
        {source.unit_count > 0 ? ` · ${source.unit_count} ${source.unit_kind}` : null}
        {source.char_count > 0 ? ` · ${source.char_count} characters` : null}
        {' · '}
        {source.coverage.complete
          ? 'all extracted text processed'
          : `${source.coverage.covered} of ${source.coverage.total} extracted-text ${source.coverage.unit} processed`}
        {source.point_count > 0 ? ` · ${source.point_count} points` : null}
      </p>
      {source.status_detail ? <p className="warn small">{source.status_detail}</p> : null}
      {source.extraction_coverage ? (
        <p className="muted small">
          Text available in {source.extraction_coverage.units_with_text} of {source.extraction_coverage.units_total} source units.
          {' '}{source.extraction_coverage.units_image_only} image-only; {source.extraction_coverage.units_failed} unreadable; {source.extraction_coverage.units_dropped} omitted.
          {source.extraction_coverage.document_truncated ? ' Extraction was truncated.' : ''}
          {' '}No OCR or diagram interpretation is performed.
        </p>
      ) : null}
      {source.excluded ? (
        <p className="muted small">Excluded. It stays on this Mac and goes into no batch.</p>
      ) : null}

      <div className="actions">
        <label className="field inline">
          <span>Tier confidence</span>
          <select
            value={source.confidence}
            disabled={busy}
            aria-label={`Tier confidence for ${source.display_name}`}
            onChange={(event) =>
              void act(() =>
                api.updateSource(source.id, { confidence: event.target.value as Tier })
              )
            }
          >
            {TIERS.map((tier) => (
              <option key={tier} value={tier}>
                {TIER_LABEL[tier]}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="button ghost small"
          disabled={busy}
          onClick={() => void act(() => api.updateSource(source.id, { excluded: !source.excluded }))}
        >
          {source.excluded ? 'Include again' : 'Exclude'}
        </button>
        <button
          type="button"
          className="button ghost small"
          disabled={busy}
          onClick={() => void act(() => api.deleteSource(source.id))}
        >
          Delete
        </button>
      </div>

      {failure ? (
        <p className="failure" role="alert">
          {failure.message} Refresh the source list to confirm its current state before retrying.
        </p>
      ) : null}
      </div>
    </li>
  )
}

/** The file's kind from its name, for the chip. Unknown extensions show as FILE. */
function fileKind(name: string): string {
  const match = /\.([a-z0-9]{1,5})$/i.exec(name)
  const ext = match?.[1]?.toUpperCase() ?? ''
  return ext === '' || ext.length > 4 ? 'FILE' : ext
}

export function SourceList({
  pileId,
  token,
  onChanged
}: {
  pileId: string
  token: number
  onChanged: () => void
}) {
  const { result, reload } = useLoad(() => api.listSources(pileId), [pileId, token])

  if (result.state === 'loading') return <p className="muted">Reading…</p>
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />
  if (result.value.length === 0) {
    return <p className="muted">No files in this pile yet.</p>
  }

  return (
    <ul className="list">
      {result.value.map((source) => (
        <SourceRow key={source.id} source={source} onChanged={onChanged} />
      ))}
    </ul>
  )
}
