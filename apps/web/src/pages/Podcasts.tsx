/**
 * The Podcast tab (ADR 0025): a two-host script from encyclopedia pages,
 * rendered to audio on the Mac with its own voices, or read aloud here by the
 * browser. Writing sends the pages once, and the disclosure says so; rendering
 * sends nothing. An episode played or read to the end drops to the archive.
 */

import { useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { PaperLink } from '../components/PaperLink'
import { Unavailable } from '../components/Unavailable'
import { API_ROOT, ApiError, api, asApiError } from '../lib/api'
import { byteLabel, momentLabel } from '../lib/format'
import type { RouteName } from '../lib/router'
import { canSpeak, speak } from '../lib/speech'
import type { EncyclopediaEntry, PodcastEpisode, PodcastVoice } from '../lib/types'
import { useLoad } from '../lib/useLoad'

const STATUS_LABEL: Record<PodcastEpisode['status'], string> = {
  draft: 'writing the script',
  scripted: 'script ready',
  rendered: 'audio ready',
  failed: 'could not be written'
}

function durationLabel(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return minutes > 0 ? `${minutes} min ${rest} s` : `${rest} s`
}

function Script({ episode, onFinished }: { episode: PodcastEpisode; onFinished: () => void }) {
  const [reading, setReading] = useState(false)
  const stop = useRef<() => void>(() => undefined)
  useEffect(() => () => stop.current(), [])

  const readAloud = () => {
    if (reading) {
      stop.current()
      setReading(false)
      return
    }
    const lines = episode.script.map((line) => line.text)
    let index = 0
    setReading(true)
    const next = () => {
      const line = lines[index]
      if (line === undefined) {
        setReading(false)
        onFinished()
        return
      }
      stop.current = speak(line, { onEnd: next })
      index += 1
    }
    next()
  }

  return (
    <div className="podcast-script">
      {episode.takeaways.length > 0 ? (
        <>
          <h4>Take-homes</h4>
          <ul className="list small">
            {episode.takeaways.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      ) : null}
      {canSpeak() && episode.script.length > 0 ? (
        <div className="actions">
          <button type="button" className="button" onClick={readAloud}>
            {reading ? 'Stop reading' : 'Read aloud in this browser'}
          </button>
        </div>
      ) : null}
      <details className="support-details">
        <summary>The script ({episode.words} words)</summary>
        <ol className="podcast-lines">
          {episode.script.map((line, index) => (
            <li key={index} className={`podcast-line speaker-${line.speaker}`}>
              <span className="podcast-speaker">{line.speaker === 'A' ? 'Host A' : 'Host B'}</span>
              <p className="body">{line.text}</p>
            </li>
          ))}
        </ol>
      </details>
    </div>
  )
}

function EpisodeCard({ episode, voices, canRender, onChanged }: { episode: PodcastEpisode; voices: PodcastVoice[]; canRender: boolean; onChanged: () => void }) {
  const [voiceA, setVoiceA] = useState(episode.voices.A || 'Samantha')
  const [voiceB, setVoiceB] = useState(episode.voices.B || 'Daniel')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  // Listened to the end, by the player or read aloud: to the archive (ADR 0026).
  const finished = () => {
    if (!episode.archived) void act(() => api.markPodcastListened(episode.id, true))
  }

  const names = voices.map((voice) => voice.name)
  return (
    <li className="podcast-episode">
      <p className="title">{episode.title || 'Untitled episode'}</p>
      <p className="muted small">
        {STATUS_LABEL[episode.status]} · {episode.entry_ids.length} page{episode.entry_ids.length === 1 ? '' : 's'} · {momentLabel(episode.created_at)}
        {episode.has_audio ? ` · ${durationLabel(episode.duration_seconds)} · ${byteLabel(episode.audio_bytes)}` : null}
      </p>
      {episode.status_detail ? <p className="warn small">{episode.status_detail}</p> : null}
      {episode.has_audio ? (
        <audio controls preload="none" src={`${API_ROOT}/podcasts/${episode.id}/audio`} className="podcast-audio" onEnded={finished}>
          <a href={`${API_ROOT}/podcasts/${episode.id}/audio`}>Download the audio</a>
        </audio>
      ) : null}
      {episode.script.length > 0 ? <Script episode={episode} onFinished={finished} /> : null}
      {episode.sources.length > 0 ? (
        <details className="support-details">
          <summary>Sources ({episode.sources.length})</summary>
          <ul className="list small">
            {episode.sources.map((source, index) => (
              <li key={`${source.kind}-${index}`}>
                {source.kind === 'paper' && source.pmid ? <PaperLink pmid={source.pmid} title={source.title} /> : <span>{source.title}</span>}
                <span className="muted small">
                  {source.kind === 'page' ? ' · encyclopedia page' : ` · ${[source.journal, source.year].filter(Boolean).join(', ')}`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {episode.script.length > 0 && canRender ? (
        <div className="podcast-voices">
          <label htmlFor={`voice-a-${episode.id}`}>Host A voice</label>
          <select id={`voice-a-${episode.id}`} value={voiceA} disabled={busy} onChange={(event) => setVoiceA(event.target.value)}>
            {(names.includes(voiceA) ? names : [voiceA, ...names]).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <label htmlFor={`voice-b-${episode.id}`}>Host B voice</label>
          <select id={`voice-b-${episode.id}`} value={voiceB} disabled={busy} onChange={(event) => setVoiceB(event.target.value)}>
            {(names.includes(voiceB) ? names : [voiceB, ...names]).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      <div className="actions">
        {episode.script.length > 0 && canRender ? (
          <button type="button" className="button primary" disabled={busy} onClick={() => void act(() => api.renderPodcast(episode.id, { voice_a: voiceA, voice_b: voiceB }))}>
            {busy ? 'Rendering…' : episode.has_audio ? 'Render again' : 'Render audio on this Mac'}
          </button>
        ) : null}
        {episode.status === 'failed' ? (
          <button type="button" className="button" disabled={busy} onClick={() => void act(() => api.rewritePodcast(episode.id))}>
            Write it again
          </button>
        ) : null}
        {episode.script.length > 0 ? (
          <button type="button" className="button small" disabled={busy} onClick={() => void act(() => api.markPodcastListened(episode.id, !episode.archived))}>
            {episode.archived ? 'Back to episodes' : 'Mark listened'}
          </button>
        ) : null}
        <button type="button" className="button ghost small" disabled={busy} onClick={() => void act(() => api.deletePodcast(episode.id))}>
          Remove
        </button>
      </div>
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </li>
  )
}

export function Podcasts({ onNavigate }: { onNavigate?: (name: RouteName) => void }) {
  const { result, reload } = useLoad(() => api.podcasts(), [])
  const voices = useLoad(() => api.podcastVoices(), [])
  const pages = useLoad(() => api.encyclopediaList(), [])
  const [pick, setPick] = useState<'today' | 'improvement' | 'chosen'>('improvement')
  const [chosen, setChosen] = useState<string[]>([])
  const [title, setTitle] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [note, setNote] = useState('')

  useEffect(() => {
    if (result.state !== 'ready' || !result.value.episodes.some((episode) => episode.status === 'draft')) return undefined
    const timer = window.setInterval(reload, 10000)
    return () => window.clearInterval(timer)
  }, [result, reload])

  const go = (name: RouteName) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    onNavigate(name)
  }

  const create = async () => {
    setBusy(true)
    setFailure(null)
    setNote('')
    try {
      const episode = await api.createPodcast({ pick, entry_ids: pick === 'chosen' ? chosen : [], title: title.trim() || undefined })
      setNote(episode.note ?? 'Writing the script now.')
      setTitle('')
      reload()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  if (result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />
  const list = result.value
  const voiceList = voices.result.state === 'ready' ? voices.result.value.voices : []
  const pageList: EncyclopediaEntry[] = pages.result.state === 'ready' ? pages.result.value.entries : []
  const current = list.episodes.filter((episode) => !episode.archived)
  const archive = list.episodes
    .filter((episode) => episode.archived)
    .sort((a, b) => (b.listened_at ?? '').localeCompare(a.listened_at ?? ''))

  return (
    <div className="stack">
      <section className="card" aria-labelledby="podcasts-heading">
        <h2 id="podcasts-heading">Podcast</h2>
        <p className="muted">
          A two-host episode grounded in your encyclopedia, expanding from there with the literature reviewed for each page and the
          hosts’ own knowledge, with sources cited as they go and take-homes at the end.
        </p>
        {list.can_write ? (
          <>
            <p className="muted small">{list.disclosure}</p>
            <fieldset className="podcast-pick">
              <legend>Pages for this episode</legend>
              <label className="tab-option">
                <input type="radio" name="pick" checked={pick === 'improvement'} onChange={() => setPick('improvement')} /> From my improvement areas
              </label>
              <label className="tab-option">
                <input type="radio" name="pick" checked={pick === 'today'} onChange={() => setPick('today')} /> Today’s page
              </label>
              <label className="tab-option">
                <input type="radio" name="pick" checked={pick === 'chosen'} onChange={() => setPick('chosen')} /> Pages I choose (up to six)
              </label>
            </fieldset>
            {pick === 'chosen' ? (
              <fieldset className="podcast-pages">
                <legend className="visually-hidden">Pages</legend>
                {pageList.length === 0 ? (
                  <p className="muted small">
                    No pages yet.{' '}
                    <a href="/encyclopedia" onClick={go('encyclopedia')}>
                      Compile the encyclopedia
                    </a>{' '}
                    first.
                  </p>
                ) : null}
                {pageList.map((entry) => (
                  <label key={entry.id} className="tab-option">
                    <input
                      type="checkbox"
                      checked={chosen.includes(entry.id)}
                      disabled={!chosen.includes(entry.id) && chosen.length >= 6}
                      onChange={(event) => setChosen(event.target.checked ? [...chosen, entry.id] : chosen.filter((id) => id !== entry.id))}
                    />{' '}
                    {entry.title}
                  </label>
                ))}
              </fieldset>
            ) : null}
            <label htmlFor="podcast-title">Title (optional)</label>
            <input id="podcast-title" type="text" value={title} maxLength={120} disabled={busy} onChange={(event) => setTitle(event.target.value)} />
            <div className="actions">
              <button type="button" className="button primary" disabled={busy || (pick === 'chosen' && chosen.length === 0)} onClick={() => void create()}>
                {busy ? 'Working…' : 'Write an episode'}
              </button>
            </div>
          </>
        ) : (
          <p className="muted small">{list.note}</p>
        )}
        {note ? (
          <p className="ok" role="status">
            {note}
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>

      <section className="card" aria-labelledby="episodes-heading">
        <h2 id="episodes-heading">Episodes</h2>
        {current.length === 0 ? (
          <p className="muted">{archive.length > 0 ? 'Nothing new to listen to. Finished episodes are in the archive.' : 'No episodes yet.'}</p>
        ) : null}
        {current.length > 0 ? (
          <ul className="list">
            {current.map((episode) => (
              <EpisodeCard key={episode.id} episode={episode} voices={voiceList} canRender={list.can_render} onChanged={reload} />
            ))}
          </ul>
        ) : null}
        {list.can_render ? (
          <p className="muted small">
            Voices are the Mac’s own. More, and better ones, can be added in System Settings under Accessibility, Spoken Content.
          </p>
        ) : null}
      </section>

      {archive.length > 0 ? (
        <details className="card toggle-card">
          <summary>
            <h2>Archive ({archive.length})</h2>
          </summary>
          <p className="muted small">Episodes you have listened to, most recent first.</p>
          <ul className="list">
            {archive.map((episode) => (
              <EpisodeCard key={episode.id} episode={episode} voices={voiceList} canRender={list.can_render} onChanged={reload} />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  )
}
