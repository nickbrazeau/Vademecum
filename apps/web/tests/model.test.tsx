/**
 * The Model page.
 *
 * Every state the connection can be in has to be legible without knowing what
 * Codex, JSON-RPC or a device code is, and the one-time code has to leave no
 * trace in this browser. Both are asserted here rather than described.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { App } from '../src/App'
import { Model, isSafeExternalUrl, planLabel, resetLabel, windowLabel } from '../src/pages/Model'
import type { ModelStatus } from '../src/lib/types'

const SIGNED_OUT: ModelStatus = {
  state: 'signed_out',
  signed_in: false,
  plan: null,
  rate_limits: null,
  login_pending: false,
  detail:
    'Not signed in to ChatGPT. Signing in lets future model features use your Codex plan. No API key is involved.',
  reason: null,
  checked_at: '2026-08-30T10:00:00Z'
}

const SIGNED_IN: ModelStatus = {
  ...SIGNED_OUT,
  state: 'signed_in',
  signed_in: true,
  plan: 'pro',
  detail: 'Signed in to ChatGPT through Codex.',
  rate_limits: {
    primary: { used_percent: 35, resets_at: '2026-08-30T18:00:00Z', window_minutes: 300 },
    secondary: { used_percent: 62, resets_at: '2026-09-04T10:00:00Z', window_minutes: 10080 },
    limited: false,
    limit_reason: null
  }
}

const RATE_LIMITED: ModelStatus = {
  ...SIGNED_IN,
  state: 'rate_limited',
  detail: 'Your Codex usage limit has been reached. Everything stored on this Mac is unaffected.',
  rate_limits: {
    primary: { used_percent: 100, resets_at: '2026-08-30T18:00:00Z', window_minutes: 300 },
    secondary: null,
    limited: true,
    limit_reason: 'rate_limit_reached'
  }
}

const UNAVAILABLE: ModelStatus = {
  ...SIGNED_OUT,
  state: 'unavailable',
  detail: 'Codex is not installed where Vademecum expects it.',
  reason: 'codex_not_found'
}

const DEVICE_LOGIN = {
  verification_url: 'https://example.test/device',
  user_code: 'WXYZ-1234',
  login_id: 'login-abc123',
  status: 'pending'
}

// A route body, or a function returning one, so a test can let the backend's
// answer change between checks -- which is the whole of the device-code flow.
type Body = unknown | (() => unknown)
type Route = { status?: Body; login?: Body; cancel?: Body; restart?: Body }

// Enough of the rest of the app for the shell tests to render a real <App />.
const EMPTY_TIERS = [
  { tier: 'low', pile_count: 0, item_count: 0 },
  { tier: 'mid', pile_count: 0, item_count: 0 },
  { tier: 'high', pile_count: 0, item_count: 0 }
]

const OTHER_ROUTES: Record<string, unknown> = {
  '/api/today': {
    curated: { articles: [], source_configured: false, message: 'Nothing here yet.' },
    worth_a_look: [],
    recent_flags: [],
    tiers: EMPTY_TIERS,
    open_flag_count: 0
  },
  '/api/piles': [],
  '/api/flags': [],
  '/api/improvement-map': { topics: [], tiers: EMPTY_TIERS, unfiled_flag_count: 0 }
}

/** A stand-in for the local backend. Nothing here reaches a network. */
function stubApi(routes: Route = {}) {
  const calls: { method: string; path: string }[] = []
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const path = String(url).split('?')[0] ?? ''
    const method = init?.method ?? 'GET'
    calls.push({ method, path })
    const chosen =
      path === '/api/model/status'
        ? (routes.status ?? SIGNED_OUT)
        : path === '/api/model/login'
          ? (routes.login ?? DEVICE_LOGIN)
          : path === '/api/model/login/cancel'
            ? (routes.cancel ?? { status: 'canceled' })
            : path === '/api/model/restart'
              ? (routes.restart ?? SIGNED_OUT)
              : (OTHER_ROUTES[path] ?? {})
    const body = typeof chosen === 'function' ? (chosen as () => unknown)() : chosen
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    })
  })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, calls }
}

afterEach(() => vi.unstubAllGlobals())

describe('the connection states', () => {
  it('says it is checking before it knows', () => {
    stubApi()
    render(<Model />)
    expect(screen.getByText('Checking…')).toBeVisible()
    expect(screen.getByRole('heading', { name: /model connection/i })).toBeVisible()
  })

  it('says signed out, and offers ChatGPT sign-in', async () => {
    stubApi()
    render(<Model />)
    expect(await screen.findByText('Not signed in')).toBeVisible()
    expect(screen.getByRole('button', { name: /sign in with chatgpt/i })).toBeVisible()
    expect(screen.getByText(/no api key is involved/i)).toBeVisible()
  })

  it('says signed in, names the plan, and shows both usage windows', async () => {
    stubApi({ status: SIGNED_IN })
    render(<Model />)
    expect(await screen.findByText('Signed in')).toBeVisible()
    expect(screen.getByText('Pro plan')).toBeVisible()

    const usage = within(screen.getByRole('region', { name: /plan usage/i }))
    expect(usage.getByText(/35% used/)).toBeVisible()
    expect(usage.getByText(/62% used/)).toBeVisible()
    // Reset times are shown when the backend could read one.
    expect(usage.getAllByText(/resets/i).length).toBe(2)
    // No sign-in offer while signed in.
    expect(screen.queryByRole('button', { name: /sign in with chatgpt/i })).not.toBeInTheDocument()
  })

  it('says the usage limit is reached without hiding the rest', async () => {
    stubApi({ status: RATE_LIMITED })
    render(<Model />)
    expect(await screen.findByText('Usage limit reached')).toBeVisible()
    expect(screen.getByText(/model actions will not work until it resets/i)).toBeVisible()
    expect(screen.getByText(/100% used/)).toBeVisible()
  })

  it('says unavailable in plain words and offers a restart', async () => {
    stubApi({ status: UNAVAILABLE })
    render(<Model />)
    expect(await screen.findByText('Unavailable')).toBeVisible()
    expect(screen.getByText(/codex is not installed/i)).toBeVisible()
    expect(screen.getByText(/no note, question or answer was sent anywhere/i)).toBeVisible()
    expect(screen.getByRole('button', { name: /restart the connection/i })).toBeVisible()
  })

  it('says the backend is not answering rather than showing nothing', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    render(<Model />)
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/not answering on this mac/i)
    // The explanation of what would be sent survives the failure.
    expect(screen.getByRole('region', { name: /what this sends/i })).toBeVisible()
  })

  it('says the device is offline instead of blaming Codex', async () => {
    stubApi()
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    render(<Model />)
    await waitFor(() =>
      expect(screen.getByText(/this device is offline/i)).toBeVisible()
    )
    expect(screen.getByText(/nothing saved on this mac is affected/i)).toBeVisible()
  })

  it('still says it is offline when the request itself could not be made', async () => {
    // Which is the case that actually happens: offline means the fetch throws,
    // and "the backend is not answering" would be the wrong explanation.
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false)
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    render(<Model />)
    expect(await screen.findByRole('alert')).toBeVisible()
    expect(screen.getByText(/this device is offline/i)).toBeVisible()
  })
})

describe('device-code sign-in', () => {
  it('shows the verification link and the one-time code', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))

    const panel = within(await screen.findByRole('region', { name: /finish signing in/i }))
    expect(panel.getByTestId('device-code')).toHaveTextContent('WXYZ-1234')
    const link = panel.getByRole('link', { name: /example\.test\/device/i })
    expect(link).toHaveAttribute('href', 'https://example.test/device')
    expect(link).toHaveAttribute('rel', expect.stringContaining('noreferrer'))
  })

  it('does not open a browser for you', async () => {
    stubApi()
    const open = vi.fn()
    vi.stubGlobal('open', open)
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await screen.findByTestId('device-code')
    expect(open).not.toHaveBeenCalled()
  })

  it('keeps the code out of browser storage entirely', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await screen.findByTestId('device-code')

    const stored = JSON.stringify([
      { ...window.localStorage },
      { ...window.sessionStorage }
    ])
    for (const secret of ['WXYZ-1234', 'login-abc123', 'example.test']) {
      expect(stored).not.toContain(secret)
    }
    expect(window.localStorage.length).toBe(0)
    expect(window.sessionStorage.length).toBe(0)
  })

  it('says the code is not saved and what to do if the page is closed', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    expect(await screen.findByText(/not saved anywhere in this browser/i)).toBeVisible()
  })

  it('cancels without the browser having kept a login id', async () => {
    const { calls } = stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await user.click(await screen.findByRole('button', { name: /cancel sign-in/i }))

    await waitFor(() =>
      expect(screen.queryByTestId('device-code')).not.toBeInTheDocument()
    )
    expect(calls).toContainEqual({ method: 'POST', path: '/api/model/login/cancel' })
  })

  it('offers to cancel a sign-in that was started before this page loaded', async () => {
    stubApi({ status: { ...SIGNED_OUT, login_pending: true } })
    render(<Model />)
    const pending = await screen.findByRole('region', { name: /a sign-in is waiting/i })
    expect(pending).toHaveTextContent(/the one-time code is not kept/i)
    expect(within(pending).getByRole('button', { name: /cancel sign-in/i })).toBeVisible()
    // And there is no code to show, because none was kept.
    expect(screen.queryByTestId('device-code')).not.toBeInTheDocument()
  })

  it('dismisses the code once the check comes back signed in', async () => {
    // The bug this replaces: "I have finished" kept `login` in state, so the
    // panel went on asking for a code the connection had already accepted.
    let signedIn = false
    stubApi({ status: () => (signedIn ? SIGNED_IN : SIGNED_OUT) })
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    expect(await screen.findByTestId('device-code')).toBeVisible()

    signedIn = true
    await user.click(screen.getByRole('button', { name: /i have finished/i }))

    await waitFor(() => expect(screen.queryByTestId('device-code')).not.toBeInTheDocument())
    expect(await screen.findByText('Signed in')).toBeVisible()
    expect(screen.queryByRole('region', { name: /finish signing in/i })).not.toBeInTheDocument()
  })

  it('dismisses the code when the check comes back rate limited', async () => {
    // Rate limited is a plan state, not an auth one: the sign-in worked.
    let limited = false
    stubApi({ status: () => (limited ? RATE_LIMITED : SIGNED_OUT) })
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await screen.findByTestId('device-code')

    limited = true
    await user.click(screen.getByRole('button', { name: /i have finished/i }))

    await waitFor(() => expect(screen.queryByTestId('device-code')).not.toBeInTheDocument())
    expect(await screen.findByText('Usage limit reached')).toBeVisible()
  })

  it('keeps the code when the check still says signed out', async () => {
    // The ordinary case of pressing the button early. Losing the code here
    // would mean cancelling and starting again for no reason.
    const { calls } = stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await screen.findByTestId('device-code')
    const before = calls.filter((call) => call.path === '/api/model/status').length

    await user.click(screen.getByRole('button', { name: /i have finished/i }))
    await waitFor(() =>
      expect(calls.filter((call) => call.path === '/api/model/status').length).toBeGreaterThan(
        before
      )
    )
    expect(screen.getByTestId('device-code')).toHaveTextContent('WXYZ-1234')
  })

  it('keeps the code while the backend still reports the sign-in as pending', async () => {
    // `login_pending` only becomes true once the sign-in has started, which is
    // also when the sign-in button goes away.
    let pending = false
    const { calls } = stubApi({
      status: () => ({ ...SIGNED_OUT, login_pending: pending }),
      login: () => {
        pending = true
        return DEVICE_LOGIN
      }
    })
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    await screen.findByTestId('device-code')
    const before = calls.filter((call) => call.path === '/api/model/status').length

    await user.click(screen.getByRole('button', { name: /i have finished/i }))
    await waitFor(() =>
      expect(calls.filter((call) => call.path === '/api/model/status').length).toBeGreaterThan(
        before
      )
    )
    expect(screen.getByTestId('device-code')).toBeVisible()
    // And the "a sign-in is waiting" card does not appear alongside it: the
    // code is right there, so there is nothing to explain.
    expect(screen.queryByRole('region', { name: /a sign-in is waiting/i })).not.toBeInTheDocument()
  })

  it('reports a failed sign-in without inventing a state', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        if ((init?.method ?? 'GET') === 'POST') {
          return new Response(
            JSON.stringify({
              error: { code: 'timeout', message: 'Codex did not answer in time.', fields: [] }
            }),
            { status: 504, headers: { 'Content-Type': 'application/json' } }
          )
        }
        return new Response(JSON.stringify(SIGNED_OUT), {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        })
      })
    )
    const user = userEvent.setup()
    render(<Model />)
    await user.click(await screen.findByRole('button', { name: /sign in with chatgpt/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/did not answer in time/i)
    expect(screen.queryByTestId('device-code')).not.toBeInTheDocument()
  })
})

describe('retry and restart', () => {
  it('checks again without reloading the page', async () => {
    const { calls } = stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await screen.findByText('Not signed in')
    await user.click(screen.getByRole('button', { name: /check again/i }))
    await waitFor(() =>
      expect(calls.filter((call) => call.path === '/api/model/status').length).toBeGreaterThan(1)
    )
  })

  it('restarts the connection explicitly', async () => {
    const { calls } = stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await screen.findByText('Not signed in')
    await user.click(screen.getByRole('button', { name: /restart the connection/i }))
    await waitFor(() =>
      expect(calls).toContainEqual({ method: 'POST', path: '/api/model/restart' })
    )
  })
})

describe('what this sends', () => {
  it('separates connection controls from explicit Build and Grade and opted-in topic checks', async () => {
    stubApi()
    render(<Model />)
    const section = within(screen.getByRole('region', { name: /what this sends/i }))
    expect(
      section.getByText(/sign-in and status controls do not send your notes or answers/i)
    ).toBeVisible()
    expect(section.getByText(/Build includes source excerpts and follow-up evidence and question checks/i)).toBeVisible()
    expect(section.getByText(/weekly literature checks, if you enable them/i)).toBeVisible()
  })

  it('does not pretend the account check reaches no network', async () => {
    // It reaches one. Codex makes the request, and saying "nothing is sent"
    // would be read as "no network traffic", which is false as soon as this
    // page loads. The narrow claim -- no learning content -- is the true one.
    stubApi()
    render(<Model />)
    const section = screen.getByRole('region', { name: /what this sends/i })
    expect(section).toHaveTextContent(/codex contacts openai to answer them/i)
    expect(section.textContent ?? '').not.toMatch(/nothing is sent/i)
  })

  it('says no API key is used and that Codex holds the credential', () => {
    stubApi()
    render(<Model />)
    const section = within(screen.getByRole('region', { name: /what this sends/i }))
    expect(section.getByText(/no api key is used/i)).toBeVisible()
    expect(section.getByText(/vademecum never sees a token/i)).toBeVisible()
  })
})

describe('accessibility', () => {
  it('reaches every action with the keyboard alone', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<Model />)
    await screen.findByText('Not signed in')

    const signIn = screen.getByRole('button', { name: /sign in with chatgpt/i })
    signIn.focus()
    expect(signIn).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await screen.findByTestId('device-code')).toBeVisible()

    await user.tab()
    // Focus lands on something focusable inside the page, not on nothing.
    expect(document.activeElement).not.toBe(document.body)
  })

  it('announces the connection state to a screen reader as it changes', async () => {
    stubApi({ status: SIGNED_IN })
    render(<Model />)
    const live = await screen.findByText('Signed in')
    const region = live.closest('[aria-live]')
    expect(region).not.toBeNull()
    expect(region).toHaveAttribute('aria-live', 'polite')
    expect(region).toHaveAttribute('role', 'status')
  })

  it('gives every panel a heading a screen reader can navigate by', async () => {
    stubApi({ status: SIGNED_IN })
    render(<Model />)
    await screen.findByText('Signed in')
    for (const name of [/model connection/i, /plan usage/i, /what this sends/i]) {
      expect(screen.getByRole('region', { name })).toBeVisible()
    }
  })

  it('describes usage as text, not only as a bar', async () => {
    stubApi({ status: SIGNED_IN })
    render(<Model />)
    await screen.findByText(/35% used/)
    // The bar is decoration and is hidden from assistive technology.
    const bars = document.querySelectorAll('.usage-bar')
    expect(bars.length).toBe(2)
    for (const bar of bars) expect(bar).toHaveAttribute('aria-hidden', 'true')
  })
})

describe('the shell', () => {
  it('offers Model in the section navigation', () => {
    stubApi()
    render(<App />)
    expect(screen.getByRole('navigation', { name: /sections/i })).toHaveTextContent('Model')
  })

  it('navigates to it without a page load', async () => {
    stubApi()
    const user = userEvent.setup()
    render(<App />)
    await user.click(screen.getByRole('link', { name: 'Model' }))
    expect(window.location.pathname).toBe('/model')
    expect(await screen.findByRole('heading', { name: /model connection/i })).toBeVisible()
  })
})

describe('the labels', () => {
  it('names a plan the way its owner would', () => {
    expect(planLabel('plus')).toBe('Plus')
    expect(planLabel('prolite')).toBe('Pro Lite')
    expect(planLabel(null)).toBeNull()
    // An unknown plan is shown as sent rather than dropped or guessed at.
    expect(planLabel('some_new_tier')).toBe('some_new_tier')
  })

  it('describes a window in ordinary words', () => {
    expect(windowLabel(300)).toBe('Every 5 hours')
    expect(windowLabel(60)).toBe('This hour')
    expect(windowLabel(10080)).toBe('This week')
    expect(windowLabel(1440)).toBe('Today')
    expect(windowLabel(null)).toBe('Usage')
  })

  it('shows nothing rather than a wrong reset time', () => {
    expect(resetLabel(null)).toBeNull()
    expect(resetLabel('not a date')).toBeNull()
    expect(resetLabel('2026-08-30T18:00:00Z')).not.toBeNull()
  })

  it('renders only an https verification link', () => {
    expect(isSafeExternalUrl('https://example.test/device')).toBe(true)
    expect(isSafeExternalUrl('http://example.test/device')).toBe(false)
    // eslint-disable-next-line no-script-url
    expect(isSafeExternalUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeExternalUrl('not a url')).toBe(false)
  })
})
