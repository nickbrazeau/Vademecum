/**
 * Literature settings: what comes first (guidelines, preferred journals) and
 * watching a subspecialty from a menu.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LiteratureSettings } from '../src/components/LiteratureSettings'

const SETTINGS = { weekly_enabled: false, interval_hours: 168, preferred_journals: ['N Engl J Med', 'JAMA'], guidelines_first: true, enabled: true, running: false, provider: 'pubmed', unread: 0 }
const MAP = { topics: [], covered_topics: [], links: [], specialties: [{ id: 'infectious-disease', name: 'Infectious Disease' }, { id: 'cardiology', name: 'Cardiology' }], positions: [], confidences: [], unfiled_flag_count: 0, bank: {} }

function stubApi() {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const body = typeof init?.body === 'string' ? JSON.parse(init.body) : null
      calls.push({ url: String(url), method, body })
      const path = String(url).split('?')[0]
      let reply: unknown = {}
      if (path === '/api/literature/settings') reply = method === 'PUT' ? { ...SETTINGS, ...body } : SETTINGS
      else if (path === '/api/improvement-map') reply = MAP
      else if (path === '/api/literature/topics') reply = method === 'POST' ? { id: 't1', label: body.label, query: body.query, enabled: true, consecutive_failures: 0 } : []
      else if (path === '/api/literature/suggestions') reply = []
      return new Response(JSON.stringify(reply), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('what comes first', () => {
  it('shows the preferred journals and guidelines-first, and saves a change', async () => {
    const calls = stubApi()
    render(<LiteratureSettings onChecked={() => undefined} />)
    expect(await screen.findByRole('button', { name: 'Stop preferring N Engl J Med' })).toBeInTheDocument()
    expect(screen.getByLabelText('Practice guidelines first')).toBeChecked()
    await userEvent.click(screen.getByRole('button', { name: 'Stop preferring JAMA' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'PUT' && c.url === '/api/literature/settings')).toBe(true))
    const put = calls.find((c) => c.method === 'PUT')!
    expect(put.body).toMatchObject({ preferred_journals: ['N Engl J Med'], weekly_enabled: false })
  })

  it('adds a journal by its PubMed abbreviation', async () => {
    const calls = stubApi()
    render(<LiteratureSettings onChecked={() => undefined} />)
    await screen.findByText('Practice guidelines first')
    await userEvent.type(screen.getByLabelText(/Add a journal/), 'Nature')
    await userEvent.click(screen.getByRole('button', { name: 'Prefer this journal' }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PUT')?.body).toMatchObject({ preferred_journals: ['N Engl J Med', 'JAMA', 'Nature'] }))
  })
})

describe('watching a subspecialty', () => {
  it('offers the subspecialties as a menu and watches the chosen one', async () => {
    const calls = stubApi()
    render(<LiteratureSettings onChecked={() => undefined} />)
    const menu = await screen.findByLabelText('Watch a subspecialty')
    await userEvent.selectOptions(menu, 'infectious-disease')
    await userEvent.click(screen.getByRole('button', { name: 'Watch it' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/literature/topics')).toBe(true))
    expect(calls.find((c) => c.method === 'POST')!.body).toEqual({ label: 'Infectious Disease', query: 'infectious disease' })
  })
})
