/**
 * Where the Case Series comes from (ADR 0022; feedback of 10 October): the disclosure
 * before the switch, a switch per source, Refresh now, a feed of one's own added only after
 * its host is named and confirmed, and nothing to set on Foris.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { HubSettings } from '../src/pages/CaseSeries'

const CATALOGUE = [
  { id: 'nejm_cpc', name: 'Case Records of the Massachusetts General Hospital', short: 'NEJM Case Records', publisher: 'The New England Journal of Medicine', home: 'https://www.nejm.org/' },
  { id: 'nejm_cps', name: 'Clinical Problem-Solving', short: 'NEJM Clinical Problem-Solving', publisher: 'The New England Journal of Medicine', home: 'https://www.nejm.org/' },
  { id: 'cps', name: 'The Clinical Problem Solvers', short: 'Clinical Problem Solvers', publisher: 'The Clinical Problem Solvers', home: 'https://clinicalproblemsolving.com/' },
  { id: 'curbsiders', name: 'The Curbsiders Internal Medicine Podcast', short: 'The Curbsiders', publisher: 'The Curbsiders', home: 'https://thecurbsiders.com/' }
]

const PODCAST = {
  id: 'case_1', series: 'curbsiders', series_name: 'The Curbsiders Internal Medicine Podcast', series_short: 'The Curbsiders', publisher: 'The Curbsiders',
  subseries: 'Hotcakes', external_id: '540', title: '#540 Hotcakes: Toxic alcohols', url: 'https://thecurbsiders.com/540', credit: 'Paul Williams',
  published_on: '2026-09-28', status: 'synthesised', status_detail: '', one_liner: 'Toxic alcohol ingestion with a wide osmolar gap.',
  points: [{ point: 'Measure both gaps early.', quote: 'Check the anion gap and the osmolar gap' }], think_first: ['What widens an osmolar gap?'],
  specialty_id: 'nephrology', synthesised_at: '2026-10-03T00:00:00Z', first_seen_at: '2026-10-03T00:00:00Z', snippet: 'Pearls: Check the anion gap…'
}
const ARTICLE = {
  ...PODCAST, id: 'case_2', series: 'nejm_cpc', series_name: 'Case Records of the Massachusetts General Hospital', series_short: 'NEJM Case Records',
  publisher: 'The New England Journal of Medicine', subseries: '', external_id: '10.1056/NEJMcpc1', title: 'Case 27-2026: A 4-Year-Old Boy with Falls',
  url: 'https://www.nejm.org/doi/full/10.1056/NEJMcpc1', credit: 'A Author', published_on: '2026-09-24', one_liner: '', points: [],
  think_first: ['What causes ataxia and fatigue in a preschooler?'], snippet: ''
}

const COUNTS = { total: 2, pending: 0, by_series: { nejm_cpc: 1, nejm_cps: 0, cps: 0, curbsiders: 1 } }
const CREDIT = 'Every case here is the work of its authors, hosts and publishers.'

function settings(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false, interval_hours: 6, series: { nejm_cpc: true, nejm_cps: true, cps: true, curbsiders: true },
    fetches_here: true, can_synthesise: true, running: false, last_refresh: null, counts: COUNTS, catalogue: CATALOGUE,
    note: '', disclosure: 'Keeping the hub updated sends fixed public requests, with nothing of yours in them.', credit: CREDIT, ...overrides
  }
}

function stubApi(options: { settings?: Record<string, unknown> } = {}) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const target = String(url)
      calls.push({ url: target, method, body: init?.body ? JSON.parse(String(init.body)) : null })
      const json = (payload: unknown, status = 200) =>
        new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
      if (target.startsWith('/api/cases/settings')) return json(settings({ ...options.settings, ...(method === 'PUT' ? { enabled: true } : {}) }))
      if (target === '/api/cases/refresh') return json(settings({ ...options.settings, running: true }), 202)
      if (target === '/api/cases/feeds') {
        const sent = JSON.parse(String(init?.body))
        return sent.confirm
          ? json({ feed: { id: 'feed_1', name: 'Core IM', host: 'example.org' }, new: 3 })
          : json({ url: sent.url, host: 'example.org', ask: 'Vademecum would contact example.org to read this feed. Add it?' })
      }
      if (target.startsWith('/api/cases')) {
        const params = new URL(target, 'http://local').searchParams
        const series = params.get('series')
        const entries = [ARTICLE, PODCAST].filter((entry) => !series || entry.series === series)
        return json({ entries, catalogue: CATALOGUE, counts: COUNTS, credit: CREDIT })
      }
      return json({ error: { code: 'not_found', message: 'No such route.' } }, 404)
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('where the Case Series comes from', () => {
  it('shows the disclosure before the switch, a switch per source, and no list of cases', async () => {
    stubApi()
    render(<HubSettings onChanged={() => undefined} />)
    expect(await screen.findByText(/sends fixed public requests, with nothing of yours in them/)).toBeInTheDocument()
    expect(screen.getByLabelText(/The Curbsiders Internal Medicine Podcast/)).toBeChecked()
    expect(screen.queryByText('#540 Hotcakes: Toxic alcohols')).not.toBeInTheDocument()
  })

  it('refreshes on request and turns the hub on', async () => {
    const calls = stubApi()
    const user = userEvent.setup()
    render(<HubSettings onChanged={() => undefined} />)
    await user.click(await screen.findByRole('button', { name: 'Refresh now' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST' && call.url === '/api/cases/refresh')).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Turn the hub on' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT' && call.url === '/api/cases/settings')).toBe(true))
    expect(calls.find((call) => call.method === 'PUT')?.body).toMatchObject({ enabled: true, interval_hours: 6 })
  })

  it('names the host a feed would contact, and adds it only on a yes', async () => {
    const calls = stubApi()
    const user = userEvent.setup()
    render(<HubSettings onChanged={() => undefined} />)
    await user.type(await screen.findByLabelText(/Add a feed of teaching cases/), 'https://example.org/feed.xml')
    await user.click(screen.getByRole('button', { name: 'Check this address' }))
    expect(await screen.findByText(/would contact example\.org/)).toBeInTheDocument()
    expect(calls.filter((call) => call.url === '/api/cases/feeds').map((call) => (call.body as { confirm?: boolean }).confirm)).toEqual([undefined])
    await user.click(screen.getByRole('button', { name: 'Yes, add it' }))
    expect(await screen.findByText(/Added, with 3 cases/)).toBeInTheDocument()
    expect((calls.filter((call) => call.url === '/api/cases/feeds').at(-1)?.body as { confirm: boolean }).confirm).toBe(true)
  })

  it('on Foris offers nothing to set', async () => {
    stubApi({ settings: { fetches_here: false } })
    render(<HubSettings onChanged={() => undefined} />)
    await waitFor(() => expect(screen.queryByRole('button', { name: /Turn the hub/ })).not.toBeInTheDocument())
    expect(screen.queryByLabelText(/Add a feed/)).not.toBeInTheDocument()
  })
})
