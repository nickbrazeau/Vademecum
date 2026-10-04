/**
 * The Case Series hub (ADR 0022): the disclosure before the switch, credit on
 * every case, teaching points with their quotes, a title-only entry showing
 * prompts, a filter per series, and Refresh now.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CaseSeries } from '../src/pages/CaseSeries'

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

describe('the Case Series hub', () => {
  it('shows the disclosure before the switch and credits every case by its link and maker', async () => {
    stubApi()
    render(<CaseSeries />)
    expect(await screen.findByText(/sends fixed public requests, with nothing of yours in them/)).toBeInTheDocument()
    expect(screen.queryByText(CREDIT)).not.toBeInTheDocument()
    expect(screen.getByText('Cases sourced from Open Education Materials.')).toBeInTheDocument()
    expect(await screen.findByText(/By Paul Williams · The Curbsiders/)).toBeInTheDocument()
    expect(screen.getByText(/By A Author · The New England Journal of Medicine/)).toBeInTheDocument()
    const original = screen.getAllByRole('link', { name: 'Open the original' })
    expect(original[0]).toHaveAttribute('href', 'https://www.nejm.org/doi/full/10.1056/NEJMcpc1')
    expect(original[0]).toHaveAttribute('rel', 'noopener noreferrer')
  })

  it('shows teaching points with their quotes, and prompts alone for a title-only case', async () => {
    stubApi()
    render(<CaseSeries />)
    const podcast = (await screen.findByText('#540 Hotcakes: Toxic alcohols')).closest('li') as HTMLElement
    expect(within(podcast).getByText('Measure both gaps early.')).toBeInTheDocument()
    expect(within(podcast).getByText('Check the anion gap and the osmolar gap')).toBeInTheDocument()
    expect(within(podcast).getByText('Hotcakes')).toBeInTheDocument()
    const article = screen.getByText('Case 27-2026: A 4-Year-Old Boy with Falls').closest('li') as HTMLElement
    expect(within(article).queryByRole('heading', { name: 'Teaching points' })).not.toBeInTheDocument()
    expect(within(article).getByText('What causes ataxia and fatigue in a preschooler?')).toBeInTheDocument()
    expect(within(article).getByText(/offers only the title here/)).toBeInTheDocument()
  })

  it('filters by series and refreshes on request', async () => {
    const calls = stubApi()
    const user = userEvent.setup()
    render(<CaseSeries />)
    await screen.findByText('#540 Hotcakes: Toxic alcohols')
    await user.click(screen.getByRole('button', { name: /NEJM Case Records \(1\)/ }))
    await waitFor(() => expect(screen.queryByText('#540 Hotcakes: Toxic alcohols')).not.toBeInTheDocument())
    expect(calls.some((call) => call.url.includes('series=nejm_cpc'))).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Refresh now' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'POST' && call.url === '/api/cases/refresh')).toBe(true))
    await user.click(screen.getByRole('button', { name: 'Turn the hub on' }))
    await waitFor(() => expect(calls.some((call) => call.method === 'PUT' && call.url === '/api/cases/settings')).toBe(true))
    const put = calls.find((call) => call.method === 'PUT')
    expect(put?.body).toMatchObject({ enabled: true, interval_hours: 6 })
  })

  it('on Foris shows the cases and offers no switch', async () => {
    stubApi({ settings: { fetches_here: false, note: 'This Vademecum shows the cases Domi gathered.' } })
    render(<CaseSeries />)
    expect(await screen.findByText('#540 Hotcakes: Toxic alcohols')).toBeInTheDocument()
    expect(screen.queryByText(/cases Domi gathered/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Keeping the hub updated' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Turn the hub/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Refresh now' })).not.toBeInTheDocument()
  })
})
