/**
 * Flashcards and settings (ADR 0024): a front, the back on request, why the
 * card came up, a rating that draws the next; and a tab picker that keeps
 * Today and Settings and hides what the owner unticks.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../src/App'
import { Flashcards } from '../src/pages/Flashcards'
import { Settings } from '../src/pages/Settings'

const CARD = { id: 'card_1', entry_id: 'ency_1', topic: 'sepsis lactate', title: 'Lactate in sepsis', front: 'Lactate above ___ mmol/L marks hypoperfusion.', back: '2 mmol/L.', point_ids: ['lp1'] }
const CITATIONS = [{ id: 'lp1', claim: 'Lactate above 2 is abnormal', support: 'evidence_supported', support_label: 'Evidence-supported', held: false, sources: [{ source_id: 's1', display_name: 'Sepsis lecture.pdf', locator: 'Page 3', quote: 'lactate above 2 mmol/L' }] }]
const DRAW = { card: CARD, reasons: ['You flagged this topic as a gap.'], citations: CITATIONS, deck: 12, empty_reason: '' }
const NEXT = { card: { ...CARD, id: 'card_2', front: 'First-line vasopressor in septic shock?' }, reasons: ['New card.'], citations: CITATIONS, deck: 12, empty_reason: '' }
const TABS = [
  { name: 'today', label: 'Today', fixed: true }, { name: 'tutor', label: 'Tutor', fixed: false }, { name: 'flashcards', label: 'Flashcards', fixed: false },
  { name: 'encyclopedia', label: 'Encyclopedia', fixed: false }, { name: 'map', label: 'Improvement Map', fixed: false }, { name: 'cases', label: 'Case Series', fixed: false }, { name: 'podcasts', label: 'Podcast', fixed: false },
  { name: 'sources', label: 'Sources', fixed: false }, { name: 'model', label: 'Model', fixed: false }, { name: 'settings', label: 'Settings', fixed: true }
]

function stub(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const target = String(url)
      const path = target.split('?')[0] ?? ''
      calls.push({ url: target, method, body: init?.body ? JSON.parse(String(init.body)) : null })
      const payload = routes[`${method} ${path}`] ?? routes[path] ?? {}
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('flashcards', () => {
  it('shows the front, says why it came up, reveals the back with its sources, and a rating draws the next', async () => {
    const calls = stub({ '/api/flashcards/next': DRAW, 'POST /api/flashcards/review': { review: { rating: 'good' }, next: NEXT } })
    render(<Flashcards />)
    expect(await screen.findByText('Lactate above ___ mmol/L marks hypoperfusion.')).toBeInTheDocument()
    expect(screen.getByText(/You flagged this topic as a gap/)).toBeInTheDocument()
    expect(screen.queryByText('2 mmol/L.')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }))
    expect(screen.getByText('2 mmol/L.')).toBeInTheDocument()
    expect(screen.getByText('Lactate above 2 is abnormal')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^Got it/ }))
    expect(await screen.findByText('First-line vasopressor in septic shock?')).toBeInTheDocument()
    expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ card_id: 'card_1', rating: 'good', practise: false })
    expect(screen.queryByText('2 mmol/L.')).not.toBeInTheDocument()
  })

  it('spaces the cards: the gap on each answer, the page hidden until the answer, and a rest with Keep practising', async () => {
    const counts = { ready: 0, new_left_today: 0, new_total: 3, learned: 9, next_ready_at: '2026-10-07T09:00:00Z', new_per_day: 20 }
    const calls = stub({
      '/api/flashcards/next': { card: null, reasons: [], citations: [], deck: 12, empty_reason: '', kind: 'rest', counts, intervals: {} }
    })
    render(<Flashcards />)
    expect(await screen.findByRole('heading', { name: 'All caught up' })).toBeInTheDocument()
    expect(screen.getByText(/0 ready now · 0 new left today \(of 20 a day\) · 9 of 12 started/)).toBeInTheDocument()
    stub({ '/api/flashcards/next': { ...DRAW, kind: 'practice', counts, intervals: { again: '10 min', good: '3 days' } } })
    await userEvent.click(screen.getByRole('button', { name: 'Keep practising' }))
    expect(await screen.findByText('Lactate above ___ mmol/L marks hypoperfusion.')).toBeInTheDocument()
    expect(screen.queryByText(/Lactate in sepsis/)).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Show the answer' }))
    expect(screen.getByRole('link', { name: 'Lactate in sepsis' })).toHaveAttribute('href', '/encyclopedia?page=ency_1')
    expect(screen.getByRole('button', { name: /Again · 10 min/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Got it · next in 3 days/ })).toBeInTheDocument()
    expect(calls.length).toBeGreaterThan(0)
  })

  it('says where cards come from when there are none', async () => {
    stub({ '/api/flashcards/next': { card: null, reasons: [], citations: [], deck: 0, empty_reason: 'There are no flashcards yet.' } })
    render(<Flashcards />)
    expect(await screen.findByText(/no flashcards yet/)).toBeInTheDocument()
  })
})

describe('settings', () => {
  it('lets the owner untick a tab, keeps Today and Settings fixed, and saves', async () => {
    const calls = stub({
      '/api/preferences': { visible_tabs: TABS.map((tab) => tab.name), tabs: TABS },
      'PUT /api/preferences': { visible_tabs: ['today', 'tutor', 'settings'], tabs: TABS }
    })
    const saved: { visible_tabs: string[] }[] = []
    render(<Settings onSaved={(visible) => saved.push(visible)} />)
    const today = await screen.findByLabelText(/Today/)
    expect(today).toBeChecked()
    expect(today).toBeDisabled()
    await userEvent.click(screen.getByLabelText('Podcast'))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true))
    const put = calls.find((call) => call.method === 'PUT')?.body as { visible_tabs: string[] }
    expect(put.visible_tabs).not.toContain('podcasts')
    expect(put.visible_tabs).toContain('today')
    expect(saved[0]?.visible_tabs).toEqual(['today', 'tutor', 'settings'])
    expect(await screen.findByText(/^Saved\. The tabs you chose/)).toBeInTheDocument()
  })

  it('hides unticked tabs from the shell navigation', async () => {
    stub({ '/api/preferences': { visible_tabs: ['today', 'tutor', 'settings'], tabs: TABS }, '/api/today': {}, '/api/health': { model_mode: 'codex', tenancy: 'single' } })
    render(<App />)
    const nav = screen.getByRole('navigation', { name: /sections/i })
    await waitFor(() => expect(nav).not.toHaveTextContent('Case Series'))
    expect(nav).toHaveTextContent('Today')
    expect(nav).toHaveTextContent('Tutor')
    expect(nav).toHaveTextContent('Settings')
    expect(nav).not.toHaveTextContent('Encyclopedia')
  })
})


describe('tab order', () => {
  it('moves a tab up, keeps Today first and Settings last, and saves the order', async () => {
    const calls = stub({
      '/api/preferences': { visible_tabs: TABS.map((tab) => tab.name), order: TABS.map((tab) => tab.name), tabs: TABS },
      'PUT /api/preferences': { visible_tabs: TABS.map((tab) => tab.name), order: TABS.map((tab) => tab.name), tabs: TABS }
    })
    render(<Settings />)
    await screen.findByLabelText(/Today/)
    expect(screen.queryByRole('button', { name: 'Move Today up' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Move Tutor up' })).toBeDisabled()
    await userEvent.click(screen.getByRole('button', { name: 'Move Flashcards up' }))
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT')).toBe(true))
    const put = calls.find((call) => call.method === 'PUT')?.body as { order: string[] }
    expect(put.order.slice(0, 3)).toEqual(['today', 'flashcards', 'tutor'])
    expect(put.order.at(-1)).toBe('settings')
  })

  it('orders the shell navigation the owner’s way', async () => {
    const { inOrder } = await import('../src/App')
    const routes = [{ name: 'today' }, { name: 'tutor' }, { name: 'map' }, { name: 'settings' }]
    expect(inOrder(routes, ['today', 'map', 'tutor', 'settings']).map((r) => r.name)).toEqual(['today', 'map', 'tutor', 'settings'])
    expect(inOrder(routes, []).map((r) => r.name)).toEqual(['today', 'tutor', 'map', 'settings'])
  })
})
