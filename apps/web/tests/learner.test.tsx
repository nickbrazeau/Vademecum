/**
 * The learner model on the Improvement Map (ADR 0031): where to go next, each step's
 * reason and its one action; the map coloured by what you know; and board questions
 * where you need them most.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { StudyNext, TopicKnowledge } from '../src/components/StudyNext'
import { learnerModel } from '../src/lib/normalize'
import { takePendingTutor } from '../src/lib/pageLink'
import { Tutor } from '../src/pages/Tutor'

afterEach(() => vi.unstubAllGlobals())

const UNIT = {
  key: 'page:ency_1',
  title: 'Cirrhosis',
  topic: 'cirrhosis',
  entry_id: 'ency_1',
  specialty_id: 'gastroenterology',
  state: 'forming',
  state_label: 'Still forming',
  understood: 0.3,
  recall: 0.8,
  half_life_days: 1,
  confidence: 0.4,
  need: 2,
  open_flags: 2,
  board_ready: 3,
  cards_ready: 0,
  evidence: ['Board questions: 0 of 2 right.', '2 open flags.'],
  next: { kind: 'socratic', label: 'Talk it through', why: 'Explaining why, out loud, builds the understanding that the misses point to.' }
}

const MODEL = { plan: [UNIT], units: [UNIT], states: [{ state: 'forming', label: 'Still forming', count: 1 }], by_name: { cirrhosis: 'page:ency_1' }, by_entry: { ency_1: 'forming' } }

describe('Where to go next', () => {
  it('names the topic, its state, why, and one step that opens the Tutor on it', async () => {
    const onNavigate = vi.fn()
    const user = userEvent.setup()
    render(<StudyNext model={learnerModel(MODEL)} onNavigate={onNavigate} />)
    expect(screen.getByText('Cirrhosis')).toBeInTheDocument()
    expect(screen.getByText('Still forming')).toBeInTheDocument()
    expect(screen.getByText(/Explaining why, out loud/)).toBeInTheDocument()
    expect(screen.getByText(/Board questions: 0 of 2 right\. 2 open flags\./)).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Talk it through' }))
    expect(onNavigate).toHaveBeenCalledWith('tutor')
    expect(window.location.search).toBe('?mode=socratic&page=ency_1')
  })

  it('says plainly when nothing stands out, without a count or a demand', () => {
    render(<StudyNext model={learnerModel({ plan: [], units: [], states: [], by_name: {}, by_entry: {} })} />)
    expect(screen.getByText(/Nothing stands out yet/)).toBeInTheDocument()
  })

  it('shows one topic as words and meters, never as a score', () => {
    render(<TopicKnowledge unit={learnerModel(MODEL).units[0]!} />)
    expect(screen.getByRole('img', { name: 'Understood: low' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: 'Holding now: high' })).toBeInTheDocument()
    expect(screen.getByText('some')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/\d+%/)
  })
})

describe('Board questions where you need them most', () => {
  it('asks with focus=need', async () => {
    window.history.pushState(null, '', '/tutor')
    takePendingTutor() // the step followed in the first test is not this one's
    const urls: string[] = []
    const question = { id: 'bq_1', entry_id: 'ency_1', topic: 'cirrhosis', stem: 'A stem?', options: ['a', 'b', 'c', 'd', 'e'], point_ids: [] }
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        urls.push(`${init?.method ?? 'GET'} ${url} ${init?.body ?? ''}`)
        const path = String(url).split('?')[0]
        const payload =
          path === '/api/tutor/board/next'
            ? { question, cycle: { number: 1, position: 0, total: 1 }, empty_reason: '' }
            : path === '/api/tutor/board/answer'
              ? { attempt: { correct: true, chosen_index: 0 }, question: { ...question, answer_index: 0, explanation: 'x' }, citations: [] }
              : {}
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
      })
    )
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /Board questions/ }))
    await user.click(await screen.findByRole('button', { name: 'Where you need it most' }))
    await waitFor(() => expect(urls.some((u) => u.includes('/api/tutor/board/next?focus=need'))).toBe(true))
    expect(await screen.findByText('A stem?')).toBeInTheDocument()
  })
})
