/**
 * Builds on a timer (ADR 0018): a disclosure before the switch, a Build now
 * button, and a plain refusal in host mode.
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
  it('shows the disclosure before the switch and turns the schedule on with the times given', async () => {
    const calls = stubApi(SCHEDULE)
    render(<BuildSchedule />)
    expect(await screen.findByText(/without a further prompt/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Turn the schedule on' }))
    await waitFor(() => expect(calls.some((c) => c.init?.method === 'PUT')).toBe(true))
    const put = calls.find((c) => c.init?.method === 'PUT')
    expect(JSON.parse(put!.init!.body as string)).toEqual({ enabled: true, times: ['07:00', '12:00', '18:00'], batches_per_run: 3 })
  })

  it('offers Build now, which posts once', async () => {
    const calls = stubApi(SCHEDULE)
    render(<BuildSchedule />)
    await userEvent.click(await screen.findByRole('button', { name: 'Build now' }))
    await waitFor(() => expect(calls.filter((c) => c.init?.method === 'POST')).toHaveLength(1))
    expect(calls.find((c) => c.init?.method === 'POST')!.url).toBe('/api/build/schedule/run')
  })

  it('in host mode says why there is no switch', async () => {
    stubApi({ ...SCHEDULE, model_mode: 'host', can_run: false, blocked_reason: 'Scheduled builds need the Mac’s own Codex connection.' })
    render(<BuildSchedule />)
    expect(await screen.findByText(/need the Mac’s own Codex connection/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /schedule/ })).toBeNull()
  })

  it('describes the last run in words', async () => {
    stubApi({
      ...SCHEDULE,
      last_run: { at: '2026-10-03T12:00:00Z', reason: 'scheduled', ran: true, note: '', piles: [{ pile_id: 'p', title: 'Sepsis', batches: 2, points: 5, status: 'succeeded', detail: '' }] }
    })
    render(<BuildSchedule />)
    expect(await screen.findByText(/Sepsis: built, 5 points/)).toBeInTheDocument()
  })
})
