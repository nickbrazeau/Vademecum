/**
 * Building in the background (ADR 0033; the timer of ADR 0018 as an option): a disclosure
 * before the switch, the builder's status, pause and resume, Build every pile now, and a
 * plain refusal in host mode.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BuildSchedule } from '../src/components/BuildSchedule'

const SCHEDULE = {
  enabled: false,
  times: ['07:00', '12:00', '18:00'],
  batches_per_run: 3,
  consent_at: null,
  model_mode: 'codex',
  can_run: true,
  blocked_reason: '',
  running: false,
  next_run_at: null,
  last_run: null,
  continuous: true,
  paused_until: null,
  builder: { state: 'idle', reason: 'Everything read in has been built.', next_attempt_at: null, last_error: '', batches: 0 },
  disclosure: 'With the schedule on, each run sends the next unbuilt excerpts of every pile without a further prompt, until you turn it off.'
}

function stubApi(schedule: Record<string, unknown>) {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url: String(url), init })
      const method = init?.method ?? 'GET'
      const bodyText = typeof init?.body === 'string' ? JSON.parse(init.body) : null
      const current = method === 'PUT' ? { ...schedule, ...bodyText, consent_at: bodyText.enabled ? '2026-10-03T12:00:00Z' : null, next_run_at: bodyText.enabled ? '2026-10-03T18:00' : null } : schedule
      return new Response(JSON.stringify(method === 'POST' ? { ...schedule, started: true, running: true } : current), {
        status: method === 'POST' ? 202 : 200,
        headers: { 'Content-Type': 'application/json' }
      })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('the build schedule panel', () => {
  it('shows the disclosure before the switch and turns building in the background on', async () => {
    const calls = stubApi(SCHEDULE)
    render(<BuildSchedule />)
    expect(await screen.findByText(/without a further prompt/)).toBeInTheDocument()
    await userEvent.click(screen.getByLabelText(/Build whenever the Mac is awake/))
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.init?.method === 'PUT')
    expect(JSON.parse(put!.init!.body as string)).toEqual({ enabled: true, times: ['07:00', '12:00', '18:00'], batches_per_run: 3, continuous: true })
  })

  it('says what the builder is doing, and pauses it for two hours', async () => {
    const calls = stubApi({ ...SCHEDULE, enabled: true, builder: { ...SCHEDULE.builder, state: 'building', reason: 'Building from Sepsis.', batches: 4 } })
    render(<BuildSchedule />)
    expect(await screen.findByText(/Building from Sepsis\. 4 batches built/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Pause for 2 hours' }))
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true))
    expect(JSON.parse(calls.find((c) => c.init?.method === 'PUT')!.init!.body as string).pause_hours).toBe(2)
  })

  it('offers Build now, which posts once', async () => {
    const calls = stubApi(SCHEDULE)
    render(<BuildSchedule />)
    await userEvent.click(await screen.findByRole('button', { name: 'Build every pile now' }))
    await waitFor(() => expect(calls.filter((c) => c.init?.method === 'POST')).toHaveLength(1))
    expect(calls.find((c) => c.init?.method === 'POST')!.url).toBe('/api/build/schedule/run')
  })

  it('in host mode says why there is no switch', async () => {
    stubApi({ ...SCHEDULE, model_mode: 'host', can_run: false, blocked_reason: 'Scheduled builds need the Mac’s own Codex connection.' })
    render(<BuildSchedule />)
    expect(await screen.findByText(/need the Mac’s own Codex connection/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Build whenever the Mac is awake/)).toBeNull()
  })
})
