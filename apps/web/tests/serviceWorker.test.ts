/**
 * The worker itself, not just its policy: a private response must never reach
 * `Cache.put`, and the only way to be sure is to run the fetch handler and
 * watch the cache.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type Handler = (event: unknown) => void

const handlers = new Map<string, Handler>()
const put = vi.fn(async (_request: Request, _response: Response) => undefined)
const addAll = vi.fn(async (_assets: string[]) => undefined)
const match = vi.fn(
  async (_request: Request | string): Promise<Response | undefined> => undefined
)

const origin = window.location.origin

function fetchEvent(url: string, init: RequestInit = {}) {
  const { mode, ...rest } = init
  const request = new Request(new URL(url, origin), rest)
  // `new Request(url, { mode: 'navigate' })` is forbidden by the spec, so the
  // one thing the handler branches on is set directly.
  if (mode !== undefined) Object.defineProperty(request, 'mode', { value: mode })
  let responded: Promise<Response> | null = null
  const event = {
    request,
    respondWith: (value: Promise<Response>) => {
      responded = value
    }
  }
  return { event, responded: () => responded }
}

beforeEach(async () => {
  handlers.clear()
  put.mockClear()
  vi.spyOn(window, 'addEventListener').mockImplementation(((type: string, handler: Handler) => {
    handlers.set(type, handler)
  }) as typeof window.addEventListener)

  vi.stubGlobal('caches', {
    open: vi.fn(async () => ({ put, addAll, match })),
    keys: vi.fn(async () => []),
    delete: vi.fn(async () => true),
    match
  })
  vi.stubGlobal('skipWaiting', vi.fn())
  Object.assign(window, { skipWaiting: vi.fn(), clients: { claim: vi.fn() } })

  vi.resetModules()
  await import('../src/sw/sw')
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the fetch handler', () => {
  it('registers install, activate and fetch', () => {
    expect([...handlers.keys()].sort()).toEqual(['activate', 'fetch', 'install'])
  })

  it('does not even respond to an API request, let alone cache it', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    for (const path of ['/api/today', '/api/flags', '/api/piles?tier=high']) {
      const { event, responded } = fetchEvent(path)
      handlers.get('fetch')?.(event)
      expect(responded(), path).toBeNull()
    }
    expect(put).not.toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('ignores a non-GET request entirely', () => {
    const { event, responded } = fetchEvent('/index.html', { method: 'POST' })
    handlers.get('fetch')?.(event)
    expect(responded()).toBeNull()
    expect(put).not.toHaveBeenCalled()
  })

  it('ignores cross-origin requests', () => {
    const { event, responded } = fetchEvent('https://example.com/tracker.js')
    handlers.get('fetch')?.(event)
    expect(responded()).toBeNull()
  })

  it('caches a hashed build asset it fetched successfully', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('console.log(1)', { status: 200 }))
    )
    const { event, responded } = fetchEvent('/assets/main-Brue7D13.js')
    handlers.get('fetch')?.(event)
    await responded()
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('refuses to cache a static path whose response says no-store', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('secret', { status: 200, headers: { 'Cache-Control': 'no-store' } }))
    )
    const { event, responded } = fetchEvent('/assets/main-CTbR10CB.css')
    handlers.get('fetch')?.(event)
    await responded()
    expect(put).not.toHaveBeenCalled()
  })

  it('refuses to cache a failed response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('nope', { status: 500 }))
    )
    const { event, responded } = fetchEvent('/assets/main-Zz00Aa11.js')
    handlers.get('fetch')?.(event)
    await responded()
    expect(put).not.toHaveBeenCalled()
  })

  it('leaves any same-origin path outside the shell alone', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
    for (const path of [
      '/attachments/x',
      '/exports/x',
      '/notes/x',
      '/private',
      '/assets/main.js',
      '/assets/notes.json'
    ]) {
      const { event, responded } = fetchEvent(path)
      handlers.get('fetch')?.(event)
      expect(responded(), path).toBeNull()
    }
    expect(put).not.toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('caches the icons the manifest names', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('png', { status: 200 }))
    )
    for (const icon of ['/icon-192.png', '/icon-512.png', '/apple-touch-icon.png']) {
      const { event, responded } = fetchEvent(icon)
      handlers.get('fetch')?.(event)
      await responded()
    }
    expect(put).toHaveBeenCalledTimes(3)
  })
})

describe('navigations', () => {
  it('serves a client route from the network but does not store it', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html></html>', { status: 200 }))
    )
    const { event, responded } = fetchEvent('/piles', { mode: 'navigate' })
    handlers.get('fetch')?.(event)
    const response = await responded()
    expect(response?.status).toBe(200)
    // The shell is what gets stored; `/piles` is a route, not a file.
    expect(put).not.toHaveBeenCalled()
  })

  it('falls back to the cached shell when the backend is not there', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      })
    )
    match.mockResolvedValueOnce(new Response('shell', { status: 200 }))
    const { event, responded } = fetchEvent('/notes/x', { mode: 'navigate' })
    handlers.get('fetch')?.(event)
    await responded()
    expect(match).toHaveBeenCalledWith('/index.html')
  })

  it('stores the shell itself when it is the navigation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('<html></html>', { status: 200 }))
    )
    const { event, responded } = fetchEvent('/', { mode: 'navigate' })
    handlers.get('fetch')?.(event)
    await responded()
    expect(put).toHaveBeenCalledTimes(1)
  })

  it('never navigates an API path through the worker', () => {
    const { event, responded } = fetchEvent('/api/today', { mode: 'navigate' })
    handlers.get('fetch')?.(event)
    expect(responded()).toBeNull()
  })
})

describe('the install handler', () => {
  it('precaches only the static shell', async () => {
    let work: Promise<unknown> | null = null
    handlers.get('install')?.({ waitUntil: (value: Promise<unknown>) => (work = value) })
    await work
    expect(addAll).toHaveBeenCalledTimes(1)
    const cached = addAll.mock.calls[0]?.[0] ?? []
    expect(cached).toEqual(['/', '/index.html', '/manifest.webmanifest', '/icon.svg'])
    for (const entry of cached) {
      expect(entry.startsWith('/api')).toBe(false)
    }
  })
})
