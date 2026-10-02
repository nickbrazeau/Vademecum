/**
 * What the service worker is allowed to keep (ADR 0002, rule 3).
 *
 * These are pure functions over plain objects rather than over `Request` and
 * `Response`, so the rule can be tested directly instead of being inferred
 * from the behaviour of a worker. A private response must never reach
 * `Cache.put`, and the only way to be sure of that is to be able to ask.
 */

export const SHELL_CACHE = 'vademecum-shell-v1'

/** Paths whose responses are the owner's own content. Never cached, ever. */
export const PRIVATE_PATH_PREFIXES = ['/api'] as const

/** Precached at install: the least that lets the shell open with no network. */
export const SHELL_ASSETS = ['/', '/index.html', '/manifest.webmanifest', '/icon.svg'] as const

/** The icons the manifest and the browser ask for, by name. */
export const ICON_ASSETS = [
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png'
] as const

/**
 * The whole allowlist of named static files.
 *
 * An allowlist rather than a denylist on purpose. "Same-origin and not /api"
 * would quietly cover `/attachments/…`, `/exports/…` and every client route,
 * all of which the backend answers with the owner's own content or with the
 * shell. Anything not named here or emitted by the build is fetched and
 * forgotten.
 */
export const STATIC_SHELL_PATHS: readonly string[] = [
  ...new Set<string>([...SHELL_ASSETS, ...ICON_ASSETS])
]

/** Vite writes hashed build output under this prefix and nowhere else. */
export const BUILD_ASSET_PREFIX = '/assets/'

// `main-CTbR10CB.css`: a name, a dash, the content hash, one extension. The
// hash is what makes the file immutable, and immutability is the only reason
// it is safe to keep.
const HASHED_BUILD_FILE = /^[A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$/

export interface RequestFacts {
  url: string
  method: string
  /** The page origin the worker is running under. */
  origin: string
}

export interface ResponseFacts {
  status: number
  /** `Response.type`: 'basic' for same-origin, 'opaque' for a no-cors reply. */
  type: string
  /** The response's Cache-Control header, if it had one. */
  cacheControl?: string | null
}

export function isPrivatePath(pathname: string): boolean {
  return PRIVATE_PATH_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  )
}

function parse(url: string, origin: string): URL | null {
  try {
    return new URL(url, origin)
  } catch {
    return null
  }
}

export function isSameOrigin(url: string, origin: string): boolean {
  const parsed = parse(url, origin)
  return parsed !== null && parsed.origin === origin
}

/** True only for a path that is part of the static shell or of the build. */
export function isStaticShellPath(pathname: string): boolean {
  if (STATIC_SHELL_PATHS.includes(pathname)) return true
  if (!pathname.startsWith(BUILD_ASSET_PREFIX)) return false
  const file = pathname.slice(BUILD_ASSET_PREFIX.length)
  // No sub-directories: `/assets/` is flat, and a path that walks out of it is
  // not something to reason about twice.
  if (file.includes('/')) return false
  return HASHED_BUILD_FILE.test(file)
}

/**
 * True only for a request whose response may be stored.
 *
 * Refuses: anything that is not a GET, anything cross-origin, anything under a
 * private path prefix, anything carrying a query string, and — the point of
 * the allowlist — any path that is not part of the static shell.
 */
export function shouldCacheRequest(request: RequestFacts): boolean {
  if (request.method.toUpperCase() !== 'GET') return false
  const parsed = parse(request.url, request.origin)
  if (parsed === null || parsed.origin !== request.origin) return false
  // A query string means a caller wanted something specific; a static file
  // never does, and caching one keyed by query is how a cache grows content.
  if (parsed.search !== '') return false
  if (isPrivatePath(parsed.pathname)) return false
  return isStaticShellPath(parsed.pathname)
}

/**
 * True only for a response that may be stored.
 *
 * Refuses: any non-200, any opaque or cross-origin response (whose contents
 * cannot be inspected), and anything the server marked `no-store` — which is
 * every `/api` response, said a second time by the server itself.
 */
export function isCacheableResponse(response: ResponseFacts): boolean {
  if (response.status !== 200) return false
  if (response.type !== 'basic' && response.type !== 'default') return false
  const directive = (response.cacheControl ?? '').toLowerCase()
  if (directive.includes('no-store') || directive.includes('private')) return false
  return true
}

/** The single question the worker asks before `Cache.put`. */
export function mayStore(request: RequestFacts, response: ResponseFacts): boolean {
  return shouldCacheRequest(request) && isCacheableResponse(response)
}
