/**
 * Adding files to a pile.
 *
 * Several at once, because that is how lecture material arrives. Every file
 * gets its own line in the result: stored, already here, or refused with the
 * reason. A file that was stored but cannot be read yet (a scan with no text
 * layer, a password-protected PDF) says so rather than sitting in the list
 * looking usable.
 */

import { useRef, useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { TIERS, TIER_LABEL } from '../lib/types'
import type { Source, Tier, UploadReport, UploadResult } from '../lib/types'
import { PhiWarning } from './PhiWarning'

export const ACCEPTED_EXTENSIONS = '.pdf,.pptx,.docx,.txt,.md'

const OUTCOME_LABEL: Record<UploadResult['outcome'], string> = {
  stored: 'Added',
  duplicate: 'Already in this pile',
  rejected: 'Not added'
}

/** A stored file that still cannot be read is not a working source. */
function statusNote(source: Source | null): string | null {
  if (source === null) return null
  if (source.status === 'needs_ocr') {
    return 'Stored, but there is no text layer to read. It needs OCR before it can be used.'
  }
  if (source.status === 'encrypted') {
    return 'Stored, but it is password-protected, so nothing can be read from it.'
  }
  if (source.status === 'unreadable') {
    return source.status_detail === '' ? 'Stored, but nothing could be read from it.' : source.status_detail
  }
  return null
}

export function UploadPanel({
  pileId,
  defaultConfidence,
  onUploaded
}: {
  pileId: string
  defaultConfidence: Tier
  onUploaded: () => void
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [confidence, setConfidence] = useState<Tier>(defaultConfidence)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<UploadReport | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const upload = async () => {
    if (files.length === 0 || busy) return
    setBusy(true)
    setFailure(null)
    setReport(null)
    try {
      setReport(await api.uploadSources(pileId, files, confidence))
      setFiles([])
      if (inputRef.current !== null) inputRef.current.value = ''
      onUploaded()
    } catch (error) {
      // Nothing is cleared: the chosen files stay chosen so the owner can
      // simply press the button again.
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="upload">
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void upload()
        }}
      >
        <label className="field">
          <span>Add files</span>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept={ACCEPTED_EXTENSIONS}
            onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
          />
        </label>
        <p className="muted small">
          PDF, PowerPoint (.pptx), Word (.docx), plain text, Markdown and pictures (.png, .jpg). Files are copied
          into your data directory on this Mac; adding a file does not send it to a model.
          Only readable text is extracted. No OCR or diagram interpretation is performed.
        </p>
        <PhiWarning />

        <fieldset className="field tier-picker">
          <legend>Source confidence for these files</legend>
          {TIERS.map((option) => (
            <label key={option} className={`tier-option tier-${option}`}>
              <input
                type="radio"
                name={`upload-confidence-${pileId}`}
                value={option}
                checked={confidence === option}
                onChange={() => setConfidence(option)}
              />
              <span>{TIER_LABEL[option]}</span>
            </label>
          ))}
        </fieldset>

        <button type="submit" className="button primary" disabled={files.length === 0 || busy}>
          {busy ? 'Adding…' : files.length > 1 ? `Add ${files.length} files` : 'Add file'}
        </button>
      </form>

      {failure ? (
        <p className="failure" role="alert">
          {failure.message} Some files may already be stored. Check the source list before retrying;
          identical files in this pile are kept only once.
        </p>
      ) : null}

      {report ? (
        <div className="upload-report" role="status">
          <p className="body">
            {report.results.filter((item) => item.outcome === 'stored').length} added ·{' '}
            {report.results.filter((item) => item.outcome === 'duplicate').length} already present · {report.rejected} not added
          </p>
          <ul className="list">
            {report.results.map((result) => {
              const note = statusNote(result.source)
              return (
                <li key={`${result.filename}-${result.outcome}-${result.message}`}>
                  <span className="title">{result.filename}</span>
                  <span className="muted small"> · {OUTCOME_LABEL[result.outcome]}</span>
                  {result.message ? <p className="body">{result.message}</p> : null}
                  {note ? <p className="warn small">{note}</p> : null}
                  {result.warnings.length > 0 ? (
                    <ul className="list small">
                      {result.warnings.map((warning) => (
                        <li key={warning} className="muted">
                          {warning}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
