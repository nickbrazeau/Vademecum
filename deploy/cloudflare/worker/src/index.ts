/**
 * The Worker in front of the seat (ADR 0017).
 *
 * Every request to this Worker's address goes to the one container, on the
 * gateway's port. The container sleeps when nothing has reached it for a
 * while and wakes on the next request; its records come back from object
 * storage at boot (seat.py). Secrets reach the container as environment
 * variables set here, from `wrangler secret put`.
 */

import { Container, getContainer } from '@cloudflare/containers'
import type { DurableObject } from 'cloudflare:workers'

interface Env {
  SEAT: DurableObjectNamespace<VademecumSeat>
  PUBLIC_URL: string
  R2_ENDPOINT: string
  R2_BUCKET: string
  R2_ACCESS_KEY_ID: string
  R2_SECRET_ACCESS_KEY: string
  VADEMECUM_SYNC_ACCEPT_TOKEN: string
  VADEMECUM_MCP_PASSPHRASE: string
}

export class VademecumSeat extends Container<Env> {
  defaultPort = 8766
  sleepAfter = '20m'
  enableInternet = true

  constructor(ctx: DurableObject['ctx'], env: Env) {
    super(ctx, env)
    this.envVars = {
      VADEMECUM_MCP_PUBLIC_URL: env.PUBLIC_URL,
      VADEMECUM_SYNC_ACCEPT_TOKEN: env.VADEMECUM_SYNC_ACCEPT_TOKEN,
      VADEMECUM_MCP_PASSPHRASE: env.VADEMECUM_MCP_PASSPHRASE,
      SEAT_REMOTE: `r2:${env.R2_BUCKET}`,
      // rclone's own configuration, from the environment: an S3 remote
      // named "r2" pointing at the account's R2 endpoint.
      RCLONE_CONFIG_R2_TYPE: 's3',
      RCLONE_CONFIG_R2_PROVIDER: 'Cloudflare',
      RCLONE_CONFIG_R2_ENDPOINT: env.R2_ENDPOINT,
      RCLONE_CONFIG_R2_ACCESS_KEY_ID: env.R2_ACCESS_KEY_ID,
      RCLONE_CONFIG_R2_SECRET_ACCESS_KEY: env.R2_SECRET_ACCESS_KEY,
      RCLONE_CONFIG_R2_ACL: 'private'
    }
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const seat = getContainer(env.SEAT, 'vademecum')
    return seat.fetch(request)
  }
}
