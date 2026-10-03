/**
 * The built in-chat document (ADR 0014), run as a host would run it: one
 * HTML file, its script executed in a frame whose parent answers tool calls.
 * This is the only test that touches the build output, and it is skipped when
 * the output is not there.
 */

import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'

// vitest runs from apps/web; the built document lives in the MCP package.
const DOCUMENT = resolve(process.cwd(), '../mcp/src/vademecum_mcp/widgets/app.html')

const EMPTY_SHEET = {
  curated: { articles: [], source_configured: false, message: 'Nothing yet.' },
  worth_a_look: [],
  recent_flags: [],
  tiers: [
    { tier: 'low', pile_count: 0, item_count: 0 },
    { tier: 'mid', pile_count: 0, item_count: 0 },
    { tier: 'high', pile_count: 0, item_count: 0 }
  ],
  open_flag_count: 0,
  held: [],
  tutor: { eligible: 0, asked: 0, cycle_started_at: null },
  literature: { unread: 0 }
}

const ROUTES: Record<string, unknown> = {
  '/api/health': { status: 'ok', version: 't', schema_version: 6, database: 'ok', loopback_only: true, tenancy: 'single', model_mode: 'host' },
  '/api/today': EMPTY_SHEET,
  '/api/piles': [],
  '/api/flags': [],
  '/api/improvement-map': { topics: [], tiers: EMPTY_SHEET.tiers, unfiled_flag_count: 0 }
}

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
  document.head.innerHTML = ''
})

describe.skipIf(!existsSync(DOCUMENT))('the in-chat document', () => {
  it('mounts in a frame and asks the host, not the network, for its data', async () => {
    const html = readFileSync(DOCUMENT, 'utf8')
    const script = /<script type="module">([\s\S]*?)<\/script>\s*<\/body>/.exec(html)?.[1]
    const style = /<style>([\s\S]*?)<\/style>/.exec(html)?.[1]
    expect(script).toBeTruthy()
    expect(style).toContain(':root[data-theme="dark"]')

    const requested: { method: string; path: string }[] = []
    const parent = {
      postMessage: (message: { id?: number; method: string; params?: { name?: string; arguments?: { method: string; path: string } } }) => {
        const reply = (result: unknown) =>
          window.dispatchEvent(
            new MessageEvent('message', { data: { jsonrpc: '2.0', id: message.id, result }, source: window.parent })
          )
        if (message.method === 'ui/initialize') reply({ protocolVersion: '2025-11-21', hostContext: { theme: 'dark' } })
        if (message.method === 'tools/call') {
          const args = message.params?.arguments
          if (args) requested.push({ method: args.method, path: args.path })
          const path = args?.path.split('?')[0] ?? ''
          reply({ structuredContent: { ok: true, status: 200, body: ROUTES[path] ?? {} } })
        }
      }
    }
    vi.spyOn(window, 'parent', 'get').mockReturnValue(parent as unknown as Window)
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    document.body.innerHTML = '<div id="root"></div>'
    // The bundle is one self-contained module with no import or export left in
    // it, so it runs as a plain script.
    expect(script).not.toMatch(/^\s*(import|export)\s/m)
    new Function(script as string)()

    await vi.waitFor(() => expect(document.querySelector('.app')).not.toBeNull())
    await vi.waitFor(() => expect(requested.some((r) => r.path === '/api/today')).toBe(true))
    expect(fetchMock).not.toHaveBeenCalled()
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(document.documentElement.getAttribute('data-host')).toBe('chat')
    expect(document.body.textContent).toContain('Educational only')
    // No Model section inside a conversation, and nothing but app_request was called.
    const nav = [...document.querySelectorAll('nav a')].map((a) => a.textContent)
    expect(nav).toEqual(['Today', 'Tutor', 'Encyclopedia', 'Sources', 'Improvement Map', 'Case Series'])
    for (const call of requested) expect(call.path.startsWith('/api/')).toBe(true)

    // The tool that opened it says which page: the Tutor, here.
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { structuredContent: { app: 'vademecum', view: 'tutor' } } },
        source: window.parent
      })
    )
    await vi.waitFor(() =>
      expect(document.querySelector('nav a[aria-current="page"]')?.textContent).toBe('Tutor')
    )
    vi.unstubAllGlobals()
  })
})
