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
  // Idle for five minutes and the container stops; it is billed only while
  // it runs, and waking takes a few seconds (the records come back from the
  // bucket).
  sleepAfter = '5m'
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
  return /^(db|mcp|attachments)\/[A-Za-z0-9._\/-]{1,200}$/.test(key) && !key.includes('..')
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
    const objects: { key: string; size: number }[] = []
    let cursor: string | undefined
    do {
      const page = await env.BUCKET.list({ prefix, cursor, limit: MAX_LIST })
      for (const object of page.objects) objects.push({ key: object.key, size: object.size })
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
    const seat = getContainer(env.SEAT, 'vademecum')
    return seat.fetch(request)
  }
}
