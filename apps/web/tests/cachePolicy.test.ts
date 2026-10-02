/**
 * ADR 0002, rule 3: the service worker may cache the app shell and nothing
 * else. These are the rules themselves, asked directly.
 */

import { describe, expect, it } from 'vitest'
import {
  ICON_ASSETS,
  PRIVATE_PATH_PREFIXES,
  SHELL_ASSETS,
  STATIC_SHELL_PATHS,
  isCacheableResponse,
  isPrivatePath,
  isSameOrigin,
  isStaticShellPath,
  mayStore,
  shouldCacheRequest
} from '../src/sw/cachePolicy'

const ORIGIN = 'http://127.0.0.1:8765'
const get = (url: string) => ({ url, method: 'GET', origin: ORIGIN })
const ok = { status: 200, type: 'basic', cacheControl: null }

describe('private paths', () => {
  it('treats /api and everything under it as private', () => {
    expect(isPrivatePath('/api')).toBe(true)
    expect(isPrivatePath('/api/today')).toBe(true)
    expect(isPrivatePath('/api/flags/kgf_abc')).toBe(true)
  })

  it('does not over-reach into look-alike paths', () => {
    expect(isPrivatePath('/apiary')).toBe(false)
    expect(isPrivatePath('/')).toBe(false)
    expect(isPrivatePath('/assets/index-abc.js')).toBe(false)
  })

  it('lists /api as private', () => {
    expect(PRIVATE_PATH_PREFIXES).toContain('/api')
  })
})

describe('shouldCacheRequest', () => {
  it('refuses every API request, whatever its shape', () => {
    for (const url of [
      `${ORIGIN}/api/today`,
      `${ORIGIN}/api/flags`,
      `${ORIGIN}/api/piles?tier=high`,
      `${ORIGIN}/api/health`,
      `${ORIGIN}/api`
    ]) {
      expect(shouldCacheRequest(get(url)), url).toBe(false)
    }
  })

  it('refuses anything that is not a GET', () => {
    for (const method of ['POST', 'PATCH', 'DELETE', 'PUT', 'HEAD']) {
      expect(shouldCacheRequest({ url: `${ORIGIN}/index.html`, method, origin: ORIGIN })).toBe(false)
    }
  })

  it('refuses cross-origin requests', () => {
    expect(shouldCacheRequest(get('https://example.com/analytics.js'))).toBe(false)
    expect(shouldCacheRequest(get('https://api.openai.com/v1/x'))).toBe(false)
  })

  it('accepts the static shell and the hashed build output', () => {
    for (const asset of STATIC_SHELL_PATHS) {
      expect(shouldCacheRequest(get(`${ORIGIN}${asset}`)), asset).toBe(true)
    }
    expect(shouldCacheRequest(get(`${ORIGIN}/assets/main-Brue7D13.js`))).toBe(true)
  })

  it('refuses same-origin paths that are not part of the shell', () => {
    for (const path of [
      '/attachments/x',
      '/attachments/scan-2026.pdf',
      '/exports/x',
      '/exports/vademecum-export-20260830.json',
      '/backups/vademecum-backup.sqlite3',
      '/notes/x',
      '/private',
      '/piles',
      '/map',
      '/logs/vademecum.log'
    ]) {
      expect(shouldCacheRequest(get(`${ORIGIN}${path}`)), path).toBe(false)
    }
  })

  it('refuses a shell path carrying a query string', () => {
    expect(shouldCacheRequest(get(`${ORIGIN}/index.html?token=abc`))).toBe(false)
    expect(shouldCacheRequest(get(`${ORIGIN}/assets/main-Brue7D13.js?v=2`))).toBe(false)
  })
})

describe('the static-shell allowlist', () => {
  it('names the shell, the manifest and the icons it knows about', () => {
    for (const path of ['/', '/index.html', '/manifest.webmanifest']) {
      expect(isStaticShellPath(path), path).toBe(true)
    }
    for (const icon of ICON_ASSETS) {
      expect(isStaticShellPath(icon), icon).toBe(true)
    }
    expect(STATIC_SHELL_PATHS).toEqual(expect.arrayContaining([...SHELL_ASSETS, ...ICON_ASSETS]))
  })

  it('accepts hashed build output and nothing else under /assets/', () => {
    for (const file of ['main-Brue7D13.js', 'main-CTbR10CB.css']) {
      expect(isStaticShellPath(`/assets/${file}`), file).toBe(true)
    }
    for (const file of [
      'main.js',
      'index-a1b2.js',
      'notes.json',
      'main-Brue7D13.js.map',
      'nested/main-Brue7D13.js'
    ]) {
      expect(isStaticShellPath(`/assets/${file}`), file).toBe(false)
    }
  })

  it('refuses an unknown icon rather than any png at the root', () => {
    expect(isStaticShellPath('/icon-192.png')).toBe(true)
    expect(isStaticShellPath('/scan.png')).toBe(false)
    expect(isStaticShellPath('/attachments/icon-192.png')).toBe(false)
  })

  it('refuses a path that only looks like the assets prefix', () => {
    expect(isStaticShellPath('/assetsmain-Brue7D13.js')).toBe(false)
    expect(isStaticShellPath('/x/assets/main-Brue7D13.js')).toBe(false)
  })
})

describe('isCacheableResponse', () => {
  it('accepts a plain same-origin 200', () => {
    expect(isCacheableResponse(ok)).toBe(true)
  })

  it('refuses anything the server marked no-store', () => {
    expect(isCacheableResponse({ ...ok, cacheControl: 'no-store' })).toBe(false)
    expect(isCacheableResponse({ ...ok, cacheControl: 'private, max-age=0' })).toBe(false)
  })

  it('refuses opaque and cross-origin responses', () => {
    expect(isCacheableResponse({ ...ok, type: 'opaque' })).toBe(false)
    expect(isCacheableResponse({ ...ok, type: 'cors' })).toBe(false)
    expect(isCacheableResponse({ ...ok, type: 'error' })).toBe(false)
  })

  it('refuses anything that is not a 200', () => {
    for (const status of [201, 204, 301, 304, 404, 500]) {
      expect(isCacheableResponse({ ...ok, status }), String(status)).toBe(false)
    }
  })
})

describe('mayStore', () => {
  it('is false for an API response even when the response itself looks fine', () => {
    expect(mayStore(get(`${ORIGIN}/api/today`), ok)).toBe(false)
  })

  it('is false for the owner\'s own files even when the response looks fine', () => {
    for (const path of ['/attachments/x', '/exports/x', '/notes/x', '/private']) {
      expect(mayStore(get(`${ORIGIN}${path}`), ok), path).toBe(false)
    }
  })

  it('is true only for a static shell asset with a storable response', () => {
    expect(mayStore(get(`${ORIGIN}/index.html`), ok)).toBe(true)
    expect(mayStore(get(`${ORIGIN}/assets/main-Brue7D13.js`), ok)).toBe(true)
  })

  it('is false for every model-connection response (ADR 0006)', () => {
    // A device code, a verification URL and a sign-in state are the last
    // things that may survive in a cache. They are under /api, so the existing
    // rule already covers them; this pins it against a future exception.
    for (const path of [
      '/api/model/status',
      '/api/model/login',
      '/api/model/login/cancel',
      '/api/model/restart'
    ]) {
      expect(mayStore(get(`${ORIGIN}${path}`), ok), path).toBe(false)
    }
  })

  it('does not cache the Model client route, only the shell that serves it', () => {
    expect(mayStore(get(`${ORIGIN}/model`), ok)).toBe(false)
  })
})

describe('isSameOrigin', () => {
  it('resolves relative URLs against the page origin', () => {
    expect(isSameOrigin('/index.html', ORIGIN)).toBe(true)
    expect(isSameOrigin('https://example.com/x', ORIGIN)).toBe(false)
    expect(isSameOrigin('javascript:alert(1)', ORIGIN)).toBe(false)
  })
})
