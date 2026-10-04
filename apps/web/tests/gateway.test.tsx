/**
 * The desk behind the gateway (ADR 0011): one learner among many, so the
 * shell offers a sign-out and not the owner's Model page. On the Mac,
 * nothing changes.
 */

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../src/App'

const EMPTY_SHEET = {
  worth_a_look: [],
  worth_a_look_message: '',
  held: { points: 0, questions: 0, needs_re_review: 0, reasons: [] },
  literature: { unread: 0, updates: [], topic_count: 0, message: '' },
  tutor: { eligible: 0, held: 0, answered_total: 0, cycle: { cycle_number: 0, position: 0, total: 0, remaining: 0, exhausted: false }, message: '' },
  recent_flags: [],
  open_flag_count: 0,
  sources: { piles: 0, sources: 0, coverage_meaning: '' },
  confidences: { low: 0, mid: 0, high: 0 }
}

function stubApi(health: Record<string, unknown>) {
  const routed: Record<string, unknown> = {
    '/api/health': health,
    '/api/today': EMPTY_SHEET,
    '/api/piles': [],
    '/api/flags': []
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const path = String(url).split('?')[0] ?? ''
      return new Response(JSON.stringify(routed[path] ?? {}), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      })
    })
  )
}

afterEach(() => vi.unstubAllGlobals())

describe('the shell behind the gateway', () => {
  it('offers a sign-out and hides the Model page in multi tenancy', async () => {
    stubApi({ status: 'ok', tenancy: 'multi', model_mode: 'host' })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Sign out' })).toBeInTheDocument())
    expect(screen.queryByRole('link', { name: 'Model' })).not.toBeInTheDocument()
    const form = screen.getByRole('button', { name: 'Sign out' }).closest('form')
    expect(form).toHaveAttribute('method', 'post')
    expect(form).toHaveAttribute('action', '/logout')
  })

  it('changes nothing on the owner’s Mac', async () => {
    stubApi({ status: 'ok', tenancy: 'single', model_mode: 'codex' })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  })

  it('treats a health check that says nothing as the owner’s Mac', async () => {
    stubApi({ status: 'ok' })
    render(<App />)
    await waitFor(() => expect(screen.getByRole('link', { name: 'Settings' })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: 'Sign out' })).not.toBeInTheDocument()
  })
})
