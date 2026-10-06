/**
 * A new pile by dropping files on the web app (feedback of 5 October): filed on the
 * Mac in the source folder, reported by name and folder, never by full path.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { FolderDrop } from '../src/components/FolderDrop'

afterEach(() => vi.unstubAllGlobals())

describe('the New pile drop', () => {
  it('sends the pile, its confidence and the files, and says where they were filed', async () => {
    const sent: FormData[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init?: RequestInit) => {
        sent.push(init?.body as FormData)
        return new Response(
          JSON.stringify({ folder: 'piles/highconfidence/Sepsis lectures', files: [{ filename: 'lecture.pdf', status: 'placed' }] }),
          { status: 201, headers: { 'Content-Type': 'application/json' } }
        )
      })
    )
    const user = userEvent.setup()
    const placed = vi.fn()
    render(<FolderDrop onPlaced={placed} />)
    const button = screen.getByRole('button', { name: 'File them in the source folder' })
    expect(button).toBeDisabled()
    await user.type(screen.getByLabelText('Pile name'), 'Sepsis lectures')
    await user.selectOptions(screen.getByLabelText('How far you trust it'), 'high')
    await user.upload(screen.getByTestId('folder-drop-input'), new File(['%PDF-1.4'], 'lecture.pdf', { type: 'application/pdf' }))
    expect(screen.getByText('1 file ready')).toBeInTheDocument()
    await user.click(button)
    await waitFor(() => expect(placed).toHaveBeenCalled())
    const form = sent[0] as FormData
    expect(form.get('pile')).toBe('Sepsis lectures')
    expect(form.get('confidence')).toBe('high')
    expect((form.get('files') as File).name).toBe('lecture.pdf')
    expect(screen.getByRole('status')).toHaveTextContent('Filed in piles/highconfidence/Sepsis lectures')
  })
})
