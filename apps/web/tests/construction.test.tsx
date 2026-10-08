/**
 * Construction (feedback of 6 October): what is waiting, read in and built; and pages the
 * owner deleted, each of which can come back.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ConstructionProgress } from '../src/components/ConstructionProgress'
import { DeletedPages } from '../src/components/DeletedPages'

afterEach(() => vi.unstubAllGlobals())

function stub(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const path = String(url).split('?')[0] ?? ''
      calls.push({ url: path, method, body: init?.body ? JSON.parse(String(init.body)) : null })
      const payload = routes[`${method} ${path}`] ?? routes[path] ?? {}
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

const PROGRESS = {
  folder: {
    present: true, files: 5, waiting_count: 2, scanning: true,
    waiting: [{ pile: 'Renal', filename: 'kidney.pdf' }, { pile: 'Medium confidence', filename: 'notes.md' }],
    last_scan: { at: '2026-10-08T10:00:00Z', stored: 3, already_present: 0, more_waiting: true, rejected: [{ filename: 'x.exe', pile: 'Sepsis', message: 'Not a kind Vademecum reads.' }] }
  },
  sources: {
    counts: { not_started: 1, partly: 1, built: 1, unreadable: 0 }, total: 3,
    items: [
      { id: 's1', filename: 'built.pdf', pile: 'Sepsis', status: 'extracted', state: 'built', percent: 100, points: 12, added_at: 'now' },
      { id: 's2', filename: 'half.pdf', pile: 'Sepsis', status: 'extracted', state: 'partly', percent: 40, points: 4, added_at: 'now' },
      { id: 's3', filename: 'new.pdf', pile: 'Renal', status: 'extracted', state: 'not_started', percent: 0, points: 0, added_at: 'now' }
    ]
  }
}

describe('Construction progress', () => {
  it('shows waiting, read in and built, the last scan, and every source filterable', async () => {
    stub({ '/api/construction': PROGRESS })
    const user = userEvent.setup()
    render(<ConstructionProgress />)
    expect(await screen.findByText(/waiting to be read in · reading now/)).toBeInTheDocument()
    expect(screen.getByText(/built into the encyclopedia · 1 partly/)).toBeInTheDocument()
    expect(screen.getByText(/3 read in, 1 turned away; more to come, eight at a time/)).toBeInTheDocument()
    expect(screen.getByText('kidney.pdf')).toBeInTheDocument()
    expect(screen.getByText('x.exe')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Partly built' }))
    expect(screen.getByText('half.pdf')).toBeInTheDocument()
    expect(screen.queryByText('built.pdf')).not.toBeInTheDocument()
    expect(screen.getByText('40%')).toBeInTheDocument()
  })
})

describe('Deleted pages', () => {
  it('lists them and brings one back', async () => {
    const calls = stub({
      '/api/encyclopedia/deleted': { deleted: [{ topic: 'Sepsis', title: 'Sepsis', deleted_at: '2026-10-08T09:00:00Z' }] },
      'POST /api/encyclopedia/deleted/restore': { restored: true }
    })
    const user = userEvent.setup()
    render(<DeletedPages />)
    await user.click(await screen.findByRole('button', { name: 'Bring back' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ topic: 'Sepsis' }))
  })
})
