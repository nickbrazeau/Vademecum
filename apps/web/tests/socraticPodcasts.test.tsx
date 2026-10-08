/**
 * The Socratic tutor and the Podcast Generator (ADR 0025): a dialogue that
 * answers one turn at a time and ends in an assessment; an episode written,
 * rendered with chosen voices, and played.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
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
    await userEvent.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    expect(await screen.findByText(/runs in the conversation/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start a session' })).not.toBeInTheDocument()
    // Launched from here into ChatGPT or Claude, with a prompt that names the tools (feedback of 5 October).
    const chatgpt = screen.getByRole('link', { name: 'Open in ChatGPT' })
    expect(chatgpt.getAttribute('href')).toMatch(/^https:\/\/chatgpt\.com\/\?q=.*socratic_start.*socratic_turn/)
    expect(chatgpt).toHaveAttribute('target', '_blank')
    expect(screen.getByRole('link', { name: 'Open in Claude' }).getAttribute('href')).toMatch(/^https:\/\/claude\.ai\/new\?q=/)
  })
})

describe("the phone's tutor, answered by the Mac", () => {
  it('starts through the relay, shows the Mac writing, then the question', async () => {
    let reads = 0
    const calls = stub({
      '/api/tutor/board/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': { open: null, recent: [], mode: 'host', can_answer_here: true, note: '', disclosure: '', relay: { available: true, live: true } },
      'POST /api/socratic': { session: { ...OPENED, mode: 'host', transcript: [], waiting: true }, note: 'Your Mac is writing the first question.', gaps_filed: 0 },
      '/api/socratic/soc_1': () => {
        reads += 1
        return { session: { ...OPENED, mode: 'host', waiting: false } }
      }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    await user.click(await screen.findByRole('button', { name: 'Start a session' }))
    expect(calls.find((call) => call.method === 'POST' && call.url === '/api/socratic')?.body).toEqual({ relay: true })
    expect(await screen.findByText(/Your Mac is writing the first question/)).toBeInTheDocument()
    expect(await screen.findByText(/What is on your differential\?/, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(reads).toBeGreaterThan(0)
    expect(calls.some((call) => call.url.endsWith('/answer'))).toBe(false)
  })

  it('wakes the Mac and says so while it is not yet answering', async () => {
    stub({
      '/api/tutor/board/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': { open: null, recent: [], mode: 'host', can_answer_here: false, note: 'being woken', disclosure: '', relay: { available: true, live: false } }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    expect(await screen.findByText(/Waking your Mac to be the tutor/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start a session' })).not.toBeInTheDocument()
    // ChatGPT's own voice is the phone's way in (feedback of 7 October); the Mac's tutor is the typed option.
    expect(screen.getByRole('link', { name: 'Continue in ChatGPT' })).toHaveClass('primary')
    expect(screen.getByText(/ChatGPT’s own voice/)).toBeInTheDocument()
    expect(screen.getByText('Or here, typed, with your Mac as the tutor')).toBeInTheDocument()
    // The hand-off is there at once, one tap (feedback of 6 October).
    expect(screen.getByRole('link', { name: 'Continue in ChatGPT' }).getAttribute('href')).toMatch(/^https:\/\/chatgpt\.com\/\?q=.*socratic_start/)
    expect(screen.getByRole('link', { name: 'Continue in Claude' })).toBeInTheDocument()
  })

  it('offers to carry the same session on in ChatGPT or Claude when the Mac goes quiet', async () => {
    stub({
      '/api/tutor/board/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': {
        open: { ...OPENED, mode: 'host', waiting: false, relay_error: 'unreachable' }, recent: [], mode: 'host', can_answer_here: true, note: '', disclosure: '',
        relay: { available: true, live: true }
      }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    // Wait for the open session to replace the start screen, then the hand-off carries it on.
    expect(await screen.findByText(/carry on this same session/i)).toBeInTheDocument()
    const hrefs = screen.getAllByRole('link', { name: 'Continue in ChatGPT' }).map((link) => decodeURIComponent(link.getAttribute('href') ?? ''))
    expect(hrefs.length).toBe(1)
    expect(hrefs[0]).toContain('socratic_start with session_id soc_1')
  })
})

describe('a Socratic session held elsewhere', () => {
  it('opens a past session to its whole dialogue, and brings in a pasted one', async () => {
    const PAST = {
      id: 'soc_9', entry_id: null, topic: 'Deep venous thrombosis', title: 'Swollen calf', mode: 'host', status: 'done', exchanges: 1,
      transcript: [{ role: 'tutor', text: 'What is on your differential?', probe: '' }, { role: 'learner', text: 'DVT, cellulitis, a Baker cyst.', probe: '' }],
      assessment: { differential: 'Broad and ordered.', treatment: '', knowledge_strengths: '', knowledge_gaps: ['Wells score'], summary: 'A good start.' },
      origin: 'chatgpt', assessed: true, created_at: '2026-10-04T20:00:00Z', finished_at: '2026-10-04T20:10:00Z'
    }
    const calls = stub({
      '/api/tutor/board/next': { question: null, cycle: { cycle_number: 0, position: 0, total: 0, remaining: 0, exhausted: false }, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': { open: null, recent: [PAST], mode: 'codex', can_answer_here: true, note: '', disclosure: 'Each answer sends…', import_disclosure: 'Bringing in a session sends its transcript once.' },
      'POST /api/socratic/import': { session: { ...PAST, id: 'soc_10', title: 'Chest pain', origin: 'pasted' }, gaps_filed: 1, note: '' }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    expect(await screen.findByText(/· from ChatGPT/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Swollen calf' }))
    expect(await screen.findByText('DVT, cellulitis, a Baker cyst.')).toBeInTheDocument()
    expect(screen.getByText('A good start.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Back' }))
    await user.click(await screen.findByText('Bring in a session from ChatGPT or Claude'))
    await user.type(screen.getByLabelText('Transcript'), 'ChatGPT: Differential?{enter}You: PE.')
    await user.click(screen.getByRole('button', { name: 'Bring it in' }))
    await waitFor(() => expect(calls.find((call) => call.url === '/api/socratic/import')?.body).toMatchObject({ text: 'ChatGPT: Differential?\nYou: PE.', origin: 'pasted' }))
    expect(await screen.findByText('1 gap flagged.')).toBeInTheDocument()
  })
})

describe('the Podcast tab', () => {
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

  it('drops an episode played to the end into the archive, and brings it back', async () => {
    let listened = false
    const audio = { ...EPISODE, status: 'rendered', has_audio: true, audio_bytes: 120000, duration_seconds: 75 }
    const calls = stub({
      '/api/podcasts': () => ({ episodes: [{ ...audio, archived: listened, listened_at: listened ? '2026-10-04T23:00:00Z' : null }], can_write: false, can_render: false, note: 'Episodes are written on the Mac.', disclosure: '' }),
      '/api/podcasts/voices': { voices: [], default: {} },
      '/api/encyclopedia': { entries: [], counts: {}, can_compile: false, running: false, last_refresh: null, note: '', disclosure: '' },
      'POST /api/podcasts/pod_1/listened': (body: unknown) => {
        listened = (body as { listened: boolean }).listened
        return { ...audio, archived: listened }
      }
    })
    const user = userEvent.setup()
    render(<Podcasts />)
    await screen.findByText('Lactate, two ways')
    expect(screen.queryByText(/Archive/)).toBeNull()
    fireEvent.ended(document.querySelector('audio') as HTMLAudioElement)
    expect(await screen.findByText('Archive (1)')).toBeInTheDocument()
    expect(screen.getByText(/Nothing new to listen to/)).toBeInTheDocument()
    expect(calls.find((call) => call.url === '/api/podcasts/pod_1/listened')?.body).toEqual({ listened: true })
    await user.click(screen.getByText('Archive (1)'))
    await user.click(screen.getByRole('button', { name: 'Back to episodes' }))
    await waitFor(() => expect(screen.queryByText('Archive (1)')).toBeNull())
    expect(screen.getByRole('button', { name: 'Mark listened' })).toBeInTheDocument()
  })

  it('makes an episode asked for in words, shows its progress, and stops at ten waiting', async () => {
    let made = false
    const calls = stub({
      '/api/podcasts': () => ({
        episodes: made
          ? [{ ...EPISODE, status: 'draft', script: [], request: 'Staph aureus bacteremia', progress: { stage: 'rendering', percent: 65, label: 'Voicing line 6 of 10' } }]
          : [],
        can_write: true, can_render: true, note: '', disclosure: 'Writing an episode sends the chosen pages once.', waiting: made ? 1 : 0, max_hosted: 10
      }),
      '/api/podcasts/voices': { voices: [{ name: 'kokoro:af_heart', label: 'Heart (Kokoro, US)', locale: 'en_US' }], default: { A: 'kokoro:af_heart', B: 'kokoro:am_michael' } },
      '/api/encyclopedia': { entries: [], counts: {}, can_compile: true, running: false, last_refresh: null, note: '', disclosure: '' },
      'POST /api/podcasts': () => {
        made = true
        return { ...EPISODE, status: 'draft', script: [], note: 'Writing the script now; it is voiced as soon as it is written.' }
      }
    })
    const user = userEvent.setup()
    render(<Podcasts />)
    expect(await screen.findByText(/0 of 10 episodes waiting/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Ask for an episode'), 'Staph aureus bacteremia')
    await user.click(screen.getByRole('button', { name: 'Make this episode' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'POST')?.body).toEqual({ pick: 'request', request: 'Staph aureus bacteremia' }))
    expect(await screen.findByText(/voiced as soon as it is written/)).toBeInTheDocument()
    expect(await screen.findByText(/Voicing line 6 of 10 · 65%/)).toBeInTheDocument()
    expect(document.querySelector('progress')?.getAttribute('value')).toBe('65')
    expect(screen.getByText(/Asked for: “Staph aureus bacteremia”/)).toBeInTheDocument()
  })

  it('refuses a new episode at ten waiting', async () => {
    stub({
      '/api/podcasts': { episodes: [], can_write: true, can_render: true, note: '', disclosure: '', waiting: 10, max_hosted: 10 },
      '/api/podcasts/voices': { voices: [], default: {} },
      '/api/encyclopedia': { entries: [], counts: {}, can_compile: true, running: false, last_refresh: null, note: '', disclosure: '' }
    })
    const user = userEvent.setup()
    render(<Podcasts />)
    expect(await screen.findByText(/10 of 10 episodes waiting to be heard. Listen to one, or remove one/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Ask for an episode'), 'Anything')
    expect(screen.getByRole('button', { name: 'Make this episode' })).toBeDisabled()
  })

  it('plays at the chosen speed, up to 2×, and saves it as a preference', async () => {
    let saved = 1
    const calls = stub({
      '/api/podcasts': { episodes: [{ ...EPISODE, status: 'rendered', has_audio: true, audio_bytes: 1000, duration_seconds: 60 }], can_write: false, can_render: false, note: '', disclosure: '', waiting: 1, max_hosted: 10 },
      '/api/podcasts/voices': { voices: [], default: {} },
      '/api/encyclopedia': { entries: [], counts: {}, can_compile: false, running: false, last_refresh: null, note: '', disclosure: '' },
      '/api/preferences': () => ({ visible_tabs: [], order: [], tabs: [], daily_goal: 20, podcast_speed: saved }),
      'PUT /api/preferences': (body: unknown) => {
        saved = (body as { podcast_speed: number }).podcast_speed
        return { visible_tabs: [], order: [], tabs: [], daily_goal: 20, podcast_speed: saved }
      }
    })
    const user = userEvent.setup()
    const { unmount } = render(<Podcasts />)
    await screen.findByText('Lactate, two ways')
    const audio = document.querySelector('audio') as HTMLAudioElement
    expect(audio.playbackRate).toBe(1)
    await user.click(screen.getByRole('button', { name: '2×' }))
    expect(screen.getByRole('button', { name: '2×' })).toHaveAttribute('aria-pressed', 'true')
    expect(audio.playbackRate).toBe(2)
    await waitFor(() => expect(calls.find((call) => call.method === 'PUT')?.body).toEqual({ podcast_speed: 2 }))
    unmount()
    render(<Podcasts />)
    await screen.findByText('Lactate, two ways')
    await waitFor(() => expect((document.querySelector('audio') as HTMLAudioElement).playbackRate).toBe(2))
  })

  it("leads an open session on the phone with ChatGPT's voice, the typed box folded away", async () => {
    stub({
      '/api/tutor/board/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/tutor/next': { question: null, cycle: {}, empty_reason: 'none' },
      '/api/socratic': {
        open: { ...OPENED, mode: 'host', waiting: false, relay_error: '' }, recent: [], mode: 'host', can_answer_here: true, note: '', disclosure: '',
        relay: { available: true, live: true }
      }
    })
    const user = userEvent.setup()
    render(<Tutor />)
    await user.click(await screen.findByRole('button', { name: /^Socratic tutor/ }))
    expect(await screen.findByText(/Carry this session on in ChatGPT’s own voice/)).toBeInTheDocument()
    const link = screen.getByRole('link', { name: 'Continue in ChatGPT' })
    expect(link).toHaveClass('primary')
    expect(decodeURIComponent(link.getAttribute('href') ?? '')).toContain('session_id soc_1')
    expect(screen.getByText('Or answer here, typed, with your Mac as the tutor')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'End session' })).toBeInTheDocument()
  })

  it('opens on one page from the Improvement Map: its questions, and the Socratic tutor on it', async () => {
    const { openTutorLater } = await import('../src/lib/pageLink')
    const calls = stub({
      '/api/tutor/board/next': { question: null, cycle: { cycle_number: 0, position: 0, total: 0, remaining: 0, exhausted: false }, empty_reason: 'This page has no board questions ready yet.' },
      '/api/socratic': { open: null, recent: [], mode: 'codex', can_answer_here: true, note: '', disclosure: '', relay: { available: false, live: false } },
      'POST /api/socratic': { session: { ...OPENED, transcript: [] }, note: '', gaps_filed: 0 },
      'POST /api/socratic/soc_1/answer': { session: OPENED, note: '', gaps_filed: 0 }
    })
    openTutorLater({ mode: 'questions', entryId: 'ency_9', title: 'Cirrhosis' })
    const user = userEvent.setup()
    render(<Tutor />)
    expect(await screen.findByText(/On one page: Cirrhosis/)).toBeInTheDocument()
    expect(calls.some((call) => call.url === '/api/tutor/board/next' && call.method === 'GET')).toBe(true)
    expect(await screen.findByText(/no board questions ready yet/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Socratic tutor' }))
    await user.click(await screen.findByRole('button', { name: 'Start a session' }))
    await waitFor(() => expect(calls.find((call) => call.method === 'POST' && call.url === '/api/socratic')?.body).toEqual({ entry_id: 'ency_9' }))
  })
})
