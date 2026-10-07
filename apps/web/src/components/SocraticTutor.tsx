/**
 * The Socratic tutor on the Mac (ADR 0025): one open question at a time, in
 * text, with the browser's dictation and speech where it offers them. Each
 * answer is one model turn on your own sign-in, and the disclosure says so.
 * In a chat host the assistant is the tutor instead, and this card says how.
 * A session held elsewhere -- ChatGPT's voice mode keeps its conversation to
 * itself -- is brought in by the assistant's socratic_save or by pasting the
 * transcript here (ADR 0026); past sessions open to their whole dialogue.
 */

import { useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { RouteName } from '../lib/router'
import { canDictate, canSpeak, dictate, speak } from '../lib/speech'
import type { SocraticOverview, SocraticSession } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { PhiWarning } from './PhiWarning'

// Launching the tutor in ChatGPT or Claude from here (feedback of 5 October). The prompt
// names the tools, so the assistant records each exchange, and asks it to say so plainly
// when it cannot reach them (voice mode, or a connector not yet refreshed).
export const LAUNCH_PROMPT =
  'Using my Vademecum connector, start a Socratic session: call socratic_start, then ask me one open question at a time, ' +
  'record every exchange with socratic_turn, and close with socratic_finish. If you cannot call the Vademecum tools here, ' +
  'tell me before we begin.'
export const CHATGPT_LAUNCH = `https://chatgpt.com/?q=${encodeURIComponent(LAUNCH_PROMPT)}`
export const CLAUDE_LAUNCH = `https://claude.ai/new?q=${encodeURIComponent(LAUNCH_PROMPT)}`

const ORIGIN_LABEL: Record<string, string> = { chatgpt: 'from ChatGPT', claude: 'from Claude', pasted: 'pasted in' }

const PROBE_LABEL: Record<string, string> = {
  differential: 'the differential',
  treatment: 'treatment',
  knowledge: 'knowledge',
  wrap_up: 'wrapping up'
}

function Assessment({ session }: { session: SocraticSession }) {
  const a = session.assessment
  return (
    <section className="card" aria-labelledby="socratic-assessment-heading">
      <h3 id="socratic-assessment-heading">How it went</h3>
      {a.summary ? <p className="body">{a.summary}</p> : null}
      {a.differential ? (
        <p className="body">
          <strong>Differential:</strong> {a.differential}
        </p>
      ) : null}
      {a.treatment ? (
        <p className="body">
          <strong>Treatment:</strong> {a.treatment}
        </p>
      ) : null}
      {a.knowledge_strengths ? (
        <p className="body">
          <strong>Strengths:</strong> {a.knowledge_strengths}
        </p>
      ) : null}
      {a.knowledge_gaps.length > 0 ? (
        <>
          <h4>Gaps, now flagged</h4>
          <ul className="chips">
            {a.knowledge_gaps.map((gap) => (
              <li key={gap} className="chip">
                {gap}
              </li>
            ))}
          </ul>
          <p className="muted small">Each gap is a flag on this topic, so the Improvement Map draws it and the flashcards weigh it.</p>
        </>
      ) : null}
    </section>
  )
}

export function SocraticTutor({ onNavigate }: { onNavigate?: (name: RouteName) => void }) {
  const { result, reload } = useLoad(() => api.socraticOverview(), [])
  const [session, setSession] = useState<SocraticSession | null>(null)
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [note, setNote] = useState('')
  const [listening, setListening] = useState(false)
  const [voiceOn, setVoiceOn] = useState(false)
  const [pasted, setPasted] = useState('')
  const [pastedTitle, setPastedTitle] = useState('')
  const stopListening = useRef<() => void>(() => undefined)
  const stopSpeaking = useRef<() => void>(() => undefined)

  useEffect(() => {
    if (result.state === 'ready') setSession(result.value.open)
  }, [result])

  // The phone's tutor (feedback of 6 October): while the Mac is being woken, look again
  // every few seconds; give up after a minute and say so.
  const relayAvailable = result.state === 'ready' && result.value.relay.available
  const relayLive = result.state === 'ready' && result.value.relay.live
  const [wakingSince] = useState(() => Date.now())
  const [macAsleep, setMacAsleep] = useState(false)
  useEffect(() => {
    if (!relayAvailable || relayLive || macAsleep) return undefined
    const timer = window.setInterval(() => {
      if (Date.now() - wakingSince > 60_000) setMacAsleep(true)
      else reload()
    }, 3000)
    return () => window.clearInterval(timer)
  }, [relayAvailable, relayLive, macAsleep, reload, wakingSince])

  // While the Mac writes the next turn, look for it every second and a half.
  useEffect(() => {
    if (!session?.waiting) return undefined
    const id = session.id
    const timer = window.setInterval(() => {
      api.socraticRead(id).then(
        (reply) => setSession(reply.session),
        () => undefined
      )
    }, 1500)
    return () => window.clearInterval(timer)
  }, [session?.waiting, session?.id])

  useEffect(() => () => {
    stopListening.current()
    stopSpeaking.current()
  }, [])

  const lastTutorLine = session?.transcript.filter((turn) => turn.role === 'tutor').at(-1)
  // Voice mode: the question is spoken, then the tutor listens, and what you say
  // is sent when you stop speaking. The browser's own voice and dictation; nothing else.
  const answerRef = useRef('')
  answerRef.current = answer
  const sessionRef = useRef<SocraticSession | null>(null)
  sessionRef.current = session
  const listen = (autoSend: boolean) => {
    setListening(true)
    stopListening.current = dictate(
      (text) => setAnswer(text),
      () => {
        setListening(false)
        const current = sessionRef.current
        if (autoSend && current && current.status === 'open' && answerRef.current.trim()) {
          const said = answerRef.current
          void act(() => api.socraticAnswer(current.id, said))
        }
      }
    )
  }
  useEffect(() => {
    if (!voiceOn || !lastTutorLine) return
    stopSpeaking.current = speak(lastTutorLine.text, {
      onEnd: () => {
        if (sessionRef.current?.status === 'open' && canDictate()) listen(true)
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voiceOn, lastTutorLine])

  if (result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (result.state === 'failed') {
    return (
      <section className="card" aria-labelledby="socratic-heading">
        <h2 id="socratic-heading">Socratic tutor</h2>
        <p className="muted small">Not readable right now. {result.error.message}</p>
      </section>
    )
  }
  const overview: SocraticOverview = result.value

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  const act = async (action: () => Promise<{ session: SocraticSession; note: string; gaps_filed: number }>) => {
    setBusy(true)
    setFailure(null)
    setNote('')
    try {
      const reply = await action()
      setSession(reply.session)
      setNote(reply.note || (reply.gaps_filed ? `${reply.gaps_filed} gap${reply.gaps_filed === 1 ? '' : 's'} flagged.` : ''))
      setAnswer('')
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const start = () => act(async () => {
    if (relayAvailable) {
      // The Mac writes the opening question; the page shows it when it comes.
      return api.socraticStart(undefined, true)
    }
    const started = await api.socraticStart()
    // The first turn opens the dialogue: no answer yet, the tutor speaks first.
    return overview.can_answer_here ? api.socraticAnswer(started.session.id, '') : started
  })

  const toggleListening = () => {
    if (listening) {
      stopListening.current()
      setListening(false)
      return
    }
    listen(false)
  }

  const heading = (
    <h2 id="socratic-heading">Socratic tutor</h2>
  )

  if (session === null) {
    return (
      <section className="card socratic" aria-labelledby="socratic-heading">
        {heading}
        <p className="muted">
          A dialogue grounded in one page of your encyclopedia: open questions through the differential, the treatment options and
          the knowledge underneath, one at a time, never the answer first. The tutor assesses and probes beyond the page from the
          literature reviewed for it, related pages, and its own knowledge, saying which is which. At the end, how you reasoned and
          what to revisit; each gap becomes a flag.
        </p>
        {overview.can_answer_here ? (
          <>
            <p className="muted small">{overview.disclosure}</p>
            <div className="actions">
              <button type="button" className="button primary" disabled={busy} onClick={() => void start()}>
                {busy ? 'Starting…' : 'Start a session'}
              </button>
              {canSpeak() && canDictate() ? (
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  onClick={() => {
                    setVoiceOn(true)
                    void start()
                  }}
                >
                  Start with voice
                </button>
              ) : null}
            </div>
          </>
        ) : relayAvailable ? (
          macAsleep ? (
            <p className="muted small" role="status">
              Your Mac is not answering. It needs to be on, awake and running Vademecum to be the tutor here. Meanwhile, use ChatGPT or Claude
              below.{' '}
              <button type="button" className="link-button" onClick={() => { setMacAsleep(false); reload() }}>
                Try again
              </button>
            </p>
          ) : (
            <p className="muted small" role="status">
              <span className="spinner" aria-hidden="true" /> Waking your Mac to be the tutor…
            </p>
          )
        ) : (
          <p className="muted small">{overview.note}</p>
        )}
        <div className="socratic-launch">
          <h3>Or with ChatGPT or Claude</h3>
          <div className="actions">
            <a className="button" href={CHATGPT_LAUNCH} target="_blank" rel="noopener noreferrer">
              Open in ChatGPT
            </a>
            <a className="button" href={CLAUDE_LAUNCH} target="_blank" rel="noopener noreferrer">
              Open in Claude
            </a>
            <button
              type="button"
              className="button ghost"
              onClick={() => {
                void navigator.clipboard?.writeText(LAUNCH_PROMPT).then(
                  () => setNote('The prompt is copied. Paste it in a chat with Vademecum connected.'),
                  () => setNote(LAUNCH_PROMPT)
                )
              }}
            >
              Copy the prompt
            </button>
          </div>
          <p className="muted small">
            This opens a text chat with the prompt filled in, and every exchange is saved here as you go. Speak your answers with the
            microphone’s dictation in that chat. Voice mode in those apps calls no tools, so a voice conversation does not reach Vademecum;
            after one, say “Save that Socratic session to Vademecum” in a text chat, or paste the transcript below. If the assistant says it
            cannot reach Vademecum, refresh the connector in its settings.
          </p>
        </div>
        <details className="support-details">
          <summary>Bring in a session from ChatGPT or Claude</summary>
          <p className="muted small">
            Paste the whole conversation, as copied from the app. It is saved with its dialogue, named, assessed, and its gaps become
            flags.{overview.can_answer_here ? '' : ' Assessing happens on the Mac, the next time you open the Tutor there.'}
          </p>
          {overview.can_answer_here && overview.import_disclosure ? <p className="muted small">{overview.import_disclosure}</p> : null}
          <label className="field">
            <span>Transcript</span>
            <textarea name="socratic-import" rows={8} value={pasted} maxLength={60000} onChange={(event) => setPasted(event.target.value)} />
          </label>
          <label className="field">
            <span>Title (optional)</span>
            <input type="text" value={pastedTitle} maxLength={120} onChange={(event) => setPastedTitle(event.target.value)} />
          </label>
          <div className="actions">
            <button
              type="button"
              className="button"
              disabled={busy || pasted.trim() === ''}
              onClick={() =>
                void act(async () => {
                  const reply = await api.socraticImport({ text: pasted, title: pastedTitle.trim() || undefined })
                  setPasted('')
                  setPastedTitle('')
                  return reply
                })
              }
            >
              {busy ? 'Bringing it in…' : 'Bring it in'}
            </button>
          </div>
        </details>
        {overview.recent.length > 0 ? (
          <section aria-labelledby="past-sessions-heading">
            <h3 id="past-sessions-heading">Past sessions</h3>
            <ul className="list small">
              {overview.recent.map((past) => (
                <li key={past.id}>
                  <button type="button" className="link-button title" onClick={() => setSession(past)}>
                    {past.title || past.topic || 'Untitled session'}
                  </button>
                  <span className="muted small">
                    {' '}
                    · {past.exchanges} exchange{past.exchanges === 1 ? '' : 's'}
                    {past.origin ? ` · ${ORIGIN_LABEL[past.origin] ?? past.origin}` : ''}
                    {past.status === 'abandoned' ? ' · ended early' : past.assessed ? '' : ' · not yet assessed'}
                  </span>
                  {past.assessment.summary ? <p className="muted small">{past.assessment.summary}</p> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {note ? (
          <p className="ok small" role="status">
            {note}
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>
    )
  }

  const done = session.status !== 'open'
  return (
    <div className="stack">
      <section className="card socratic" aria-labelledby="socratic-heading">
        {heading}
        <p className="muted small">
          About <strong>{session.title || session.topic}</strong> · {session.exchanges} exchange{session.exchanges === 1 ? '' : 's'}
          {lastTutorLine?.probe ? ` · ${PROBE_LABEL[lastTutorLine.probe] ?? lastTutorLine.probe}` : null}
        </p>
        <ol className="socratic-dialogue">
          {session.transcript.map((turn, index) => (
            <li key={`${index}-${turn.role}`} className={`socratic-turn socratic-${turn.role}`}>
              <span className="socratic-who">{turn.role === 'tutor' ? 'Tutor' : 'You'}</span>
              <p className="body">{turn.text}</p>
            </li>
          ))}
        </ol>
        {session.waiting ? (
          <p className="muted socratic-waiting" role="status">
            <span className="spinner" aria-hidden="true" /> Your Mac is writing the {session.transcript.length === 0 ? 'first' : 'next'} question…
          </p>
        ) : null}
        {!session.waiting && session.relay_error ? (
          <div className="failure" role="alert">
            <p>Your Mac could not write the next question ({session.relay_error}). Your answer is kept.</p>
            <button type="button" className="button small" disabled={busy} onClick={() => void act(() => api.socraticAnswer(session.id, ''))}>
              Ask again
            </button>
          </div>
        ) : null}
        {!done && (overview.can_answer_here || relayAvailable) ? (
          <>
            <label className="field">
              <span>Your answer</span>
              <textarea
                name="socratic-answer"
                rows={5}
                value={answer}
                maxLength={8000}
                placeholder={listening ? 'Listening…' : 'Answer in your own words, or dictate.'}
                onChange={(event) => setAnswer(event.target.value)}
              />
            </label>
            <PhiWarning />
            <div className="actions">
              <button type="button" className="button primary" disabled={busy || session.waiting || answer.trim() === ''} onClick={() => void act(() => api.socraticAnswer(session.id, answer))}>
                {busy ? 'Thinking…' : 'Answer'}
              </button>
              {canDictate() ? (
                <button type="button" className={`button${listening ? ' primary' : ''}`} disabled={busy} onClick={toggleListening}>
                  {listening ? 'Stop listening' : 'Dictate'}
                </button>
              ) : null}
              {canSpeak() ? (
                <button
                  type="button"
                  className={`button${voiceOn ? ' primary' : ''}`}
                  onClick={() => {
                    if (voiceOn) {
                      stopSpeaking.current()
                      stopListening.current()
                    }
                    setVoiceOn((on) => !on)
                  }}
                >
                  {voiceOn ? 'Voice mode on' : 'Voice mode'}
                </button>
              ) : null}
              <button type="button" className="button ghost" disabled={busy} onClick={() => void act(() => api.socraticAbandon(session.id))}>
                End session
              </button>
            </div>
          </>
        ) : null}
        {!done && !overview.can_answer_here ? <p className="muted small">{overview.note}</p> : null}
        {note && !session.waiting ? (
          <p className="ok" role="status">
            {note}
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message} Your answer is still here.
          </p>
        ) : null}
        {done ? (
          <div className="actions">
            {!session.assessed && session.status === 'done' && overview.can_answer_here ? (
              <button type="button" className="button primary" disabled={busy} onClick={() => void act(() => api.socraticAssess(session.id))}>
                {busy ? 'Assessing…' : 'Assess'}
              </button>
            ) : null}
            {overview.can_answer_here ? (
              <button type="button" className="button" disabled={busy} onClick={() => void start()}>
                Start another
              </button>
            ) : null}
            <button type="button" className="button ghost" onClick={() => { setSession(null); reload() }}>
              Back
            </button>
            <a href="/map" className="button ghost" onClick={go('map')}>
              Improvement Map
            </a>
          </div>
        ) : null}
      </section>
      {done && session.assessed ? <Assessment session={session} /> : null}
    </div>
  )
}
