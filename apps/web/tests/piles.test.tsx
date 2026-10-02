/**
 * Piles: the owner types the item's content, and a failed save costs nothing.
 *
 * A learning item whose body cannot be entered is a title and a citation with
 * nothing between them — the pile has nothing to build questions from. These
 * tests are about that field, its draft, and the failures around it.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { itemDraftKey, loadDraft, saveDraft } from '../src/lib/drafts'
import { MAX_ITEM_BODY_LENGTH } from '../src/lib/types'
import { Sources as Piles } from '../src/pages/Sources'

const PILE = {
  id: 'pil_1',
  title: 'Endocarditis',
  tier: 'high' as const,
  description: '',
  created_at: '2026-08-30T00:00:00Z',
  updated_at: '2026-08-30T00:00:00Z',
  item_count: 1
}

const ITEM = {
  id: 'itm_1',
  pile_id: 'pil_1',
  title: 'Duke criteria',
  body: 'Two major, or one major and three minor.',
  source: 'Lecture',
  content_hash: 'a'.repeat(64),
  created_at: '2026-08-30T00:00:00Z',
  updated_at: '2026-08-30T00:00:00Z'
}

interface Overrides {
  createItem?: () => Response | Promise<Response>
  deleteItem?: () => Response | Promise<Response>
}

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' }
  })

const failure = (status: number, message: string) =>
  json({ error: { code: 'server', message } }, status)

/** The local backend, stubbed. Nothing here reaches a network. */
function stubApi(overrides: Overrides = {}) {
  const calls: { method: string; path: string; body: unknown }[] = []
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const path = String(url).split('?')[0] ?? ''
    const method = (init.method ?? 'GET').toUpperCase()
    calls.push({
      method,
      path,
      body: init.body === undefined ? undefined : JSON.parse(String(init.body))
    })

    if (method === 'GET' && path === '/api/piles') return json([PILE])
    if (method === 'GET' && path === '/api/piles/pil_1/items') return json([ITEM])
    if (method === 'GET' && path === '/api/piles/pil_1/sources') return json([])
    if (method === 'GET' && path === '/api/piles/pil_1/build/status') return json({ run: null, running: false, coverage: {} })
    if (method === 'GET' && path === '/api/points') return json([])
    if (method === 'POST' && path === '/api/piles/pil_1/items') {
      return overrides.createItem?.() ?? json(ITEM, 201)
    }
    if (method === 'DELETE' && path === '/api/items/itm_1') {
      return overrides.deleteItem?.() ?? new Response(null, { status: 204 })
    }
    return json({}, 404)
  })
  vi.stubGlobal('fetch', fetchMock)
  return calls
}

/** Open the one pile and wait for its item panel. */
async function openPile(user: ReturnType<typeof userEvent.setup>) {
  render(<Piles />)
  await user.click(await screen.findByRole('button', { name: /endocarditis/i }))
  return screen.findByLabelText(/what it says/i)
}

beforeEach(() => {
  window.localStorage.clear()
})

afterEach(() => vi.unstubAllGlobals())

describe('entering a learning item', () => {
  it('offers a body field, not only a title and a source', async () => {
    stubApi()
    const user = userEvent.setup()
    const body = await openPile(user)

    expect(body.tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText(/add a learning item/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/where it came from/i)).toBeInTheDocument()
  })

  it('holds the body to the length the API accepts', async () => {
    stubApi()
    const user = userEvent.setup()
    const body = await openPile(user)

    expect(body).toHaveAttribute('maxlength', String(MAX_ITEM_BODY_LENGTH))
    expect(MAX_ITEM_BODY_LENGTH).toBe(20000)
  })

  it('sends what was typed in the body', async () => {
    const calls = stubApi()
    const user = userEvent.setup()
    const body = await openPile(user)

    await user.type(screen.getByLabelText(/add a learning item/i), 'Duke criteria')
    await user.type(body, 'Two major criteria.')
    await user.click(screen.getByRole('button', { name: /add item/i }))

    await waitFor(() => {
      const created = calls.find((call) => call.method === 'POST')
      expect(created?.body).toMatchObject({
        title: 'Duke criteria',
        body: 'Two major criteria.'
      })
    })
  })

  it('warns about identifiers where the typing happens', async () => {
    stubApi()
    const user = userEvent.setup()
    await openPile(user)
    expect(screen.getAllByRole('note').some((note) => /no patient identifiers/i.test(note.textContent || ''))).toBe(true)
  })
})

describe('the body draft', () => {
  it('is kept locally, and the words survive a failed save', async () => {
    stubApi({ createItem: () => failure(500, 'The local service returned 500.') })
    const user = userEvent.setup()
    const body = await openPile(user)

    await user.type(screen.getByLabelText(/add a learning item/i), 'Duke criteria')
    await user.type(body, 'Two major criteria.')
    await user.click(screen.getByRole('button', { name: /add item/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/your text is still here/i)
    expect(body).toHaveValue('Two major criteria.')
    expect(loadDraft(itemDraftKey('pil_1'))).toBe('Two major criteria.')
  })

  it('survives the backend not being there at all', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init: RequestInit = {}) => {
        const method = (init.method ?? 'GET').toUpperCase()
        if (method === 'GET' && String(url).startsWith('/api/piles/pil_1/items')) return json([])
        if (method === 'GET') return json([PILE])
        throw new TypeError('Failed to fetch')
      })
    )
    const user = userEvent.setup()
    const body = await openPile(user)

    await user.type(screen.getByLabelText(/add a learning item/i), 'Duke criteria')
    await user.type(body, 'Two major criteria.')
    await user.click(screen.getByRole('button', { name: /add item/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not answering on this mac/i)
    expect(loadDraft(itemDraftKey('pil_1'))).toBe('Two major criteria.')
  })

  it('is cleared once the item is saved', async () => {
    stubApi()
    const user = userEvent.setup()
    const body = await openPile(user)

    await user.type(screen.getByLabelText(/add a learning item/i), 'Duke criteria')
    await user.type(body, 'Two major criteria.')
    await user.click(screen.getByRole('button', { name: /add item/i }))

    await waitFor(() => expect(body).toHaveValue(''))
    expect(loadDraft(itemDraftKey('pil_1'))).toBe('')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('comes back when the panel is opened again', async () => {
    saveDraft(itemDraftKey('pil_1'), 'Half a thought about the Duke criteria')
    stubApi()
    const user = userEvent.setup()
    const body = await openPile(user)

    expect(body).toHaveValue('Half a thought about the Duke criteria')
  })

  it('belongs to one pile and is not shown under another', () => {
    saveDraft(itemDraftKey('pil_1'), 'Endocarditis notes')
    expect(loadDraft(itemDraftKey('pil_2'))).toBe('')
    expect(itemDraftKey('pil_1')).not.toBe(itemDraftKey('pil_2'))
  })
})

describe('failures that used to be silent', () => {
  it('says a delete did not happen instead of dropping the rejection', async () => {
    stubApi({ deleteItem: () => failure(500, 'The local service returned 500.') })
    const user = userEvent.setup()
    await openPile(user)

    const item = screen.getByText('Duke criteria').closest('li') as HTMLElement
    await user.click(within(item).getByRole('button', { name: /delete/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/refresh to confirm/i)
    // The item is still listed, because it is still there.
    expect(screen.getByText('Duke criteria')).toBeInTheDocument()
  })

  it('says a pile could not be created rather than clearing the field', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit = {}) => {
        const method = (init.method ?? 'GET').toUpperCase()
        if (method === 'GET') return json([])
        return failure(500, 'The local service returned 500.')
      })
    )
    const user = userEvent.setup()
    render(<Piles />)

    const title = screen.getByLabelText(/^title$/i)
    await user.type(title, 'Antimicrobial stewardship')
    await user.click(screen.getByRole('button', { name: /create pile/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/returned 500/i)
    expect(title).toHaveValue('Antimicrobial stewardship')
  })
})
