/**
 * Exam reports for the Improvement Map (ADR 0020).
 *
 * A score report, uploaded here, is read on the Mac into content areas with
 * a standing each, and those areas are drawn on the map. Uploading is the
 * explicit act that sends; the disclosure above the button says so.
 */

import { useRef, useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { ExamReport } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { PhiWarning } from './PhiWarning'
import { TransmissionDisclosure } from './TransmissionDisclosure'

export const REPORT_DISCLOSURE = {
  headline: 'Adding a score report sends its text to the Mac’s own model connection, once.',
  bullets: [
    'The whole report, as read on this Mac: a PDF with a text layer, a picture of it, or a text file.',
    'Nothing else: no notes, no sources, no answers.',
    'The model is told to omit anything that identifies you; remove it yourself first where you can.',
    'What comes back is kept only where it quotes the report. The map draws the areas; it judges nothing.'
  ],
  destination: 'The Mac’s own model connection, Codex or Claude, on your sign-in. No API key is used.'
}

const STANDING_LABEL = { below: 'below', at: 'at', above: 'above' } as const
const STATUS_LABEL: Record<ExamReport['status'], string> = {
  uploaded: 'waiting to be read',
  parsed: 'read',
  failed: 'could not be read'
}

export function ExamReports({ onChanged }: { onChanged: () => void }) {
  const reports = useLoad(() => api.listExamReports(), [])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [note, setNote] = useState('')
  const input = useRef<HTMLInputElement>(null)

  const act = async (action: () => Promise<unknown>, after?: (result: unknown) => void) => {
    setBusy(true)
    setFailure(null)
    try {
      const result = await action()
      after?.(result)
      reports.reload()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const upload = () => {
    const file = input.current?.files?.[0]
    if (!file) return
    void act(
      () => api.uploadExamReport(file),
      (result) => {
        setNote((result as ExamReport).note ?? '')
        if (input.current) input.current.value = ''
      }
    )
  }

  return (
    <section className="card exam-reports" aria-labelledby="exam-reports-heading">
      <details className="card-details">
        <summary>
          <h2 id="exam-reports-heading">Exam reports</h2>
        </summary>
        <p className="muted small">
          An in-training exam report, a Step score report, a board feedback letter: the areas it
          scores become areas on the map, with your standing in each, so the map rests on a test and
          not only on what you happened to flag.
        </p>
        <TransmissionDisclosure disclosure={REPORT_DISCLOSURE} />
        <PhiWarning />
        <label className="field">
          <span>Add a score report</span>
          <input ref={input} type="file" accept=".pdf,.txt,.md,.docx,.png,.jpg,.jpeg" disabled={busy} />
        </label>
        <div className="actions">
          <button type="button" className="button primary" disabled={busy} onClick={upload}>
            {busy ? 'Working…' : 'Add and read it'}
          </button>
        </div>
        {note ? (
          <p className="ok" role="status">
            {note}
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
        {reports.result.state === 'ready' && reports.result.value.length > 0 ? (
          <ul className="list">
            {reports.result.value.map((report) => (
              <li key={report.id}>
                <span className="title">{report.display_name}</span>
                <span className="muted small">
                  {' '}
                  · {STATUS_LABEL[report.status]} · added {momentLabel(report.created_at)}
                </span>
                {report.status_detail ? <p className="muted small">{report.status_detail}</p> : null}
                {report.areas.length > 0 ? (
                  <ul className="chips">
                    {report.areas.map((area) => (
                      <li key={area.id} className={`chip standing-${area.standing}`}>
                        {area.topic}: {STANDING_LABEL[area.standing]}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="actions">
                  {report.status !== 'parsed' ? (
                    <button type="button" className="button ghost small" disabled={busy} onClick={() => void act(() => api.parseExamReport(report.id))}>
                      Read it again
                    </button>
                  ) : null}
                  <button type="button" className="button ghost small" disabled={busy} onClick={() => void act(() => api.deleteExamReport(report.id))}>
                    Remove
                  </button>
                </div>
              </li>
            ))}
          </ul>
        ) : null}
      </details>
    </section>
  )
}
