/**
 * The shell: three sections, one keystroke to capture, and honest states when
 * the backend is not answering.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App, isQuickFlagShortcut } from '../src/App'

const EMPTY_SHEET = {
  curated: {
    articles: [],
    source_configured: false,
    message:
      'No curated-article source is configured, so there is nothing here yet. Vademecum does not fetch or generate articles on its own.'
  },
  worth_a_look: [],
  recent_flags: [],
  tiers: [
    { tier: 'low', pile_count: 0, item_count: 0 },
    { tier: 'mid', pile_count: 0, item_count: 0 },
    { tier: 'high', pile_count: 0, item_count: 0 }
  ],
  open_flag_count: 0
}

const ROUTED: Record<string, unknown> = {
  '/api/today': EMPTY_SHEET,
  '/api/piles': [],
  '/api/flags': [],
  '/api/improvement-map': { topics: [], tiers: EMPTY_SHEET.tiers, unfiled_flag_count: 0 }
}

/** A stand-in for the local backend. No test here reaches a network. */
function stubApi() {
  const fetchMock = vi.fn(async (url: string) => {
    const path = String(url).split('?')[0] ?? ''
    return new Response(JSON.stringify(ROUTED[path] ?? {}), {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

describe('the quick-flag shortcut', () => {
  it('is Cmd-K or Ctrl-K and nothing else', () => {
    const base = { key: 'k', metaKey: false, ctrlKey: false, altKey: false }
    expect(isQuickFlagShortcut({ ...base, metaKey: true })).toBe(true)
    expect(isQuickFlagShortcut({ ...base, ctrlKey: true })).toBe(true)
    expect(isQuickFlagShortcut({ ...base, key: 'K', metaKey: true })).toBe(true)
    expect(isQuickFlagShortcut(base)).toBe(false)
    expect(isQuickFlagShortcut({ ...base, key: 'j', metaKey: true })).toBe(false)
    expect(isQuickFlagShortcut({ ...base, metaKey: true, altKey: true })).toBe(false)
  })

  it('opens capture from anywhere in the app', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await screen.findByRole('heading', { name: /worth a look/i })

    expect(screen.queryByRole('heading', { name: /flag a knowledge gap/i })).not.toBeInTheDocument()
    await user.keyboard('{Meta>}k{/Meta}')
    expect(await screen.findByRole('heading', { name: /flag a knowledge gap/i })).toBeVisible()
  })

  it('is also a thumb-sized button, because a phone has no Cmd key', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('button', { name: /flag a gap/i }))
    expect(await screen.findByRole('heading', { name: /flag a knowledge gap/i })).toBeVisible()
  })
})

describe('the shell', () => {
  it('offers Today, Tutor, Sources and the Improvement Map', () => {
    stubApi()
    render(<App />)
    const nav = screen.getByRole('navigation', { name: /sections/i })
    expect(nav).toHaveTextContent('Today')
    expect(nav).toHaveTextContent('Sources')
    expect(nav).toHaveTextContent('Tutor')
    expect(nav).toHaveTextContent('Improvement Map')
  })

  it('navigates without a page load', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: 'Sources' }))
    expect(window.location.pathname).toBe('/sources')
    expect(await screen.findByRole('heading', { name: /new pile/i })).toBeVisible()
  })

  it('says it is educational, not a substitute for clinical judgment', () => {
    stubApi()
    render(<App />)
    expect(screen.getByRole('contentinfo')).toHaveTextContent(
      /not a substitute for clinical judgment/i
    )
  })
})

describe('unavailable states', () => {
  it('says the backend is not answering instead of showing nothing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    render(<App />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/not answering on this mac/i)
    expect(alert).toHaveTextContent(/outcome could not be confirmed/i)
  })

  it('offers to try again rather than reloading the page', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    render(<App />)
    expect(await screen.findByRole('button', { name: /try again/i })).toBeVisible()
  })

  it('shows an offline notice when the device is offline', async () => {
    stubApi()
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<App />)
    await waitFor(() =>
      expect(screen.getByRole('status')).toHaveTextContent(/this device is offline/i)
    )
  })
})

describe('the cover sheet', () => {
  it('says the literature section is empty until a topic check finds papers', async () => {
    stubApi()
    render(<App />)
    expect(await screen.findByText(/new papers appear here only when a topic check finds them/i)).toBeVisible()
  })

  it('does not invent anything to fill an empty workspace', async () => {
    stubApi()
    render(<App />)
    expect(await screen.findByRole('heading', { name: /worth a look/i })).toBeVisible()
    expect(screen.queryByRole('list', { name: /generated points/i })).not.toBeInTheDocument()
  })
})
