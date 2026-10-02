/// <reference lib="webworker" />
/**
 * Vademecum's service worker.
 *
 * It exists for one reason: so the shell opens when the backend is not
 * running, and says so honestly rather than showing a browser error page. It
 * caches the static shell and the hashed build output, and nothing else. It
 * never caches `/api`, and never caches anything the allowlist in
 * `cachePolicy.ts` does not name.
 *
 * Serving and storing are separate questions here. A navigation to a client
 * route is served -- that is how the shell opens offline -- while only the
 * shell itself is stored.
 */

import {
  SHELL_ASSETS,
  SHELL_CACHE,
  isPrivatePath,
  mayStore,
  shouldCacheRequest
} from './cachePolicy'

const worker = self as unknown as ServiceWorkerGlobalScope

function facts(request: Request) {
  return { url: request.url, method: request.method, origin: worker.location.origin }
}

function responseFacts(response: Response) {
  return {
    status: response.status,
    type: response.type,
    cacheControl: response.headers.get('Cache-Control')
  }
}

worker.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(SHELL_CACHE)
      await cache.addAll([...SHELL_ASSETS])
      await worker.skipWaiting()
    })()
  )
})

worker.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys()
      await Promise.all(
        names.filter((name) => name !== SHELL_CACHE).map((name) => caches.delete(name))
      )
      await worker.clients.claim()
    })()
  )
})

async function storeIfPermitted(request: Request, response: Response): Promise<void> {
  if (!mayStore(facts(request), responseFacts(response))) return
  const cache = await caches.open(SHELL_CACHE)
  await cache.put(request, response.clone())
}

worker.addEventListener('fetch', (event) => {
  const { request } = event
  const url = new URL(request.url)

  // Private content, non-GETs and anything cross-origin: pass straight
  // through, with no cache read and no cache write. Returning early is the
  // point.
  if (request.method.toUpperCase() !== 'GET') return
  if (url.origin !== worker.location.origin || isPrivatePath(url.pathname)) return

  if (request.mode === 'navigate') {
    // Network first, so a running backend always wins; the cached shell is the
    // offline fallback, not the default.
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request)
          await storeIfPermitted(request, response)
          return response
        } catch {
          const cached = await caches.match('/index.html')
          if (cached) return cached
          throw new Error('offline and no cached shell')
        }
      })()
    )
    return
  }

  // Everything else is served only if the allowlist names it. An arbitrary
  // path -- `/attachments/x`, `/exports/x`, an unhashed file -- is left to the
  // browser, untouched and unstored.
  if (!shouldCacheRequest(facts(request))) return

  event.respondWith(
    (async () => {
      const cached = await caches.match(request)
      if (cached) return cached
      const response = await fetch(request)
      await storeIfPermitted(request, response)
      return response
    })()
  )
})
