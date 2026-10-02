/**
 * Building learning material from a pile, in two deliberate steps.
 *
 * Step one is a read: the exact excerpts that would leave this Mac are shown,
 * with their file and locator, before anything is sent. Step two posts back the
 * batch and the hash of that selection, so a pile that changed underneath the
 * preview is refused rather than silently sending something else.
 *
 * A pile is described as fully processed only when the API says every passage
 * is covered. Anything less says how much is left.
 */

import { useEffect, useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { BuildPreview, Coverage, Run } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { BatchCoverageNote, CoverageBar } from './CoverageBar'
import { ConfidenceBadge } from './ConfidenceBadge'
import { MachineReviewedNote } from './SupportBadge'
import { TransmissionDisclosure } from './TransmissionDisclosure'
import { Unavailable } from './Unavailable'

const POLL_MS = 2000

const STATUS_LABEL: Record<Run['status'], string> = {
  running: 'Running',
  succeeded: 'Finished',
  failed: 'Failed',
  cancelled: 'Cancelled'
}

function RunReport({ run }: { run: Run }) {
  return (
    <div className="run-report" role="status">
      <p className="body">
        <span className={`badge run-${run.status}`}>{STATUS_LABEL[run.status]}</span>{' '}
        {run.stage ? <span className="muted small">{run.stage}</span> : null}
      </p>
      {run.status === 'succeeded' ? (
        <p className="body">
          {run.point_count} learning points and {run.question_count} questions were made from{' '}
          {run.excerpt_count} excerpts across {run.source_count} files. {run.held_count} were held
          back for review and are not eligible for Tutor.
        </p>
      ) : null}
      {run.status === 'failed' ? (
        <p className="failure">
          {run.failure_detail === ''
            ? 'The run failed. Previously accepted material and coverage are preserved.'
            : run.failure_detail}
        </p>
      ) : null}
      {run.status === 'cancelled' ? (
        <p className="muted">You cancelled this run. Anything it had already made was kept.</p>
      ) : null}
      <p className="muted small">
        Started {momentLabel(run.started_at)}
        {run.finished_at ? ` · finished ${momentLabel(run.finished_at)}` : null}
      </p>
    </div>
  )
}

export function BuildPanel({ pileId, onChanged }: { pileId: string; onChanged: () => void }) {
  const initial = useLoad(() => api.buildStatus(pileId), [pileId])
  const [run, setRun] = useState<Run | null>(null)
  const [coverage, setCoverage] = useState<Coverage | null>(null)
  const [preview, setPreview] = useState<BuildPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [stale, setStale] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [notice, setNotice] = useState('')
  // Host mode (ADR 0009): the run is parked on work only ChatGPT can do.
  const [awaitingHost, setAwaitingHost] = useState(false)

  useEffect(() => {
    if (initial.result.state !== 'ready') return
    setRun(initial.result.value.run)
    setCoverage(initial.result.value.coverage)
    setAwaitingHost(initial.result.value.awaiting_host)
  }, [initial.result])

  // While a run is going, ask the backend what stage it is at. Nothing here
  // guesses at progress it has not been told about.
  useEffect(() => {
    if (run?.status !== 'running') return
    const timer = window.setInterval(() => {
      void api
        .buildStatus(pileId)
        .then((state) => {
          setRun(state.run)
          setCoverage(state.coverage)
          setAwaitingHost(state.awaiting_host)
          if (state.run !== null && state.run.status !== 'running') onChanged()
        })
        .catch((error: unknown) => setFailure(asApiError(error)))
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [run?.status, pileId, onChanged])

  const loadPreview = async () => {
    setBusy(true)
    setFailure(null)
    setStale(false)
    try {
      setPreview(await api.buildPreview(pileId))
    } catch (error) {
      setPreview(null)
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const send = async () => {
    if (preview === null || busy || !preview.batch_id || !preview.selection_hash || !preview.excerpts.length || preview.blocked_reason) return
    setBusy(true)
    setFailure(null)
    try {
      const started = await api.startBuild(pileId, {
        batch_id: preview.batch_id,
        selection_hash: preview.selection_hash
      })
      setRun(started.run)
      setPreview(null)
      onChanged()
    } catch (error) {
      const problem = asApiError(error)
      if (problem.kind === 'conflict') {
        // The pile changed after the preview was drawn. The old batch is not
        // what is on screen any more, so it is thrown away and re-read.
        setPreview(null)
        await loadPreview()
        setStale(true)
      }
      setFailure(problem)
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    setBusy(true)
    setFailure(null)
    try {
      const result = await api.cancelBuild(pileId)
      setRun(result.run)
      setNotice(result.stopped ? 'Cancellation requested. Check the run status below.' : 'No active run was stopped. The run may already have finished.')
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const recheck = async () => {
    setBusy(true)
    setFailure(null)
    try {
      const result = await api.recheckPile(pileId)
      setCoverage(result.coverage)
      setPreview(null)
      setNotice(result.note || 'Extracted text reopened locally for a fresh preview. No model request has started.')
      onChanged()
    } catch (error) { setFailure(asApiError(error)) }
    finally { setBusy(false) }
  }

  if (initial.result.state === 'loading') return <p className="muted">Reading…</p>
  if (initial.result.state === 'failed') {
    return <Unavailable error={initial.result.error} onRetry={initial.reload} />
  }

  const running = run?.status === 'running'
  const incomplete = coverage !== null && !coverage.complete

  return (
    <div className="build">
      <h4>Build learning material</h4>
      {coverage ? <CoverageBar coverage={coverage} /> : null}

      {failure ? (
        <p className="failure" role="alert">
          {stale
            ? `${failure.message} The prior preview cannot be used. Review the fresh selection and current run status before sending again.`
            : `${failure.message} If a request reached the service it may have started. Check its status before retrying.`}
        </p>
      ) : null}
      {notice ? <p className="body" role="status">{notice}</p> : null}

      {run ? <RunReport run={run} /> : null}

      {running && awaitingHost ? (
        <p className="warn" role="status">
          This build is waiting for ChatGPT to do the model work. Open Vademecum in ChatGPT and
          ask it to continue the build; nothing happens here until it does. You can cancel it
          instead.
        </p>
      ) : null}

      {running ? (
        <div className="actions">
          <button type="button" className="button" disabled={busy} onClick={() => void cancel()}>
            Cancel this run
          </button>
        </div>
      ) : null}

      {!running && preview === null ? (
        <div className="actions">
          <button type="button" className="button primary" disabled={busy} onClick={() => void loadPreview()}>
            {run !== null && incomplete
              ? 'Continue with the remaining material'
              : 'Build learning material'}
          </button>
          {coverage?.complete ? <button type="button" className="button" disabled={busy} onClick={() => void recheck()}>
            Reopen for evidence recheck
          </button> : null}
        </div>
      ) : null}

      {!running && preview !== null ? (
        <div className="stack">
          {preview.blocked_reason !== '' ? (
            <p className="warn" role="status">
              {preview.blocked_reason}
            </p>
          ) : null}

          <TransmissionDisclosure disclosure={preview.disclosure} />

          <BatchCoverageNote coverage={preview.coverage} />
          <p className="muted small">
            {preview.excerpt_count} source excerpts · {preview.excerpt_chars} characters for synthesis.
            Follow-up evidence and question checks also send the generated learning material with
            selected passages and retrieved abstracts. Public topic searches go to PubMed.
          </p>

          {preview.attention.length > 0 ? (
            <div className="warn">
              <p>
                <strong>Not included, and why:</strong>
              </p>
              <ul className="list small">
                {preview.attention.map((item) => (
                  <li key={item.source_id}>
                    <span className="title">{item.display_name}</span>
                    <span className="muted small">
                      {' '}
                      · {item.status}
                      {item.status_detail ? ` · ${item.status_detail}` : null}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {preview.excerpts.length === 0 ? (
            <p className="muted">There is nothing readable left to send from this pile.</p>
          ) : (
            <ul className="list excerpts">
              {preview.excerpts.map((excerpt, index) => (
                <li key={`${excerpt.source_id}-${excerpt.locator}-${index}`}>
                  <span className="title">{excerpt.display_name}</span>
                  <span className="muted small"> · {excerpt.range_label || excerpt.locator} · </span>
                  <ConfidenceBadge tier={excerpt.confidence} label={excerpt.confidence_label} />
                  <blockquote className="quote">{excerpt.text}</blockquote>
                  <p className="muted small">{excerpt.char_count} characters</p>
                </li>
              ))}
            </ul>
          )}

          <MachineReviewedNote />

          <div className="actions">
            <button
              type="button"
              className="button primary"
              disabled={busy || !preview.batch_id || !preview.selection_hash || preview.excerpts.length === 0 || preview.blocked_reason !== ''}
              onClick={() => void send()}
            >
              Send this batch to the model
            </button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => setPreview(null)}>
              Not now
            </button>
          </div>
        </div>
      ) : null}

      {!running && run !== null && run.status === 'succeeded' && incomplete ? (
        <p className="muted small">
          This pile is not fully processed. Some of its material has never been through the model.
        </p>
      ) : null}
    </div>
  )
}
