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
  const topics: { id: string; label: string; query: string; enabled: boolean; consecutive_failures: number }[] = []
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
      else if (path === '/api/literature/topics') {
        if (method === 'POST') topics.push({ id: `t${topics.length + 1}`, label: body.label, query: body.query, enabled: true, consecutive_failures: 0 })
        reply = method === 'POST' ? topics[topics.length - 1] : topics
      } else if (path.startsWith('/api/literature/topics/') && method === 'PATCH') {
        const topic = topics.find((t) => path.endsWith(t.id))
        if (topic) topic.enabled = body.enabled
        reply = topic
      }
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

describe('watching subspecialties', () => {
  it('lists every subspecialty with a switch, watches several, and shows which are on', async () => {
    const calls = stubApi()
    render(<LiteratureSettings onChecked={() => undefined} />)
    const id = await screen.findByRole('switch', { name: 'Watch Infectious Disease' })
    const cardio = screen.getByRole('switch', { name: 'Watch Cardiology' })
    expect(id).not.toBeChecked()
    await userEvent.click(id)
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Watch Infectious Disease' })).toBeChecked())
    await userEvent.click(cardio)
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Watch Cardiology' })).toBeChecked())
    expect(screen.getByText('2 on')).toBeInTheDocument()
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.body)).toEqual([
      { label: 'Infectious Disease', query: 'infectious disease' },
      { label: 'Cardiology', query: 'cardiology' }
    ])
    // Off keeps the topic and turns it off.
    await userEvent.click(screen.getByRole('switch', { name: 'Watch Cardiology' }))
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Watch Cardiology' })).not.toBeChecked())
    expect(calls.find((c) => c.method === 'PATCH')?.body).toEqual({ enabled: false })
  })
})
