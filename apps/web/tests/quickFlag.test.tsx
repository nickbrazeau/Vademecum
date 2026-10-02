/**
 * Knowledge Gap Flag capture: one action, one required field, and a draft that
 * survives a failure.
 */

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { QuickFlagDialog } from '../src/components/QuickFlagDialog'
import { FLAG_DRAFT_KEY, loadDraft } from '../src/lib/drafts'

const noop = () => undefined

function stubFetch(response: Response | Error) {
  const fetchMock = vi.fn(async () => {
    if (response instanceof Error) throw response
    return response.clone()
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const savedFlag = new Response(
  JSON.stringify({
    id: 'kgf_1',
    text: 'Had to look up the 4T score',
    topic: null,
    pile_id: null,
    status: 'open',
    created_at: '2026-08-30T00:00:00.000Z',
    updated_at: '2026-08-30T00:00:00.000Z',
    addressed_at: null
  }),
  { status: 201, headers: { 'Content-Type': 'application/json' } }
)

afterEach(() => vi.unstubAllGlobals())

describe('capture', () => {
  it('asks for one thing and marks the topic optional', () => {
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)
    expect(screen.getByRole('heading', { name: /flag a knowledge gap/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/what were you unsure about/i)).toBeInTheDocument()
    const topic = screen.getByLabelText(/topic/i)
    expect(topic).toBeInTheDocument()
    expect(topic).not.toBeRequired()
  })

  it('warns about identifiers where the typing happens', () => {
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)
    expect(screen.getByRole('note')).toHaveTextContent(/no patient identifiers/i)
    expect(screen.getByRole('note')).toHaveTextContent(/hipaa material/i)
  })

  it('says where the text goes, next to the text', () => {
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)
    expect(screen.getByText(/saved on this mac\. not sent anywhere\./i)).toBeInTheDocument()
  })

  it('cannot be saved empty', async () => {
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)
    expect(screen.getByRole('button', { name: /save flag/i })).toBeDisabled()
  })

  it('autosaves the draft locally without calling anything', async () => {
    const fetchMock = stubFetch(savedFlag)
    const user = userEvent.setup()
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)

    await user.type(screen.getByLabelText(/what were you unsure about/i), 'Unsure about 4T')

    expect(loadDraft(FLAG_DRAFT_KEY)).toBe('Unsure about 4T')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('restores a draft left from last time', () => {
    window.localStorage.setItem(FLAG_DRAFT_KEY, 'Half a thought')
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)
    expect(screen.getByLabelText(/what were you unsure about/i)).toHaveValue('Half a thought')
  })

  it('saves to the local API and clears the draft', async () => {
    const fetchMock = stubFetch(savedFlag)
    const onSaved = vi.fn()
    const user = userEvent.setup()
    render(<QuickFlagDialog open onClose={noop} onSaved={onSaved} />)

    await user.type(screen.getByLabelText(/what were you unsure about/i), 'Had to look up 4T')
    await user.click(screen.getByRole('button', { name: /save flag/i }))

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/flags')
    expect(init.method).toBe('POST')
    expect(JSON.parse(String(init.body))).toEqual({ text: 'Had to look up 4T' })
    expect(onSaved).toHaveBeenCalled()
    expect(loadDraft(FLAG_DRAFT_KEY)).toBe('')
  })

  it('keeps the words when the save fails', async () => {
    stubFetch(new TypeError('Failed to fetch'))
    const user = userEvent.setup()
    render(<QuickFlagDialog open onClose={noop} onSaved={noop} />)

    await user.type(screen.getByLabelText(/what were you unsure about/i), 'Worth keeping')
    await user.click(screen.getByRole('button', { name: /save flag/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/not answering on this mac/i)
    expect(screen.getByRole('alert')).toHaveTextContent(/your text is still here/i)
    expect(screen.getByLabelText(/what were you unsure about/i)).toHaveValue('Worth keeping')
    expect(loadDraft(FLAG_DRAFT_KEY)).toBe('Worth keeping')
  })

  it('closes without losing the draft', async () => {
    const onClose = vi.fn()
    const user = userEvent.setup()
    render(<QuickFlagDialog open onClose={onClose} onSaved={noop} />)

    await user.type(screen.getByLabelText(/what were you unsure about/i), 'Later')
    await user.click(screen.getByRole('button', { name: /close/i }))

    expect(onClose).toHaveBeenCalled()
    expect(loadDraft(FLAG_DRAFT_KEY)).toBe('Later')
  })
})
