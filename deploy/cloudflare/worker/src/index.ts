/**
 * The Worker in front of the seat (ADR 0017).
 *
 * Every request to this Worker's address goes to the one container, on the
 * gateway's port, except the seat's own snapshot store under `/__seat/`,
 * which the Worker answers itself from an R2 bucket binding. The container
 * sleeps when nothing has reached it for a while and wakes on the next
 * request; its records come back from the bucket at boot (seat.py).
 *
 * The container reaches the store through this Worker's public address,
 * presenting `SEAT_KEY`, a secret only the two of them know. No R2 API
 * token exists anywhere: the binding is the only path to the bucket.
 */

import { Container, getContainer } from '@cloudflare/containers'
import type { DurableObject } from 'cloudflare:workers'

interface Env {
  SEAT: DurableObjectNamespace<VademecumSeat>
  BUCKET: R2Bucket
  LIMITER: RateLimit
  PUBLIC_URL: string
  SEAT_KEY: string
  VADEMECUM_SYNC_ACCEPT_TOKEN: string
  VADEMECUM_MCP_PASSPHRASE: string
}

const STORE_PREFIX = '/__seat/'
const KEY_HEADER = 'x-seat-key'
const MAX_LIST = 1000

export class VademecumSeat extends Container<Env> {
  defaultPort = 8766
  // Idle for 45 minutes and the container stops; it is billed only while it runs.
  // Five minutes was shorter than the Mac's sync interval, so it slept between most
  // rounds and came back on its last save each time (feedback of 10 October); while
  // the Mac is awake it now stays awake, and sleeps through the night.
  sleepAfter = '45m'
  enableInternet = true

  constructor(ctx: DurableObject['ctx'], env: Env) {
    super(ctx, env)
    this.envVars = {
      VADEMECUM_MCP_PUBLIC_URL: env.PUBLIC_URL,
      VADEMECUM_SYNC_ACCEPT_TOKEN: env.VADEMECUM_SYNC_ACCEPT_TOKEN,
      VADEMECUM_MCP_PASSPHRASE: env.VADEMECUM_MCP_PASSPHRASE,
      SEAT_STORE_URL: `${env.PUBLIC_URL.replace(/\/$/, '')}${STORE_PREFIX}`,
      SEAT_KEY: env.SEAT_KEY
    }
  }
}

function timingSafeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false
  let out = 0
  for (let i = 0; i < a.length; i++) out |= a.charCodeAt(i) ^ b.charCodeAt(i)
  return out === 0
}

/** Keys are `db/...`, `mcp/...` or `attachments/<kind>/<name>`: nothing else, nothing upward. */
function validKey(key: string): boolean {
  return /^(db|mcp|attachments|relay)\/[A-Za-z0-9._\/-]{1,200}$/.test(key) && !key.includes('..')
}

const RELAY_PATH = '/__relay/wanted'

/**
 * The Socratic relay's flag (feedback of 6 October): when the owner last opened the tutor
 * on the phone, written by the container, read here by the Mac with its sync token. Answered
 * from the bucket by the Worker alone, so the Mac's check every few seconds never wakes the
 * container.
 */
async function relayWanted(request: Request, env: Env): Promise<Response> {
  const presented = request.headers.get('x-vademecum-sync') ?? ''
  if (request.method !== 'GET' || !env.VADEMECUM_SYNC_ACCEPT_TOKEN || !timingSafeEqual(presented, env.VADEMECUM_SYNC_ACCEPT_TOKEN)) {
    return new Response('not found', { status: 404 })
  }
  const object = await env.BUCKET.get('relay/wanted')
  if (object === null) return Response.json({ at: null }, { status: 404, headers: { 'cache-control': 'no-store' } })
  const at = Number(await object.text())
  return Response.json({ at: Number.isFinite(at) ? at : null }, { headers: { 'cache-control': 'no-store' } })
}

async function store(request: Request, env: Env, url: URL): Promise<Response> {
  const presented = request.headers.get(KEY_HEADER) ?? ''
  if (!env.SEAT_KEY || !timingSafeEqual(presented, env.SEAT_KEY)) {
    return new Response('not found', { status: 404 })
  }
  const rest = url.pathname.slice(STORE_PREFIX.length)
  if (rest === 'list') {
    const prefix = url.searchParams.get('prefix') ?? ''
    if (prefix && !validKey(prefix + 'x')) return new Response('bad prefix', { status: 400 })
    // When each was written, so the seat can tell a stale copy from a fresh one.
    const objects: { key: string; size: number; uploaded: string }[] = []
    let cursor: string | undefined
    do {
      const page = await env.BUCKET.list({ prefix, cursor, limit: MAX_LIST })
      for (const object of page.objects) objects.push({ key: object.key, size: object.size, uploaded: object.uploaded.toISOString() })
      cursor = page.truncated ? page.cursor : undefined
    } while (cursor)
    return Response.json({ objects })
  }
  if (!rest.startsWith('object/')) return new Response('not found', { status: 404 })
  const key = rest.slice('object/'.length)
  if (!validKey(key)) return new Response('bad key', { status: 400 })
  if (request.method === 'PUT') {
    await env.BUCKET.put(key, request.body, { httpMetadata: { contentType: 'application/octet-stream' } })
    return Response.json({ stored: key })
  }
  if (request.method === 'DELETE') {
    // Only retired podcast audio (ADR 0027) and page figures no page places any more are
    // ever deleted; the records are not.
    if (!key.startsWith('attachments/podcasts/') && !key.startsWith('attachments/figures/')) {
      return new Response('not deletable', { status: 403 })
    }
    await env.BUCKET.delete(key)
    return Response.json({ deleted: key })
  }
  if (request.method === 'GET') {
    const object = await env.BUCKET.get(key)
    if (object === null) return new Response('not found', { status: 404 })
    return new Response(object.body, { headers: { 'content-type': 'application/octet-stream', 'cache-control': 'no-store' } })
  }
  return new Response('method not allowed', { status: 405 })
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    // Per-address rate limit: a scanning bot must not keep the seat awake.
    const address = request.headers.get('cf-connecting-ip') ?? 'unknown'
    const { success } = await env.LIMITER.limit({ key: address })
    if (!success) return new Response('Too many requests', { status: 429, headers: { 'retry-after': '60' } })
    if (url.pathname.startsWith(STORE_PREFIX)) return store(request, env, url)
    if (url.pathname === RELAY_PATH) return relayWanted(request, env)
    const seat = getContainer(env.SEAT, 'vademecum')
    return seat.fetch(request)
  }
}
