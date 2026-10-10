/**
 * Notes (ADR 0032, feedback of 10 October): notebooks and nested notes, written in
 * Markdown, saved, read back, searched.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Notes } from '../src/pages/Notes'

afterEach(() => vi.unstubAllGlobals())

const BOOK = { id: 'note_b', parent_id: null, notebook: true, title: 'Cardiology', position: 1, use_as_source: false, created_at: 'now', updated_at: 'now', has_body: false }
const NOTE = { id: 'note_n', parent_id: 'note_b', notebook: false, title: 'Heart failure', position: 1, use_as_source: false, created_at: 'now', updated_at: 'now', has_body: true }

describe('Notes', () => {
  it('opens a notebook, edits a note in Markdown, saves it, reads it, and finds it', async () => {
    let body = 'Four pillars.'
    const calls: { method: string; url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, init?: RequestInit) => {
        const method = init?.method ?? 'GET'
        const parsed = init?.body ? JSON.parse(String(init.body)) : null
        calls.push({ method, url: String(url), body: parsed })
        const path = String(url).split('?')[0]
        if (method === 'PATCH') body = parsed.body_md ?? body
        const payload =
          path === '/api/notes'
            ? { notes: [BOOK, NOTE] }
            : path === '/api/notes/search'
              ? { notes: [NOTE] }
              : path === '/api/notes/note_n'
                ? { ...NOTE, body_md: body, path: ['Cardiology', 'Heart failure'] }
                : {}
        return new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json' } })
      })
    )
    const user = userEvent.setup()
    render(<Notes />)
    await user.click(await screen.findByRole('button', { name: 'Open Cardiology' }))
    await user.click(screen.getByRole('button', { name: /Heart failure/ }))
    await user.click(await screen.findByRole('button', { name: 'Write' }))
    const editor = screen.getByLabelText('Note, in Markdown')
    await user.clear(editor)
    await user.type(editor, '## GDMT{enter}{enter}**SGLT2** inhibitors')
    await user.click(screen.getByRole('button', { name: /^Save/ }))
    await waitFor(() => expect(calls.find((c) => c.method === 'PATCH')?.body).toMatchObject({ body_md: '## GDMT\n\n**SGLT2** inhibitors' }))
    await user.click(screen.getByRole('button', { name: 'Read' }))
    expect(screen.getByRole('heading', { name: 'GDMT' })).toBeInTheDocument()
    await user.type(screen.getByPlaceholderText('Search notes'), 'pillars')
    expect(await screen.findByRole('button', { name: 'Heart failure' })).toBeInTheDocument()
  })
})
