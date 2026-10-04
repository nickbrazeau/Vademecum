/**
 * The Socratic tutor on the Mac (ADR 0025): one open question at a time, in
 * text, with the browser's dictation and speech where it offers them. Each
 * answer is one model turn on your own sign-in, and the disclosure says so.
 * In a chat host the assistant is the tutor instead, and this card says how.
 */

import { useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { RouteName } from '../lib/router'
import { canDictate, canSpeak, dictate, speak } from '../lib/speech'
import type { SocraticOverview, SocraticSession } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { PhiWarning } from './PhiWarning'

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
  const stopListening = useRef<() => void>(() => undefined)
  const stopSpeaking = useRef<() => void>(() => undefined)

  useEffect(() => {
    if (result.state === 'ready') setSession(result.value.open)
  }, [result])

  useEffect(() => () => {
    stopListening.current()
    stopSpeaking.current()
  }, [])

  const lastTutorLine = session?.transcript.filter((turn) => turn.role === 'tutor').at(-1)
  useEffect(() => {
    if (voiceOn && lastTutorLine) stopSpeaking.current = speak(lastTutorLine.text)
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
    setListening(true)
    stopListening.current = dictate(
      (text) => setAnswer(text),
      () => setListening(false)
    )
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
            </div>
          </>
        ) : (
          <p className="muted small">{overview.note}</p>
        )}
        {overview.recent.length > 0 ? (
          <details className="support-details">
            <summary>Past sessions</summary>
            <ul className="list small">
              {overview.recent.map((past) => (
                <li key={past.id}>
                  <span className="title">{past.title || past.topic}</span>
                  <span className="muted small">
                    {' '}
                    · {past.exchanges} exchange{past.exchanges === 1 ? '' : 's'} · {past.status}
                  </span>
                  {past.assessment.summary ? <p className="muted small">{past.assessment.summary}</p> : null}
                </li>
              ))}
            </ul>
          </details>
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
        {!done && overview.can_answer_here ? (
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
              <button type="button" className="button primary" disabled={busy || answer.trim() === ''} onClick={() => void act(() => api.socraticAnswer(session.id, answer))}>
                {busy ? 'Thinking…' : 'Answer'}
              </button>
              {canDictate() ? (
                <button type="button" className={`button${listening ? ' primary' : ''}`} disabled={busy} onClick={toggleListening}>
                  {listening ? 'Stop listening' : 'Dictate'}
                </button>
              ) : null}
              {canSpeak() ? (
                <button type="button" className="button ghost" onClick={() => setVoiceOn((on) => !on)}>
                  {voiceOn ? 'Stop reading aloud' : 'Read questions aloud'}
                </button>
              ) : null}
              <button type="button" className="button ghost" disabled={busy} onClick={() => void act(() => api.socraticAbandon(session.id))}>
                End session
              </button>
            </div>
          </>
        ) : null}
        {!done && !overview.can_answer_here ? <p className="muted small">{overview.note}</p> : null}
        {note ? (
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
            <button type="button" className="button primary" disabled={busy} onClick={() => void start()}>
              Start another
            </button>
            <button type="button" className="button ghost" onClick={() => { setSession(null); reload() }}>
              Back
            </button>
            <a href="/map" className="button ghost" onClick={go('map')}>
              Improvement Map
            </a>
          </div>
        ) : null}
      </section>
      {done && session.status === 'done' ? <Assessment session={session} /> : null}
    </div>
  )
}
