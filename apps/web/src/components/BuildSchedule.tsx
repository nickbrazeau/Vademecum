/**
 * Builds on a timer (ADR 0018).
 *
 * The switch is a standing consent: the disclosure above it says what every
 * run will send, and turning it on records the moment. "Build now" is the
 * same consent given once. In host mode there is nobody to do the turns on
 * a timer, and the panel says so instead of offering the switch.
 */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { BuildSchedule as Schedule } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { Loading } from './Loading'

const STATUS_LABEL: Record<string, string> = {
  succeeded: 'built',
  nothing_to_build: 'nothing new to build',
  busy: 'a build was already running',
  timed_out: 'did not finish in time',
  failed: 'failed'
}

export function BuildSchedule({ onBuilt }: { onBuilt?: () => void }) {
  const { result, reload } = useLoad(() => api.buildSchedule(), [])
  const [times, setTimes] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  if (result.state === 'loading') return <Loading />
  if (result.state === 'failed') {
    // A schedule that cannot be read is a quiet line, not an alarm: the rest
    // of the page is what the owner came for.
    return (
      <section className="card" aria-labelledby="build-schedule-heading" id="build-schedule">
        <h2 id="build-schedule-heading">Builds on a timer</h2>
        <p className="muted small">Not readable right now. {result.error.message}</p>
      </section>
    )
  }
  const schedule: Schedule = result.value
  const timesText = times ?? schedule.times.join(', ')

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      reload()
      onBuilt?.()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const parsedTimes = timesText
    .split(/[,\s]+/)
    .map((value) => value.trim())
    .filter((value) => value !== '')

  const save = (enabled: boolean) =>
    act(() => api.saveBuildSchedule({ enabled, times: parsedTimes, batches_per_run: schedule.batches_per_run }))

  return (
    <section className="card" aria-labelledby="build-schedule-heading" id="build-schedule">
      <h2 id="build-schedule-heading">Builds on a timer</h2>
      {!schedule.can_run ? (
        <p className="muted">{schedule.blocked_reason}</p>
      ) : (
        <>
          <p className="muted small">{schedule.disclosure}</p>
          <label htmlFor="build-times">Times of day (24-hour, comma-separated)</label>
          <input
            id="build-times"
            type="text"
            value={timesText}
            disabled={busy}
            onChange={(event) => setTimes(event.target.value)}
          />
          <p className="muted small">
            Up to {schedule.batches_per_run} batch{schedule.batches_per_run === 1 ? '' : 'es'} per pile per run.{' '}
            {schedule.enabled && schedule.next_run_at ? `Next run ${schedule.next_run_at.replace('T', ' ')}.` : 'Off.'}
            {schedule.consent_at ? ` Consent given ${momentLabel(schedule.consent_at)}.` : null}
          </p>
          <div className="actions">
            <button type="button" className="button primary" disabled={busy} onClick={() => void save(!schedule.enabled)}>
              {schedule.enabled ? 'Turn the schedule off' : 'Turn the schedule on'}
            </button>
            {schedule.enabled && times !== null ? (
              <button type="button" className="button ghost" disabled={busy} onClick={() => void save(true)}>
                Save times
              </button>
            ) : null}
            <button
              type="button"
              className="button"
              disabled={busy || schedule.running}
              onClick={() => void act(() => api.runBuildsNow())}
            >
              {schedule.running ? 'Building…' : 'Build now'}
            </button>
          </div>
        </>
      )}
      {schedule.last_run ? (
        <p className="muted small">
          Last run {momentLabel(schedule.last_run.at)}
          {schedule.last_run.ran
            ? ': ' +
              schedule.last_run.piles
                .map((pile) => `${pile.title}: ${STATUS_LABEL[pile.status] ?? pile.status}${pile.points ? `, ${pile.points} point${pile.points === 1 ? '' : 's'}` : ''}`)
                .join('; ')
            : `: ${schedule.last_run.note}`}
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
