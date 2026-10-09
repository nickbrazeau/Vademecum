/**
 * The encyclopedia and the board Tutor (ADR 0023): a page with its sources
 * under every paragraph, Today's page to review, and a board question checked
 * locally with its explanation and citations.
 */

import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EncyclopediaPage } from '../src/components/EncyclopediaPage'
import { Construction } from '../src/pages/Construction'
import { Encyclopedia } from '../src/pages/Encyclopedia'
import { Tutor } from '../src/pages/Tutor'
import { Today } from '../src/pages/Today'

const PAGE = {
  id: 'ency_1', topic: 'sepsis lactate', title: 'Lactate in sepsis', specialty_id: 'infectious-disease',
  summary: 'Lactate above 2 mmol/L marks hypoperfusion.', point_count: 2, question_count: 1, status: 'current', status_detail: '', version: 1, compiled_at: '2026-10-03T00:00:00Z',
  sections: [{ heading: 'Thresholds', paragraphs: [{ text: 'A lactate above 2 mmol/L is abnormal in sepsis.', point_ids: ['lp1'] }] }],
  citations: [{ id: 'lp1', claim: 'Lactate above 2 is abnormal', support: 'evidence_supported', support_label: 'Evidence-supported', held: false, sources: [{ source_id: 's1', display_name: 'Sepsis lecture.pdf', locator: 'Page 3', quote: 'lactate above 2 mmol/L' }] }],
  literature: [{ record_id: 'rec1', cited: true, pmid: '30012345', doi: '', title: 'Lactate targets in septic shock', journal: 'Crit Care', published_on: '2025-01-15', url: '', priority: 'guideline', retracted: false, corrected: false }],
  literature_checked_at: '2026-10-03T00:00:00Z',
  literature_note: '',
  body_md: '', markdown: '# Lactate in sepsis', edited: false, edit_outdated: false, edited_at: null
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
    expect(screen.getByText(/Where this page comes from: \d+ learning point/)).toBeInTheDocument()
    expect(screen.getByText('Lactate above 2 is abnormal')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Literature reviewed for this page/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Lactate targets in septic shock' })).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/30012345/')
    expect(screen.getByText(/drawn on above/)).toBeInTheDocument()
  })
})

describe('the dissection agent card', () => {
  const idle = { status: 'idle', phase: '', pile_id: '', pile_title: '', coverage: null, batches_done: 0, points_built: 0, pages_compiled: 0, questions_written: 0, failures: 0, last_error: '', running: false, can_run: true, blocked_reason: '', encyclopedia: COUNTS, disclosure: 'Dissecting a pile is a standing consent: until you stop it, the agent sends batch after batch.' }
  it('offers a pile, starts with the disclosure shown, and reports progress', async () => {
    let started = false
    const calls = stub({})
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        const target = String(url).split('?')[0] ?? ''
        calls.push({ url: target, method, body: init?.body ? JSON.parse(String(init.body)) : null })
        const json = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
        if (target === '/api/encyclopedia/dissection' && method === 'POST') {
          started = true
          return json({ ...idle, status: 'running', phase: 'building', pile_id: 'p1', pile_title: 'The book', batches_done: 3, points_built: 31, pages_compiled: 4, questions_written: 9, running: true, coverage: { chars_total: 100, chars_covered: 12, percent: 12, complete: false } }, 202)
        }
        if (target === '/api/encyclopedia/dissection') return json(started ? { ...idle, status: 'running', phase: 'building', pile_id: 'p1', pile_title: 'The book', batches_done: 3, points_built: 31, pages_compiled: 4, questions_written: 9, running: true } : idle)
        if (target === '/api/piles') return json([{ id: 'p1', title: 'The book', tier: 'high' }])
        if (target === '/api/encyclopedia') return json({ entries: [], counts: COUNTS, can_compile: true, running: false, last_refresh: null, note: '', disclosure: '' })
        return json({})
      })
    )
    render(<Construction />)
    expect(await screen.findByText(/standing consent/)).toBeInTheDocument()
    const start = await screen.findByRole('button', { name: 'Dissect this pile' })
    await waitFor(() => expect(start).toBeEnabled())
    await userEvent.click(start)
    expect(await screen.findByText(/Working through/)).toBeInTheDocument()
    expect(screen.getByText(/3 batches built · 31 points · 4 pages compiled · 9 questions written/)).toBeInTheDocument()
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/encyclopedia/dissection')?.body).toEqual({ pile_id: 'all' })
    expect(screen.getByRole('button', { name: 'Stop the agent' })).toBeInTheDocument()
  })
})

describe('Today', () => {
  it('opens with a page to review and offers another', async () => {
    const calls = stub({
      '/api/today': { page: PAGE, encyclopedia: { ...COUNTS, entries: 2, message: '' }, recall: { card: null, unit: null }, held: { points: 0, questions: 0, needs_re_review: 0, reasons: [] }, literature: { unread: 0, updates: [], topic_count: 0, message: '' }, tutor: { eligible: 0, held: 0, answered_total: 0, cycle: CYCLE, message: '' }, recent_flags: [], open_flag_count: 0 },
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
  it('compiles from Construction, lists pages by subject in toggleable sections, and opens one', async () => {
    const UNSHELVED = { ...PAGE, id: 'ency_2', topic: 'gout', title: 'Gout', specialty_id: null }
    const calls = stub({
      '/api/encyclopedia': { entries: [UNSHELVED, PAGE], specialties: [{ id: 'cardiology', name: 'Cardiology' }, { id: 'infectious-disease', name: 'Infectious Disease' }], counts: COUNTS, can_compile: true, running: false, last_refresh: null, note: '', disclosure: 'Compiling sends, per topic, the learning points already built from your sources.' },
      '/api/encyclopedia/ency_1': PAGE,
      'POST /api/encyclopedia/compile': { entries: [PAGE], counts: COUNTS, can_compile: true, running: true, last_refresh: null, note: '', disclosure: '' }
    })
    render(<Construction />)
    expect(await screen.findByText(/Compiling sends, per topic/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Compile now' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST' && call.url === '/api/encyclopedia/compile')).toBe(true))
    cleanup()
    render(<Encyclopedia />)
    await screen.findByRole('navigation', { name: 'Contents' })
    // Subjects come in their own order; one with no page is not listed, and a page with none comes last.
    const contents = screen.getByRole('navigation', { name: 'Contents' })
    expect(within(contents).getAllByRole('link').map((link) => link.textContent)).toEqual(['Infectious Disease', 'Lactate in sepsis', 'Other topics', 'Gout'])
    const subject = document.getElementById('subject-infectious-disease') as HTMLElement
    expect(subject.tagName).toBe('DETAILS')
    expect(within(subject).getByText('Lactate above 2 mmol/L marks hypoperfusion.')).toBeInTheDocument()
    await userEvent.click(within(contents).getByRole('link', { name: 'Lactate in sepsis' }))
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
    await userEvent.click(await screen.findByRole('button', { name: /^Board questions/ }))
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
    await userEvent.click(await screen.findByRole('button', { name: /^Board questions/ }))
    expect(await screen.findByText('Explain retrieval practice.')).toBeInTheDocument()
  })
})


describe('the daily goal on Today', () => {
  it('says how many more, and says done once the goal is met', async () => {
    const { ReviewDashboard } = await import('../src/components/ReviewDashboard')
    const base = { days_in_a_row: 2, longest_run: 4, reviewed_today_already: true, today: { question: 5, card: 2, socratic: 0, page: 1 }, week_total: 30, all_time_total: 90, history: [] }
    const { unmount } = render(<ReviewDashboard dashboard={{ ...base, daily_goal: 20, goal_met: false, remaining_today: 12, today_total: 8 }} />)
    expect(screen.getByRole('status')).toHaveTextContent('12 more to reach today’s 20. 8 so far.')
    unmount()
    render(<ReviewDashboard dashboard={{ ...base, daily_goal: 8, goal_met: true, remaining_today: 0, today_total: 8 }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Done for today. 8 of 8 reviewed.')
  })
})
