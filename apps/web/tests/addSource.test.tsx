/**
 * Add source from the header (feedback of 6 October): text or files, into a pile you pick
 * or a new one; filed in the source folder on the Mac, uploaded to the cloud copy on the phone.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AddSourceDialog } from '../src/components/AddSourceDialog'

afterEach(() => vi.unstubAllGlobals())

const PILE = { id: 'pile_1', title: 'Sepsis', tier: 'high', confidence_label: 'High', description: '', created_at: 'now', updated_at: 'now', item_count: 0 }

function stub(routes: Record<string, unknown>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const path = String(url).split('?')[0] ?? ''
      calls.push({ url: path, method, body: init?.body ?? null })
      const payload = routes[`${method} ${path}`] ?? routes[path] ?? {}
      return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

describe('Add source', () => {
  it('on the phone, makes a new pile and uploads pasted text to it', async () => {
    const calls = stub({
      '/api/piles': [],
      'POST /api/piles': { ...PILE, id: 'pile_new', title: 'Night float' },
      'POST /api/piles/pile_new/sources': { accepted: 1, rejected: 0, results: [] }
    })
    const user = userEvent.setup()
    render(<AddSourceDialog open onClose={() => undefined} onPhone />)
    await user.type(await screen.findByLabelText('New pile name'), 'Night float')
    await user.type(screen.getByLabelText('Paste text (optional)'), 'Hyperkalaemia: calcium first.')
    await user.click(screen.getByRole('button', { name: /^Add/ }))
    expect(await screen.findByText(/1 added to Night float/)).toBeInTheDocument()
    expect(calls.some((c) => c.method === 'POST' && c.url === '/api/piles')).toBe(true)
    const upload = calls.find((c) => c.url === '/api/piles/pile_new/sources')!.body as FormData
    expect((upload.get('files') as File).name).toBe('Pasted note.md')
  })

  it('on the Mac, files into the source folder under the chosen pile', async () => {
    const calls = stub({
      '/api/piles': [PILE],
      'POST /api/sources/folder-drop': { folder: 'piles/highconfidence/Sepsis', files: [{ filename: 'lecture.pdf', status: 'placed' }] }
    })
    const user = userEvent.setup()
    render(<AddSourceDialog open onClose={() => undefined} onPhone={false} />)
    await waitFor(() => expect((screen.getByLabelText('Pile') as HTMLSelectElement).value).toBe('pile_1'))
    await user.upload(screen.getByTestId('add-source-input'), new File(['%PDF'], 'lecture.pdf', { type: 'application/pdf' }))
    await user.click(screen.getByRole('button', { name: /^Add/ }))
    expect(await screen.findByText(/1 filed in piles\/highconfidence\/Sepsis/)).toBeInTheDocument()
    const form = calls.find((c) => c.url === '/api/sources/folder-drop')!.body as FormData
    expect(form.get('pile')).toBe('Sepsis')
    expect(form.get('confidence')).toBe('high')
  })

  it('on the phone, stops a file over 95 MB before sending it', async () => {
    stub({ '/api/piles': [PILE] })
    const user = userEvent.setup()
    render(<AddSourceDialog open onClose={() => undefined} onPhone />)
    await waitFor(() => expect((screen.getByLabelText('Pile') as HTMLSelectElement).value).toBe('pile_1'))
    const big = new File(['x'], 'atlas.pdf', { type: 'application/pdf' })
    Object.defineProperty(big, 'size', { value: 120 * 1024 * 1024 })
    await user.upload(screen.getByTestId('add-source-input'), big)
    expect(screen.getByRole('alert')).toHaveTextContent(/atlas.pdf is over 95 MB/)
    expect(screen.getByRole('button', { name: /^Add/ })).toBeDisabled()
  })
})
