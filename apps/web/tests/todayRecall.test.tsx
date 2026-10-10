/**
 * Today's one thing to recall (feedback of 9 October): a card from where the learner model
 * points, answered like any flashcard, then the suggested step.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { TodayRecall } from '../src/components/TodayRecall'
import { todayRecall } from '../src/lib/normalize'

afterEach(() => vi.unstubAllGlobals())

const RECALL = {
  card: { id: 'fc_1', entry_id: 'ency_1', topic: 'myocarditis', title: 'Myocarditis', front: 'Which viruses most often cause myocarditis?', back: 'Coxsackie B and other enteroviruses; also influenza and SARS-CoV-2.', point_ids: ['lp_1'], status: 'eligible' },
  citations: [{ id: 'lp_1', claim: 'c', support_label: 'Source-supported', sources: [] }],
  reasons: [],
  kind: 'recall',
  deck: 3,
  intervals: { again: '10 min', good: '1 day' },
  unit: {
    key: 'page:ency_1', title: 'Myocarditis', topic: 'myocarditis', entry_id: 'ency_1', specialty_id: 'cardiology', state: 'fading', state_label: 'Fading',
    understood: 0.7, recall: 0.5, half_life_days: 3, confidence: 0.4, need: 1, open_flags: 0, board_ready: 2, cards_ready: 3, evidence: [],
    next: { kind: 'board', label: 'Board questions', why: 'It is slipping. Recalling it now, just as it fades, makes it last longer.' }
  }
}

describe('Recall one thing', () => {
  it('asks first, shows the answer on request, and records how it went as a flashcard review', async () => {
    const posts: unknown[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if (init?.method === 'POST') posts.push(JSON.parse(String(init.body)))
        return new Response(JSON.stringify({ review: {}, next: {} }), { status: 200, headers: { 'Content-Type': 'application/json' } })
      })
    )
    const user = userEvent.setup()
    render(<TodayRecall recall={todayRecall(RECALL)} />)
    expect(screen.getByText('Which viruses most often cause myocarditis?')).toBeInTheDocument()
    expect(screen.queryByText(/Coxsackie B/)).not.toBeInTheDocument()
    expect(screen.getByText('Fading')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show answer' }))
    expect(screen.getByText(/Coxsackie B/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Got it/ }))
    await waitFor(() => expect(posts).toEqual([{ card_id: 'fc_1', rating: 'good', practise: false }]))
    expect(await screen.findByText(/comes back in 1 day/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Board questions' })).toBeInTheDocument()
  })

  it('says plainly when there is nothing to recall yet', () => {
    render(<TodayRecall recall={todayRecall({ card: null, unit: null })} />)
    expect(screen.getByText(/Nothing to recall yet/)).toBeInTheDocument()
  })
})

describe('Recall one thing, refreshed', () => {
  it('reports an answer so Today can bring a fresh card on return', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ review: {}, next: {} }), { status: 200, headers: { 'Content-Type': 'application/json' } })))
    const answered = vi.fn()
    const user = userEvent.setup()
    render(<TodayRecall recall={todayRecall(RECALL)} onAnswered={answered} />)
    await user.click(screen.getByRole('button', { name: 'Show answer' }))
    await user.click(screen.getByRole('button', { name: /Again/ }))
    await waitFor(() => expect(answered).toHaveBeenCalledTimes(1))
  })
})
