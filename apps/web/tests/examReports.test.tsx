/**
 * Exam reports (ADR 0020): a disclosure before the button, the upload itself,
 * the areas listed, and the graph drawing a below-the-mark area.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ExamReports } from '../src/components/ExamReports'
import { buildGraph } from '../src/components/TopicGraph'

const REPORT = {
  id: 'rpt_1', display_name: 'ite-2026.pdf', media_type: 'application/pdf', byte_size: 1000, status: 'parsed', status_detail: '3 content areas read.',
  created_at: '2026-10-03T00:00:00Z', parsed_at: '2026-10-03T00:01:00Z', text_chars: 900,
  areas: [{ id: 'a1', report_id: 'rpt_1', ordinal: 0, topic: 'Infectious Disease', specialty_id: 'infectious-disease', standing: 'below', quote: 'ID: 38th', note: '', created_at: '2026-10-03T00:01:00Z' }]
}

function stubApi(listed: unknown[]) {
  const calls: { url: string; method: string }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      calls.push({ url: String(url), method })
      if (method === 'POST') return new Response(JSON.stringify({ ...REPORT, status: 'uploaded', areas: [], note: 'Reading it now; the map updates when it is done.' }), { status: 201, headers: { 'Content-Type': 'application/json' } })
      if (method === 'DELETE') return new Response(null, { status: 204 })
      return new Response(JSON.stringify(listed), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('the exam reports card', () => {
  it('states the disclosure, uploads a report and says what happens next', async () => {
    const calls = stubApi([])
    render(<ExamReports onChanged={() => undefined} />)
    expect(await screen.findByText(/sends its text to the Mac’s own model connection/)).toBeInTheDocument()
    const file = new File(['Infectious Disease: 38th percentile'], 'ite.txt', { type: 'text/plain' })
    await userEvent.upload(screen.getByLabelText('Add a score report'), file)
    await userEvent.click(screen.getByRole('button', { name: 'Add and read it' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'POST' && c.url === '/api/improvement-map/reports')).toBe(true))
    expect(await screen.findByRole('status')).toHaveTextContent('Reading it now')
  })

  it('lists a read report with its areas and offers removal', async () => {
    const calls = stubApi([REPORT])
    render(<ExamReports onChanged={() => undefined} />)
    expect(await screen.findByText('Infectious Disease: below')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(calls.some((c) => c.method === 'DELETE')).toBe(true))
  })
})

describe('the graph', () => {
  it('draws a below-the-mark area even when only open flags are shown, and marks a known topic', () => {
    const graph = buildGraph(
      [{ topic: 'Sepsis', open_flags: 1, addressed_flags: 0, last_flagged_at: null, cluster: null, specialty: null }],
      [],
      [],
      {
        openOnly: true,
        reports: [
          { topic: 'Infectious Disease', specialty_id: 'infectious-disease', standing: 'below', note: '', quote: '', report_id: 'r', report: 'ite', reported_at: '' },
          { topic: 'Cardiology', specialty_id: 'cardiology', standing: 'above', note: '', quote: '', report_id: 'r', report: 'ite', reported_at: '' },
          { topic: 'Sepsis', specialty_id: null, standing: 'at', note: '', quote: '', report_id: 'r', report: 'ite', reported_at: '' }
        ]
      }
    )
    const byId = Object.fromEntries(graph.nodes.map((n) => [n.id, n]))
    expect(byId['Infectious Disease']?.standing).toBe('below')
    expect(byId['Infectious Disease']?.specialty).toBe('infectious-disease')
    expect(byId['Cardiology']).toBeUndefined()
    expect(byId['Sepsis']?.standing).toBe('at')
  })
})
