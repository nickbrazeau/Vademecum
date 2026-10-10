/**
 * New in the literature on Today (feedback of 10 October): Reviewed it, Next article with a
 * replacement in its place, and thumbs that tune what comes next.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Today } from '../src/pages/Today'

afterEach(() => vi.unstubAllGlobals())

const paper = (id: string, title: string) => ({
  id, rating: 0, topic_id: 'ltp_1', topic_label: 'Sepsis', record_id: `lrc_${id}`, state: 'unread', why_relevant: '', first_seen_at: '2026-10-09T00:00:00Z',
  checked_at: null, pmid: id, doi: null, title, journal: 'Lancet', abstract: '', published_on: null, publication_types: [], retracted: false, corrected: false,
  is_notice: false, correction_notes: [], url: '', priority: 'other'
})

describe('New in the literature', () => {
  it('Next article sets one aside and brings another in its place; thumbs are sent', async () => {
    const calls: { method: string; url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        calls.push({ method, url: String(url), body: init?.body ? JSON.parse(String(init.body)) : null })
        const path = String(url).split('?')[0]
        const payload =
          path === '/api/today'
            ? { literature: { unread: 1, updates: [paper('1', 'First paper')], topic_count: 1, message: '' }, recall: { card: null, unit: null } }
            : path === '/api/literature/updates/next'
              ? { update: paper('2', 'Second paper'), fetching: false }
              : {}
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
      })
    )
    const user = userEvent.setup()
    render(<Today reloadToken={0} />)
    const first = (await screen.findByText('First paper')).closest('li') as HTMLElement
    await user.click(within(first).getByRole('button', { name: 'More like this' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toEqual({ rating: 1 }))
    expect(within(first).getByRole('button', { name: 'More like this' })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(first).getByRole('button', { name: 'Next article' }))
    expect(await screen.findByText('Second paper')).toBeInTheDocument()
    expect(screen.queryByText('First paper')).not.toBeInTheDocument()
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ state: 'dismissed' })
    expect(calls.some((c) => c.url.includes('/api/literature/updates/next?exclude=1'))).toBe(true)
    expect(screen.getAllByRole('button', { name: 'Reviewed it' }).length).toBeGreaterThan(0)
  })
})
