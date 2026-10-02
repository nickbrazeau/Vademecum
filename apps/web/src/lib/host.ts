/**
 * The chat host, when the app is drawn inside a conversation (ADR 0014).
 *
 * The same React app runs in two places: in a browser, where `fetch` reaches
 * the API on 127.0.0.1, and inside Codex, ChatGPT or Claude as an MCP App,
 * where the sandbox has no network at all and everything goes through the
 * host as a tool call. This module is the second place. It speaks two
 * dialects, because both hosts exist:
 *
 * - MCP Apps: JSON-RPC over `postMessage` to the parent frame. The app sends
 *   `ui/initialize`, then `tools/call`; the host sends notifications about
 *   its theme and the tool's own result.
 * - ChatGPT's `window.openai` bridge: `callTool`, `toolOutput`, `theme`.
 *
 * Whichever it is, the only tool the app ever calls is `app_request`, and
 * the server decides which API routes that may reach.
 */

export type HostKind = 'none' | 'chatgpt' | 'mcp-app'

export interface ToolResult {
  structuredContent?: unknown
  content?: { type: string; text?: string }[]
  isError?: boolean
}

interface OpenAiBridge {
  callTool?: (name: string, args: Record<string, unknown>) => Promise<unknown>
  toolOutput?: unknown
  theme?: string
  notifyIntrinsicHeight?: (height: number) => void
}

declare global {
  interface Window {
    openai?: OpenAiBridge
  }
}

const PROTOCOL_VERSION = '2025-11-21'
const INITIALIZE_TIMEOUT_MS = 4000

let marked = false
let kind: HostKind = 'none'
let ready: Promise<HostKind> | null = null
let nextId = 1
const pending = new Map<number, { resolve: (value: unknown) => void; reject: (error: Error) => void }>()
const themeListeners = new Set<(theme: 'light' | 'dark') => void>()
const resultListeners = new Set<(result: unknown) => void>()
let lastResult: unknown = undefined

/** Called once by the in-chat entry point. Nothing else flips this. */
export function markInChat(): void {
  marked = true
}

/** Whether this bundle is running inside a conversation rather than a browser tab. */
export function inChat(): boolean {
  return marked
}

/** For tests. */
export function resetHost(): void {
  marked = false
  kind = 'none'
  ready = null
  pending.clear()
  themeListeners.clear()
  resultListeners.clear()
  lastResult = undefined
}

function deliverResult(result: unknown): void {
  if (result === undefined || result === null) return
  lastResult = result
  for (const listener of resultListeners) listener(result)
}

/**
 * The structured content of the tool whose result this frame draws. The
 * dashboard uses it for one thing: which page to open on (`view`).
 */
export function onToolResult(listener: (result: unknown) => void): () => void {
  resultListeners.add(listener)
  if (lastResult !== undefined) listener(lastResult)
  return () => resultListeners.delete(listener)
}

function openai(): OpenAiBridge | null {
  try {
    return window.openai ?? null
  } catch {
    return null
  }
}

function applyTheme(theme: unknown): void {
  if (theme !== 'light' && theme !== 'dark') return
  document.documentElement.setAttribute('data-theme', theme)
  for (const listener of themeListeners) listener(theme)
}

export function onTheme(listener: (theme: 'light' | 'dark') => void): () => void {
  themeListeners.add(listener)
  return () => themeListeners.delete(listener)
}

function post(message: Record<string, unknown>): void {
  window.parent.postMessage({ jsonrpc: '2.0', ...message }, '*')
}

function request(method: string, params: Record<string, unknown>): Promise<unknown> {
  const id = nextId++
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    post({ id, method, params })
  })
}

function onMessage(event: MessageEvent): void {
  if (event.source !== window.parent) return
  const message = event.data as { id?: unknown; result?: unknown; error?: unknown; method?: string; params?: unknown }
  if (message === null || typeof message !== 'object') return
  if (typeof message.id === 'number' && pending.has(message.id)) {
    const waiter = pending.get(message.id)
    pending.delete(message.id)
    if (waiter === undefined) return
    if (message.error !== undefined) {
      const error = message.error as { message?: string }
      waiter.reject(new Error(error.message ?? 'The host refused the request.'))
    } else {
      waiter.resolve(message.result)
    }
    return
  }
  if (message.method === 'ui/notifications/host-context-changed') {
    const params = message.params as { theme?: unknown } | undefined
    applyTheme(params?.theme)
  }
  if (message.method === 'ui/notifications/tool-result') {
    const params = message.params as { structuredContent?: unknown } | undefined
    deliverResult(params?.structuredContent)
  }
}

/**
 * Find out which host this is. Resolves once; later calls return the same
 * answer. In a browser tab it is `none` and the app uses `fetch`.
 */
export function detectHost(): Promise<HostKind> {
  if (ready !== null) return ready
  ready = (async () => {
    if (!marked) return (kind = 'none')
    const bridge = openai()
    if (bridge !== null && typeof bridge.callTool === 'function') {
      applyTheme(bridge.theme)
      deliverResult(bridge.toolOutput)
      window.addEventListener('openai:set_globals', () => {
        applyTheme(openai()?.theme)
        deliverResult(openai()?.toolOutput)
      })
      return (kind = 'chatgpt')
    }
    if (window.parent === window) return (kind = 'none')
    window.addEventListener('message', onMessage)
    const initialised = request('ui/initialize', {
      protocolVersion: PROTOCOL_VERSION,
      appInfo: { name: 'Vademecum', version: '1' },
      appCapabilities: {}
    })
    const timeout = new Promise<null>((resolve) => setTimeout(() => resolve(null), INITIALIZE_TIMEOUT_MS))
    const answer = (await Promise.race([initialised, timeout])) as { hostContext?: { theme?: unknown } } | null
    if (answer === null) {
      // No MCP Apps host answered. A frame that is not a host gets fetch, as a
      // tab would; the request to the parent is harmless.
      return (kind = 'none')
    }
    applyTheme(answer.hostContext?.theme)
    post({ method: 'ui/notifications/initialized' })
    return (kind = 'mcp-app')
  })()
  return ready
}

export function hostKind(): HostKind {
  return kind
}

/** One server tool call, through whichever host is there. */
export async function callTool(name: string, args: Record<string, unknown>): Promise<ToolResult> {
  const host = await detectHost()
  if (host === 'chatgpt') {
    const bridge = openai()
    if (bridge === null || typeof bridge.callTool !== 'function') throw new Error('The host bridge went away.')
    const raw = (await bridge.callTool(name, args)) as ToolResult & { result?: ToolResult }
    return raw.result !== undefined && raw.structuredContent === undefined ? raw.result : raw
  }
  if (host === 'mcp-app') {
    return (await request('tools/call', { name, arguments: args })) as ToolResult
  }
  throw new Error('No chat host is present.')
}

/** Tell the host how tall the page is, so the frame fits the content. */
export function reportHeight(): void {
  const height = document.documentElement.scrollHeight
  const bridge = openai()
  if (bridge !== null && typeof bridge.notifyIntrinsicHeight === 'function') {
    bridge.notifyIntrinsicHeight(height)
    return
  }
  if (kind === 'mcp-app') post({ method: 'ui/notifications/size-changed', params: { height } })
}

export function watchHeight(): () => void {
  if (typeof ResizeObserver === 'undefined') return () => undefined
  const observer = new ResizeObserver(() => reportHeight())
  observer.observe(document.documentElement)
  return () => observer.disconnect()
}
