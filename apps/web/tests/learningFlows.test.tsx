import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BuildPanel } from '../src/components/BuildPanel'
import { CoverageBar } from '../src/components/CoverageBar'
import { LiteratureSettings } from '../src/components/LiteratureSettings'
import { PaperLink } from '../src/components/PaperLink'
import { UploadPanel } from '../src/components/UploadPanel'
import { answerDraftKey, loadDraft } from '../src/lib/drafts'
import { api } from '../src/lib/api'
import { coverage } from '../src/lib/normalize'
import { Sources } from '../src/pages/Sources'
import { Today } from '../src/pages/Today'
import { Tutor } from '../src/pages/Tutor'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' }
})
type Call = { path: string; method: string; body: RequestInit['body'] }
function backend(handler: (call: Call) => Response | Promise<Response>) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const call = { path: String(url), method: init.method || 'GET', body: init.body }
    calls.push(call)
    return handler(call)
  }))
  return calls
}
const question = { id: 'q1', prompt: 'Explain retrieval practice.', support: 'evidence_supported', status: 'eligible' }
const shown = { ...question, reference_answer: 'Recall without looking.', rubric: 'Mention recall.' }
const next = { question, cycle: { total: 2, remaining: 1, cycle_number: 1 } }
const preview = {
  batch_id: 'b1', selection_hash: 'abc',
  excerpts: [{ source_id: 's1', segment_id: 'g1', display_name: 'Study.txt', confidence: 'mid',
    locator: 'Paragraph 1', range_label: 'Paragraph 1, characters 11–20', text: 'Recall now', start: 10, end: 20, char_count: 10 }],
  coverage: { chars_total: 25, chars_covered: 10, chars_in_batch: 10, chars_remaining_after: 5 },
  disclosure: { headline: 'Build sends selected excerpts and follow-up checks',
    bullets: ['Selected file text, confidence and location', 'Generated claims, questions, reference answers and rubrics with evidence abstracts'],
    destination: 'OpenAI via ChatGPT; public topic words to PubMed' }
}
afterEach(() => vi.unstubAllGlobals())

describe('wire contracts and extracted-text coverage', () => {
  it('uses true partial character coverage and never rounds incomplete material to100%', () => {
    const { container } = render(<CoverageBar coverage={coverage({ chars_total: 1000, chars_covered: 999, complete: false, percent: 99.9 })} />)
    expect(screen.getByText(/999 of 1000 extracted-text characters processed/)).toBeVisible()
    expect(container.querySelector('.usage-fill')).toHaveStyle({ width: '99%' })
    expect(screen.getByText(/not diagrams, image-only pages or omitted text/)).toBeVisible()
  })
  it('normalizes missing nested fields without fabricating a grade or running build', async () => {
    backend(() => json({}))
    const today = await api.today()
    expect(today.literature.updates).toEqual([])
    expect(today.held.reasons).toEqual([])
    expect((await api.buildStatus('p1')).run).toBeNull()
    expect((await api.cancelBuild('p1')).stopped).toBe(false)
    expect((await api.tutorGrade('q1', 'Study answer')).graded).toBe(false)
  })
  it('shows flat literature records as direct safe PubMed links without inventing recency', async () => {
    backend(({ path }) => json(path === '/api/today' ? { literature: { updates: [{
      id: 'u1', title: 'Study methods paper', pmid: '123456', topic_label: 'Learning', state: 'unread',
      published_on: '2000-01-01', first_seen_at: '2026-09-10'
    }] } } : {}))
    render(<Today reloadToken={0} />)
    expect(await screen.findByRole('link', { name: 'Study methods paper' })).toHaveAttribute('href', 'https://pubmed.ncbi.nlm.nih.gov/123456/')
    expect(screen.queryByText('Recently published')).not.toBeInTheDocument()
  })
  it('never turns an untrusted citation identifier into an executable destination', () => {
    render(<PaperLink pmid="javascript:alert(1)" title="Unsafe identifier" />)
    expect(screen.getByText('Unsafe identifier')).toBeVisible()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})

describe('deliberate source intake and Build consent', () => {
  it('refreshes the parent pile summary after file upload, note save, and completed Build', async () => {
    let sourceCount = 0
    let noteCount = 0
    let built = false
    const source = { id: 's1', display_name: 'Study.txt', status: 'extracted', confidence: 'mid' }
    const finishedRun = { id: 'r1', status: 'succeeded', point_count: 1, question_count: 2 }
    const calls = backend(({ path, method }) => {
      if (path === '/api/piles') return json([{
        id: 'p1', title: 'Study methods', tier: 'mid', source_count: sourceCount,
        item_count: noteCount, point_count: built ? 1 : 0, question_count: built ? 2 : 0,
        coverage: { chars_total: sourceCount ? 25 : 0, chars_covered: built ? 25 : 0, complete: built }
      }])
      if (path.endsWith('/sources')) {
        if (method === 'POST') {
          sourceCount = 1
          return json({ accepted: 1, results: [{ filename: 'Study.txt', outcome: 'stored', source }] })
        }
        return json(sourceCount ? [source] : [])
      }
      if (path.endsWith('/items')) {
        if (method === 'POST') { noteCount = 1; return json({ id: 'n1', title: 'Recall note', body: 'Recall first.' }) }
        return json(noteCount ? [{ id: 'n1', title: 'Recall note', body: 'Recall first.' }] : [])
      }
      if (path.endsWith('/preview')) return json(preview)
      if (path.endsWith('/build') && method === 'POST') { built = true; return json({ run: finishedRun }) }
      if (path.endsWith('/status')) return json({ run: built ? finishedRun : null,
        coverage: { chars_total: sourceCount ? 25 : 0, chars_covered: built ? 25 : 0, complete: built } })
      return json([])
    })
    render(<Sources />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Study methods/ }))
    await user.upload(await screen.findByLabelText('Add files'), new File(['Recall practice'], 'Study.txt', { type: 'text/plain' }))
    await user.click(screen.getByRole('button', { name: 'Add file' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Study methods/ })).toHaveTextContent('1 files · 0 notes'))
    expect(screen.getByRole('button', { name: /Study methods/ })).toHaveTextContent('0 of 25 extracted-text characters processed')
    await user.type(await screen.findByLabelText('Add a learning item'), 'Recall note')
    await user.type(screen.getByRole('textbox', { name: /What it says/ }), 'Recall first.')
    await user.click(screen.getByRole('button', { name: 'Add item' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Study methods/ })).toHaveTextContent('1 files · 1 notes'))
    await user.click(await screen.findByRole('button', { name: 'Build learning material' }))
    await user.click(await screen.findByRole('button', { name: 'Send this batch to the model' }))
    await waitFor(() => expect(screen.getByRole('button', { name: /Study methods/ })).toHaveTextContent('1 files · 1 notes · 1 points · 2 questions · fully processed'))
    expect(calls.filter((call) => call.path === '/api/piles' && call.method === 'GET').length).toBeGreaterThanOrEqual(4)
  })
  it('shows exact ranges and follow-up transmissions before sending the acknowledged selection', async () => {
    const calls = backend(({ path, method }) => {
      if (path.endsWith('/status')) return json({ coverage: { chars_total: 25, chars_covered: 10 }, run: null })
      if (path.endsWith('/preview')) return json(preview)
      if (method === 'POST') return json({ run: { id: 'r1', status: 'succeeded', held_count: 1 } })
      return json({})
    })
    render(<BuildPanel pileId="p1" onChanged={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Build learning material' }))
    expect(await screen.findByText(/Paragraph 1, characters 11–20/)).toBeVisible()
    expect(screen.getByText(/follow-up evidence and question checks also send/i)).toBeVisible()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Send this batch to the model' }))
    await screen.findByText(/not eligible for Tutor/)
    expect(calls.filter((call) => call.method === 'POST')).toEqual([
      { path: '/api/piles/p1/build', method: 'POST', body: JSON.stringify({ batch_id: 'b1', selection_hash: 'abc' }) }
    ])
  })
  it('does not assert no content was sent after an uncertain dispatched Build', async () => {
    backend(({ path, method }) => {
      if (method === 'POST') throw new TypeError('Lost response')
      return json(path.endsWith('/preview') ? preview : {})
    })
    render(<BuildPanel pileId="p1" onChanged={() => {}} />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Build learning material' }))
    await user.click(await screen.findByRole('button', { name: 'Send this batch to the model' }))
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(/may have started/i)
    expect(alert).not.toHaveTextContent(/nothing was sent/i)
  })
  it('fails closed if a preview has no consent hash even if an excerpt is present', async () => {
    backend(({ path }) => json(path.endsWith('/preview') ? { ...preview, selection_hash: '' } : {}))
    render(<BuildPanel pileId="p1" onChanged={() => {}} />)
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Build learning material' }))
    expect(await screen.findByRole('button', { name: 'Send this batch to the model' })).toBeDisabled()
  })
  it('retains chosen files and admits a partial upload may have succeeded after disconnect', async () => {
    backend(() => { throw new TypeError('Lost response') })
    render(<UploadPanel pileId="p1" defaultConfidence="mid" onUploaded={() => {}} />)
    const user = userEvent.setup()
    const input = screen.getByLabelText('Add files') as HTMLInputElement
    await user.upload(input, new File(['Recall'], 'Study.txt', { type: 'text/plain' }))
    await user.click(screen.getByRole('button', { name: 'Add file' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/some files may already be stored/i)
    expect(input.files).toHaveLength(1)
    expect(screen.getByText(/No OCR or diagram interpretation/)).toBeVisible()
  })
  it('copies a saved note to a local text source only on explicit choice and keeps the note', async () => {
    const note = { id: 'n1', title: 'Recall note', body: 'Try recall before reading.', source: 'Study guide' }
    const calls = backend(({ path, method }) => {
      if (path === '/api/piles') return json([{ id: 'p1', title: 'Study methods', tier: 'mid' }])
      if (path.endsWith('/items')) return json([note])
      if (path.endsWith('/sources') && method === 'POST') return json({ accepted: 1, results: [{ filename: 'Recall note.txt', outcome: 'stored' }] })
      return json(path.endsWith('/sources') || path.startsWith('/api/points') ? [] : {})
    })
    render(<Sources />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /Study methods/ }))
    const button = await screen.findByRole('button', { name: 'Use as source' })
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    await user.click(button)
    expect(await screen.findByRole('status')).toHaveTextContent(/original note is kept/i)
    const posts = calls.filter((call) => call.method === 'POST')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.path).toBe('/api/piles/p1/sources')
    const data = posts[0]?.body as FormData
    expect(data.get('confidence')).toBe('mid')
    expect((data.get('files') as File).name).toBe('Recall note.txt')
    expect(screen.getByText('Recall note')).toBeVisible()
  })
})

describe('Tutor verdicts and local drafts', () => {
  it.each(['patient_specific', 'not_eligible', 'stale'])('preserves the draft with no invented verdict for a %s refusal', async (refused) => {
    const calls = backend(({ path }) => json(path.endsWith('/grade')
      ? { attempt: null, refused, message: 'Restate as a general learning answer.', question }
      : next))
    render(<Tutor />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Board questions/ }))
    const answer = await screen.findByRole('textbox', { name: 'Your answer' })
    await user.type(answer, 'My unfinished general study response')
    await user.click(screen.getByRole('button', { name: 'Grade with the model' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/no attempt was recorded/i)
    expect(answer).toHaveValue('My unfinished general study response')
    expect(loadDraft(answerDraftKey('q1'))).toBe('My unfinished general study response')
    expect(screen.queryByRole('heading', { name: 'Correct' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /judge it yourself/i })).not.toBeInTheDocument()
    expect(calls.filter((call) => call.method === 'POST')).toHaveLength(1)
  })
  it('uses a recorded model grade and distinctly labels self-assessment after a timeout', async () => {
    let fail = false
    const calls = backend(({ path }) => {
      if (path.endsWith('/grade')) return fail
        ? json({ error: { message: 'The model reply timed out.', code: 'unavailable' } }, 503)
        : json({ question: shown, attempt: { id: 'a1', question_id: 'q1', graded_by: 'model', outcome: 'correct', feedback: 'Reference matched.' } })
      if (path.endsWith('/reveal')) return json({ question: shown })
      if (path.endsWith('/self-assess')) return json({ attempt: { id: 'a2', question_id: 'q1', graded_by: 'self', outcome: 'self_assessed' } })
      return json(next)
    })
    const first = render(<Tutor />)
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: /^Board questions/ }))
    await user.type(await screen.findByRole('textbox', { name: 'Your answer' }), 'Recall without looking.')
    await user.click(screen.getByRole('button', { name: 'Grade with the model' }))
    expect(await screen.findByRole('heading', { name: 'Correct' })).toBeVisible()
    expect(loadDraft(answerDraftKey('q1'))).toBe('')
    first.unmount()
    fail = true
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Board questions/ }))
    await user.type(await screen.findByRole('textbox', { name: 'Your answer' }), 'My next response')
    await user.click(screen.getByRole('button', { name: 'Grade with the model' }))
    expect(await screen.findByRole('heading', { name: /judge it yourself/i })).toBeVisible()
    expect(screen.getByRole('alert')).toHaveTextContent(/no model grade was recorded/i)
    expect(loadDraft(answerDraftKey('q1'))).toBe('My next response')
    await user.click(screen.getByRole('button', { name: 'Partly' }))
    expect(await screen.findByRole('heading', { name: /You judged this yourself/ })).toBeVisible()
    expect(calls.filter((call) => call.path.endsWith('/self-assess'))).toHaveLength(1)
  })
})

describe('weekly literature opt-in and suggestions', () => {
  function literatureBackend(rejectSetting = false) {
    let enabled = false
    let watched = false
    return backend(({ path, method, body }) => {
      if (path.endsWith('/settings')) {
        if (method === 'PUT') {
          if (rejectSetting) return json({ error: { code: 'invalid_request', message: 'Could not save the setting.' } }, 503)
          enabled = JSON.parse(String(body)).weekly_enabled as boolean
        }
        return json({ weekly_enabled: enabled, enabled: true, running: enabled, interval_hours: 168 })
      }
      if (path.endsWith('/suggestions')) return json({ suggestions: [{ topic: 'retrieval practice', query: '"retrieval practice"', point_count: 2, already_watched: watched }], note: 'Choose a public topic.' })
      if (path.endsWith('/topics')) {
        if (method === 'POST') watched = true
        return json(watched ? [{ id: 't1', label: 'retrieval practice', query: '"retrieval practice"', enabled: true }] : [])
      }
      if (path.endsWith('/check')) return json({ checks: [], message: 'No watched topics to check.', summary: {} })
      return json({})
    })
  }
  it('is off on load and enables weekly checks only with the checkbox, not a suggested watch', async () => {
    const calls = literatureBackend()
    render(<LiteratureSettings onChecked={() => {}} />)
    const user = userEvent.setup()
    const checkbox = await screen.findByRole('switch', { name: 'Check weekly while Vademecum is running' })
    expect(checkbox).not.toBeChecked()
    expect(calls.every((call) => call.method === 'GET')).toBe(true)
    await user.click(await screen.findByText('Suggested watch topics'))
    await user.click(await screen.findByRole('switch', { name: 'Watch retrieval practice' }))
    await waitFor(() => expect(screen.getByRole('switch', { name: 'Watch retrieval practice' })).toBeChecked())
    expect(screen.getByRole('switch', { name: /Check weekly/ })).not.toBeChecked()
    expect(calls.filter((call) => call.method === 'PUT')).toHaveLength(0)
    await user.click(screen.getByRole('switch', { name: /Check weekly/ }))
    await waitFor(() => expect(screen.getByRole('switch', { name: /Check weekly/ })).toBeChecked())
    expect(calls.find((call) => call.method === 'PUT')?.body).toBe(JSON.stringify({ weekly_enabled: true, interval_hours: 168 }))
    expect(screen.getByText(/weekly watcher is active/)).toBeVisible()
  })
  it('does not claim weekly checks are enabled when saving fails', async () => {
    literatureBackend(true)
    render(<LiteratureSettings onChecked={() => {}} />)
    await userEvent.setup().click(await screen.findByRole('switch', { name: /Check weekly/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save the setting.')
    expect(await screen.findByRole('switch', { name: /Check weekly/ })).not.toBeChecked()
  })
  it('shows an empty check report without invented result counts', async () => {
    literatureBackend()
    render(<LiteratureSettings onChecked={() => {}} />)
    await userEvent.setup().click(screen.getByRole('button', { name: 'Check for new literature now' }))
    expect(await screen.findByRole('status')).toHaveTextContent('No watched topics to check.')
    expect(screen.getByRole('status')).not.toHaveTextContent(/new to your library/)
  })
})
