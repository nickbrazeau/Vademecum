/**
 * The Socratic tutor and the Podcast Generator (ADR 0025): a dialogue that
 * answers one turn at a time and ends in an assessment; an episode written,
 * rendered with chosen voices, and played.
 */

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Podcasts } from '../src/pages/Podcasts'
import { Tutor } from '../src/pages/Tutor'

const EMPTY_ASSESSMENT = { differential: '', treatment: '', knowledge_strengths: '', knowledge_gaps: [], summary: '' }
const OPENED = {
  id: 'soc_1', entry_id: 'ency_1', topic: 'sepsis lactate', title: 'Lactate in sepsis', mode: 'codex', status: 'open', exchanges: 0, created_at: '2026-10-04T00:00:00Z', finished_at: null,
  transcript: [{ role: 'tutor', text: 'A 60-year-old presents with fever and hypotension. What is on your differential?', probe: 'differential' }], assessment: EMPTY_ASSESSMENT
}
const DONE = {
  ...OPENED, status: 'done', exchanges: 1,
  transcript: [...OPENED.transcript, { role: 'learner', text: 'Sepsis first.', probe: '' }, { role: 'tutor', text: 'Sound. That is where we stop today.', probe: 'wrap_up' }],
  assessment: { differential: 'Reasoned from sepsis outward.', treatment: '', knowledge_strengths: 'Knew the threshold.', knowledge_gaps: ['Vasopressor thresholds'], summary: 'Sound on the basics.' }
}
const EPISODE = {
  id: 'pod_1', title: 'Lactate, two ways', status: 'scripted', status_detail: '', entry_ids: ['ency_1'], voices: {}, has_audio: false, audio_bytes: 0, duration_seconds: 0, words: 14, created_at: '2026-10-04T00:00:00Z',
  script: [{ speaker: 'A', text: 'Welcome. Today, lactate in sepsis.' }, { speaker: 'B', text: 'Why does it matter?' }], takeaways: ['Lactate above 2 mmol/L marks hypoperfusion.']
}

function stub(routes: Record<string, unknown | ((body: unknown) => unknown)>) {
  const calls: { url: string; method: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET'
      const target = String(url).split('?')[0] ?? ''
      const body = init?.body ? JSON.parse(String(init.body)) : null
      calls.push({ url: target, method, body })
      const handler = routes[`${method} ${target}`] ?? routes[target] ?? {}
      const payload = typeof handler === 'function' ? (handler as (b: unknown) => unknown)(body) : handler
      return new Response(JSON.stringify(payload), { status: method === 'DELETE' ? 204 : 200, headers: { 'Content-Type': 'application/json' } })
    })
  )
  return calls
}

afterEach(() => vi.unstubAllGlobals())

describe('the Socratic tutor on the Mac', () => {
  it('starts with the tutor speaking first, takes an answer, and ends with the assessment and flagged gaps', async () => {
    let answered = 0
    const calls = stub({
      '/api/tutor/board/next': { question: null, cycle: { cycle_number: 0, position: 0, total: 0, remaining: 0, exhausted: false }, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': { open: null, recent: [], mode: 'codex', can_answer_here: true, note: '', disclosure: 'Each answer you give sends the page the session is about, the dialogue so far and your answer.' },
      'POST /api/socratic': { session: { ...OPENED, transcript: [] }, note: '', gaps_filed: 0 },
      'POST /api/socratic/soc_1/answer': () => {
        answered += 1
        return answered === 1 ? { session: OPENED, note: '', gaps_filed: 0 } : { session: DONE, note: '', gaps_filed: 1 }
      }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: 'Socratic tutor' }))
    expect(await screen.findByText(/sends the page the session is about/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start a session' }))
    expect(await screen.findByText(/What is on your differential/)).toBeInTheDocument()
    expect(calls.filter((call) => call.url === '/api/socratic/soc_1/answer')[0]?.body).toEqual({ answer: '' })
    await user.type(screen.getByLabelText('Your answer'), 'Sepsis first.')
    await user.click(screen.getByRole('button', { name: 'Answer' }))
    expect(await screen.findByRole('heading', { name: 'How it went' })).toBeInTheDocument()
    expect(screen.getByText('Vasopressor thresholds')).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent('1 gap flagged')
    expect(calls.filter((call) => call.url === '/api/socratic/soc_1/answer')[1]?.body).toEqual({ answer: 'Sepsis first.' })
  })

  it('in host mode says the conversation is the tutor and offers no answer box', async () => {
    stub({
      '/api/tutor/board/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': { open: null, recent: [], mode: 'host', can_answer_here: false, note: 'In this Vademecum the Socratic tutor runs in the conversation.', disclosure: '' }
    })
    render(<Tutor />)
    await userEvent.click(await screen.findByRole('button', { name: 'Socratic tutor' }))
    expect(await screen.findByText(/runs in the conversation/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start a session' })).not.toBeInTheDocument()
  })
})

describe('the Podcast Generator', () => {
  it('writes an episode from improvement areas, lists it with its script, and renders it with chosen voices', async () => {
    let rendered = false
    const calls = stub({
      '/api/podcasts': () => ({ episodes: [rendered ? { ...EPISODE, status: 'rendered', has_audio: true, audio_bytes: 120000, duration_seconds: 75, voices: { A: 'Karen', B: 'Daniel' } } : EPISODE], can_write: true, can_render: true, note: '', disclosure: 'Writing an episode sends the chosen pages once.' }),
      '/api/podcasts/voices': { voices: [{ name: 'Samantha', locale: 'en_US' }, { name: 'Daniel', locale: 'en_GB' }, { name: 'Karen', locale: 'en_AU' }], default: { A: 'Samantha', B: 'Daniel' } },
      '/api/encyclopedia': { entries: [], counts: {}, can_compile: true, running: false, last_refresh: null, note: '', disclosure: '' },
      'POST /api/podcasts': { ...EPISODE, status: 'draft', script: [], note: 'Writing the script now.' },
      'POST /api/podcasts/pod_1/render': () => {
        rendered = true
        return { ...EPISODE, status: 'rendered', has_audio: true, audio_bytes: 120000, duration_seconds: 75, voices: { A: 'Karen', B: 'Daniel' } }
      }
    })
    const user = userEvent.setup()
    render(<Podcasts />)
    expect(await screen.findByText(/sends the chosen pages once/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Write an episode' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Writing the script now')
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/podcasts')?.body).toMatchObject({ pick: 'improvement' })
    const episode = (await screen.findByText('Lactate, two ways')).closest('li') as HTMLElement
    expect(within(episode).getByText('Lactate above 2 mmol/L marks hypoperfusion.')).toBeInTheDocument()
    await user.selectOptions(within(episode).getByLabelText('Host A voice'), 'Karen')
    await user.click(within(episode).getByRole('button', { name: 'Render audio on this Mac' }))
    await waitFor(() => expect(calls.find((call) => call.url === '/api/podcasts/pod_1/render')?.body).toEqual({ voice_a: 'Karen', voice_b: 'Daniel' }))
    await waitFor(() => expect(screen.getByText(/audio ready · 1 page · /)).toBeInTheDocument())
    expect(document.querySelector('audio')?.getAttribute('src')).toBe('/api/podcasts/pod_1/audio')
  })
})
