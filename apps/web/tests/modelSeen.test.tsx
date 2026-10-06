/**
 * Feedback of 5 October: on the phone's copy, Settings shows the Mac's model
 * connection and allowance as the Mac last saw them, rather than nothing.
 */

import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Settings } from '../src/pages/Settings'

afterEach(() => vi.unstubAllGlobals())

const PREFERENCES = { visible_tabs: ['today', 'settings'], order: ['today', 'settings'], tabs: [], daily_goal: 20, podcast_speed: 1 }

describe("the Mac's model, on the phone", () => {
  it('shows the connection, plan and allowance as last seen', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const path = String(url).split('?')[0]
        const reply =
          path === '/api/model/last-seen'
            ? {
                seen: {
                  provider: 'codex',
                  state: 'signed_in',
                  signed_in: true,
                  plan: 'Pro Lite',
                  primary: { used_percent: 25, resets_at: '2026-10-11T18:21:00Z', window_minutes: 10080 },
                  secondary: null,
                  limited: false,
                  seen_at: '2026-10-06T15:00:00Z'
                }
              }
            : path === '/api/preferences'
              ? PREFERENCES
              : {}
        return new Response(JSON.stringify(reply), { status: 200, headers: { 'Content-Type': 'application/json' } })
      })
    )
    render(<Settings showModel={false} />)
    expect(await screen.findByText(/ChatGPT, through Codex · Pro Lite plan/)).toBeInTheDocument()
    expect(screen.getByText('Signed in')).toBeInTheDocument()
    expect(screen.getByText(/This week: 25% used/)).toBeInTheDocument()
    expect(screen.getByText(/As the Mac saw it/)).toBeInTheDocument()
  })
})
