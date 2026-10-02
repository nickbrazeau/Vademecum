/**
 * The API client: same-origin, never cached, and honest when the backend is
 * not there.
 */

import { afterEach, describe, expect, it, vi } from 'vitest'
import { API_ROOT, ApiError, UNREACHABLE_MESSAGE, api } from '../src/lib/api'

function stubFetch(response: Response | (() => Promise<Response>)) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
    typeof response === 'function' ? await response() : response.clone()
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

describe('every request', () => {
  it('is sent with cache: no-store', async () => {
    const fetchMock = stubFetch(json({ topics: [], tiers: [], unfiled_flag_count: 0 }))
    await api.improvementMap()
    await api.today()
    await api.listPiles()
    await api.listFlags()
    expect(fetchMock).toHaveBeenCalledTimes(4)
    for (const call of fetchMock.mock.calls) {
      const init = call[1] as RequestInit
      expect(init.cache).toBe('no-store')
      expect(init.credentials).toBe('same-origin')
    }
  })

  it('goes to a relative /api path and nowhere else', async () => {
    const fetchMock = stubFetch(json({}))
    await api.health()
    await api.createFlag({ text: 'Something' })
    for (const call of fetchMock.mock.calls) {
      const url = String(call[0])
      expect(url.startsWith(`${API_ROOT}/`)).toBe(true)
      expect(url).not.toMatch(/^https?:/)
    }
  })

  it('sends no authorization or provider header', async () => {
    const fetchMock = stubFetch(json({}))
    await api.createFlag({ text: 'Something' })
    const headers = (fetchMock.mock.calls[0]?.[1] as RequestInit).headers as Record<string, string>
    expect(Object.keys(headers).map((key) => key.toLowerCase())).toEqual([
      'accept',
      'content-type'
    ])
  })
})

describe('failures', () => {
  it('reports an unreachable backend as unreachable, not as empty data', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')))
    await expect(api.today()).rejects.toMatchObject({
      kind: 'unreachable',
      message: UNREACHABLE_MESSAGE
    })
  })

  it('carries validation problems through without the submitted text', async () => {
    stubFetch(
      json(
        {
          error: {
            code: 'invalid_request',
            message: 'That request could not be accepted.',
            fields: [{ field: 'text', problem: 'String should have at least 1 character' }]
          }
        },
        422
      )
    )
    const error = await api.createFlag({ text: '' }).catch((caught: unknown) => caught)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).kind).toBe('invalid')
    expect((error as ApiError).fields[0]?.field).toBe('text')
  })

  it('maps a 404 to not_found', async () => {
    stubFetch(json({ error: { code: 'not_found', message: 'No such pile.' } }, 404))
    await expect(api.listItems('pil_nope')).rejects.toMatchObject({ kind: 'not_found' })
  })

  it('treats 204 as success with no body', async () => {
    stubFetch(new Response(null, { status: 204 }))
    await expect(api.deleteFlag('kgf_1')).resolves.toBeUndefined()
  })
})
