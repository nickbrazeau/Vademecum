/**
 * The chat host bridge (ADR 0014): the same app, with every request turned
 * into one call to the server's `app_request` tool.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, IN_CHAT_FILES_MESSAGE, IN_CHAT_REMOVAL_MESSAGE, api } from '../src/lib/api'
import { callTool, detectHost, markInChat, onToolResult, resetHost } from '../src/lib/host'

interface Sent {
  jsonrpc: string
  id?: number
  method: string
  params?: Record<string, unknown>
}

/** A stand-in for an MCP Apps host: answers initialize and tools/call over postMessage. */
function fakeHost(answer: (name: string, args: Record<string, unknown>) => unknown) {
  const sent: Sent[] = []
  const parent = {
    postMessage: (message: Sent) => {
      sent.push(message)
      const reply = (result: unknown) =>
        window.dispatchEvent(
          new MessageEvent('message', { data: { jsonrpc: '2.0', id: message.id, result }, source: window.parent })
        )
      if (message.method === 'ui/initialize') {
        reply({ protocolVersion: '2025-11-21', hostContext: { theme: 'dark' } })
      } else if (message.method === 'tools/call') {
        const params = message.params as { name: string; arguments: Record<string, unknown> }
        reply({ structuredContent: answer(params.name, params.arguments) })
      }
    }
  }
  vi.spyOn(window, 'parent', 'get').mockReturnValue(parent as unknown as Window)
  return sent
}

beforeEach(() => {
  resetHost()
  document.documentElement.removeAttribute('data-theme')
})

afterEach(() => {
  resetHost()
  vi.unstubAllGlobals()
})

describe('in a browser tab', () => {
  it('there is no host, and fetch is used', async () => {
    expect(await detectHost()).toBe('none')
    await expect(callTool('app_request', {})).rejects.toThrow('No chat host')
  })
})

describe('inside an MCP Apps host', () => {
  it('initialises, takes the theme, and routes every request through app_request', async () => {
    markInChat()
    const seen: { name: string; args: Record<string, unknown> }[] = []
    const sent = fakeHost((name, args) => {
      seen.push({ name, args })
      return { ok: true, status: 200, body: [] }
    })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)

    expect(await detectHost()).toBe('mcp-app')
    expect(document.documentElement.getAttribute('data-theme')).toBe('dark')
    expect(sent.map((m) => m.method)).toEqual(['ui/initialize', 'ui/notifications/initialized'])

    expect(await api.listFlags()).toEqual([])
    await api.createFlag({ text: 'Unsure about vasopressors' })

    expect(fetchMock).not.toHaveBeenCalled()
    expect(seen).toEqual([
      { name: 'app_request', args: { method: 'GET', path: '/api/flags' } },
      {
        name: 'app_request',
        args: { method: 'POST', path: '/api/flags', body: { text: 'Unsure about vasopressors' } }
      }
    ])
  })

  it("turns the API's refusal into the same ApiError the browser would see", async () => {
    markInChat()
    fakeHost(() => ({
      ok: false,
      status: 409,
      error: { code: 'pile_in_use', message: 'That pile still holds sources.' }
    }))
    await expect(api.updatePile('pil_1', { title: 'x' })).rejects.toMatchObject({
      kind: 'conflict',
      code: 'pile_in_use',
      message: 'That pile still holds sources.'
    })
  })

  it('says where files and removals are done instead', async () => {
    markInChat()
    fakeHost(() => ({ ok: true, status: 200, body: {} }))
    await expect(api.uploadSources('pil_1', [new File(['x'], 'a.txt')], 'mid')).rejects.toMatchObject({
      kind: 'invalid',
      message: IN_CHAT_FILES_MESSAGE
    })
    await expect(api.deleteFlag('flg_1')).rejects.toMatchObject({ kind: 'invalid', message: IN_CHAT_REMOVAL_MESSAGE })
  })

  it('follows a theme change from the host', async () => {
    markInChat()
    fakeHost(() => ({ ok: true, status: 200, body: {} }))
    await detectHost()
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { theme: 'light' } },
        source: window.parent
      })
    )
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
  })
})

describe('the tool result', () => {
  it('reaches a listener, including one that subscribes after it arrived', async () => {
    markInChat()
    fakeHost(() => ({ ok: true, status: 200, body: {} }))
    await detectHost()
    const seen: unknown[] = []
    onToolResult((result) => seen.push(result))
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { structuredContent: { view: 'tutor' } } },
        source: window.parent
      })
    )
    expect(seen).toEqual([{ view: 'tutor' }])
    const late: unknown[] = []
    onToolResult((result) => late.push(result))
    expect(late).toEqual([{ view: 'tutor' }])
  })
})

describe("inside ChatGPT's bridge", () => {
  it('uses window.openai.callTool and its theme', async () => {
    markInChat()
    const callToolMock = vi.fn(async () => ({ structuredContent: { ok: true, status: 200, body: { status: 'ok' } } }))
    vi.stubGlobal('openai', { callTool: callToolMock, theme: 'light' })
    expect(await detectHost()).toBe('chatgpt')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(await api.health()).toEqual({ status: 'ok' })
    expect(callToolMock).toHaveBeenCalledWith('app_request', { method: 'GET', path: '/api/health' })
  })

  it('reports a host that stops answering as unreachable', async () => {
    markInChat()
    vi.stubGlobal('openai', { callTool: vi.fn(async () => { throw new Error('gone') }) })
    await expect(api.health()).rejects.toBeInstanceOf(ApiError)
    await expect(api.health()).rejects.toMatchObject({ kind: 'unreachable' })
  })
})
