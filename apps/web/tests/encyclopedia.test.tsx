/**
 * The encyclopedia and the board Tutor (ADR 0023): a page with its sources
 * under every paragraph, Today's page to review, and a board question checked
 * locally with its explanation and citations.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EncyclopediaPage } from '../src/components/EncyclopediaPage'
import { Encyclopedia } from '../src/pages/Encyclopedia'
import { Tutor } from '../src/pages/Tutor'
import { Today } from '../src/pages/Today'

const PAGE = {
  id: 'ency_1', topic: 'sepsis lactate', title: 'Lactate in sepsis', specialty_id: 'infectious-disease',
  summary: 'Lactate above 2 mmol/L marks hypoperfusion.', point_count: 2, question_count: 1, status: 'current', status_detail: '', version: 1, compiled_at: '2026-10-03T00:00:00Z',
  sections: [{ heading: 'Thresholds', paragraphs: [{ text: 'A lactate above 2 mmol/L is abnormal in sepsis.', point_ids: ['lp1'] }] }],
  citations: [{ id: 'lp1', claim: 'Lactate above 2 is abnormal', support: 'evidence_supported', support_label: 'Evidence-supported', held: false, sources: [{ source_id: 's1', display_name: 'Sepsis lecture.pdf', locator: 'Page 3', quote: 'lactate above 2 mmol/L' }] }]
}
const COUNTS = { entries: 1, stale: 0, questions_eligible: 1, questions_held: 0, questions_total: 1 }
const QUESTION = {
  id: 'bq1', entry_id: 'ency_1', topic: 'sepsis lactate', title: 'Lactate in sepsis', status: 'eligible', point_ids: ['lp1'], objective: 'Interpret lactate.',
  stem: 'A 60-year-old woman has a lactate of 3.1 mmol/L with suspected sepsis. Which of the following is the most accurate interpretation?',
  options: [{ letter: 'A', text: 'Hypoperfusion is likely' }, { letter: 'B', text: 'The value is normal' }, { letter: 'C', text: 'Liver failure only' }, { letter: 'D', text: 'It excludes sepsis' }, { letter: 'E', text: 'Laboratory error' }]
}
const CYCLE = { cycle_number: 1, position: 0, total: 3, remaining: 3, exhausted: false }

function stub(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const target = String(url)
      calls.push({ url: target, method, body: init?.body ? JSON.parse(String(init.body)) : null })
      const path = target.split('?')[0] ?? ''
      const payload = routes[`${method} ${path}`] ?? routes[path] ?? {}
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('an encyclopedia page', () => {
  it('shows the sources under every paragraph and the points it rests on', () => {
    render(<EncyclopediaPage page={PAGE} />)
    expect(screen.getByRole('heading', { name: 'Lactate in sepsis' })).toBeInTheDocument()
    expect(screen.getByText('A lactate above 2 mmol/L is abnormal in sepsis.')).toBeInTheDocument()
    expect(screen.getByText(/From Sepsis lecture.pdf, Page 3/)).toBeInTheDocument()
    expect(screen.getByText('The points this page rests on')).toBeInTheDocument()
    expect(screen.getByText('Lactate above 2 is abnormal')).toBeInTheDocument()
  })
})

describe('Today', () => {
  it('opens with a page to review and offers another', async () => {
    const calls = stub({
      '/api/today': { page: PAGE, encyclopedia: { ...COUNTS, entries: 2, message: '' }, worth_a_look: [], held: { points: 0, questions: 0, needs_re_review: 0, reasons: [] }, literature: { unread: 0, updates: [], topic_count: 0, message: '' }, tutor: { eligible: 0, held: 0, answered_total: 0, cycle: CYCLE, message: '' }, recent_flags: [], open_flag_count: 0 },
      '/api/encyclopedia/page': { page: { ...PAGE, id: 'ency_2', title: 'Noradrenaline in shock' }, counts: COUNTS, message: '' },
      '/api/literature/settings': {}
    })
    render(<Today reloadToken={0} />)
    expect(await screen.findByRole('heading', { name: /a page to review/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Lactate in sepsis' })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Another page' }))
    expect(await screen.findByRole('heading', { name: 'Noradrenaline in shock' })).toBeInTheDocument()
    expect(calls.some((call) => call.url.includes('/api/encyclopedia/page?random=true'))).toBe(true)
  })

  it('says where pages come from when there are none', async () => {
    stub({ '/api/today': { page: null, encyclopedia: { ...COUNTS, entries: 0, message: 'There are no encyclopedia pages yet.' } }, '/api/literature/settings': {} })
    render(<Today reloadToken={0} />)
    expect(await screen.findByText(/no encyclopedia pages yet/)).toBeInTheDocument()
  })
})

describe('the Encyclopedia tab', () => {
  it('lists pages with the compile disclosure and opens one', async () => {
    const calls = stub({
      '/api/encyclopedia': { entries: [PAGE], counts: COUNTS, can_compile: true, running: false, last_refresh: null, note: '', disclosure: 'Compiling sends, per topic, the learning points already built from your sources.' },
      '/api/encyclopedia/ency_1': PAGE,
      'POST /api/encyclopedia/compile': { entries: [PAGE], counts: COUNTS, can_compile: true, running: true, last_refresh: null, note: '', disclosure: '' }
    })
    render(<Encyclopedia />)
    expect(await screen.findByText(/Compiling sends, per topic/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Compile now' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST' && call.url === '/api/encyclopedia/compile')).toBe(true))
    await userEvent.click(screen.getByRole('link', { name: 'Lactate in sepsis' }))
    expect(await screen.findByText('A lactate above 2 mmol/L is abnormal in sepsis.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /All pages/ }))
    expect(await screen.findByRole('heading', { name: 'Pages' })).toBeInTheDocument()
  })
})

describe('the board Tutor', () => {
  it('asks a vignette, checks the choice locally, explains, cites, and advances', async () => {
    const calls = stub({
      '/api/tutor/board/next': { question: QUESTION, cycle: CYCLE, last_attempt: null, history_count: 0, empty_reason: '' },
      'POST /api/tutor/board/answer': {
        attempt: { id: 'batt1', question_id: 'bq1', chosen_index: 1, chosen_letter: 'B', correct: false, created_at: '2026-10-03T00:00:00Z' },
        question: { ...QUESTION, answer_index: 0, answer_letter: 'A', explanation: 'A: above 2 mmol/L marks hypoperfusion. B is wrong because 3.1 is above the threshold.' },
        citations: PAGE.citations
      },
      'POST /api/tutor/board/advance': { question: { ...QUESTION, id: 'bq2', stem: 'A second vignette.' }, cycle: { ...CYCLE, remaining: 2, position: 1 }, last_attempt: null, history_count: 0, empty_reason: '' }
    })
    render(<Tutor />)
    expect(await screen.findByText(/A 60-year-old woman/)).toBeInTheDocument()
    expect(screen.getByText(/no model is involved in checking it/)).toBeInTheDocument()
    const check = screen.getByRole('button', { name: 'Check answer' })
    expect(check).toBeDisabled()
    await userEvent.click(screen.getByLabelText(/The value is normal/))
    await userEvent.click(check)
    expect(await screen.findByRole('heading', { name: /Incorrect — the answer is A/ })).toBeInTheDocument()
    expect(screen.getByText(/marks hypoperfusion/)).toBeInTheDocument()
    expect(screen.getByText('Lactate above 2 is abnormal')).toBeInTheDocument()
    const answer = calls.find((call) => call.method === 'POST' && call.url === '/api/tutor/board/answer')
    expect(answer?.body).toEqual({ question_id: 'bq1', choice: 1 })
    await userEvent.click(within(screen.getByRole('heading', { name: /Incorrect/ }).closest('section') as HTMLElement).getByRole('button', { name: 'Next question' }))
    expect(await screen.findByText('A second vignette.')).toBeInTheDocument()
  })

  it('falls back to the open-answer Tutor when there are no board questions', async () => {
    stub({
      '/api/tutor/board/next': { question: null, cycle: CYCLE, last_attempt: null, history_count: 0, empty_reason: 'There are no board questions yet.' },
      '/api/tutor/next': { question: { id: 'q1', prompt: 'Explain retrieval practice.', support: 'evidence_supported', status: 'eligible' }, cycle: CYCLE }
    })
    render(<Tutor />)
    expect(await screen.findByText('Explain retrieval practice.')).toBeInTheDocument()
  })
})
