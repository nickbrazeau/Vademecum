/**
 * Building in the background (ADR 0033, feedback of 10 October; the timer of ADR 0018 is
 * still an option).
 *
 * The switch is a standing consent: the disclosure above it says what each batch will
 * send, and turning it on records the moment. On, the Mac builds whenever it is awake,
 * one batch at a time, and says what it is doing; it can be paused. In host mode there is
 * nobody to do the turns, and the panel says so instead of offering the switch.
 */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { BuildSchedule as Schedule } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { Loading } from './Loading'
import { Switch } from './Switch'

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
        <h2 id="build-schedule-heading">Build in the background</h2>
        <p className="muted small">Not readable right now. {result.error.message}</p>
      </section>
    )
  }
  const schedule: Schedule = result.value
  const timesText = times ?? schedule.times.join(', ')
  const parsedTimes = timesText
    .split(/[,\s]+/)
    .map((value) => value.trim())
    .filter((value) => value !== '')

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
  const save = (input: { enabled?: boolean; continuous?: boolean; pause_hours?: number }) =>
    act(() =>
      api.saveBuildSchedule({
        enabled: input.enabled ?? schedule.enabled,
        times: parsedTimes.length ? parsedTimes : schedule.times,
        batches_per_run: schedule.batches_per_run,
        continuous: input.continuous ?? schedule.continuous,
        ...(input.pause_hours !== undefined ? { pause_hours: input.pause_hours } : {})
      })
    )
  const paused = schedule.paused_until !== null

  return (
    <section className="card" aria-labelledby="build-schedule-heading" id="build-schedule">
      <h2 id="build-schedule-heading">Build in the background</h2>
      {!schedule.can_run ? (
        <p className="muted">{schedule.blocked_reason}</p>
      ) : (
        <>
          <p className="muted small">{schedule.disclosure}</p>
          <Switch
            label="Build whenever the Mac is awake"
            checked={schedule.enabled}
            disabled={busy}
            onChange={(on) => void save({ enabled: on })}
            hint={schedule.consent_at && schedule.enabled ? `consent given ${momentLabel(schedule.consent_at)}` : undefined}
          />
          {schedule.enabled && schedule.continuous && schedule.builder ? (
            <p className="small" role="status">
              {schedule.builder.reason}
              {schedule.builder.batches ? ` ${schedule.builder.batches} batch${schedule.builder.batches === 1 ? '' : 'es'} built since the Mac started.` : ''}
            </p>
          ) : null}
          <div className="actions">
            {schedule.enabled && schedule.continuous ? (
              paused ? (
                <button type="button" className="button" disabled={busy} onClick={() => void save({ pause_hours: 0 })}>
                  Resume building
                </button>
              ) : (
                <button type="button" className="button ghost" disabled={busy} onClick={() => void save({ pause_hours: 2 })}>
                  Pause for 2 hours
                </button>
              )
            ) : null}
            <button
              type="button"
              className="button"
              disabled={busy || schedule.running}
              onClick={() => void act(() => api.runBuildsNow())}
            >
              {schedule.running ? 'Building…' : 'Build every pile now'}
            </button>
          </div>
          <details className="support-details">
            <summary>Only at set times instead</summary>
            <Switch
              label="Build only at the times below"
              checked={!schedule.continuous}
              disabled={busy}
              onChange={(on) => void save({ continuous: !on })}
            />
            <label htmlFor="build-times">Times of day (24-hour, comma-separated)</label>
            <input id="build-times" type="text" value={timesText} disabled={busy} onChange={(event) => setTimes(event.target.value)} />
            {times !== null ? (
              <button type="button" className="button ghost small" disabled={busy} onClick={() => void save({})}>
                Save times
              </button>
            ) : null}
            {!schedule.continuous && schedule.enabled && schedule.next_run_at ? (
              <p className="muted small">Next run {schedule.next_run_at.replace('T', ' ')}.</p>
            ) : null}
          </details>
        </>
      )}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}
