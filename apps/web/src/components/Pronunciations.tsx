/**
 * How the podcast says medical words (feedback of 10 October). Medical words espeak gets
 * wrong are said from a built-in list; here the owner adds their own: the word and a
 * sound-alike spelling, heard before it is kept. On the Mac, where the voices are.
 */

import { useEffect, useRef, useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { useLoad } from '../lib/useLoad'

export function Pronunciations() {
  const { result, reload } = useLoad(() => api.pronunciations(), [])
  const [word, setWord] = useState('')
  const [said, setSaid] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const sound = useRef<string | null>(null)
  useEffect(() => () => {
    if (sound.current) URL.revokeObjectURL(sound.current)
  }, [])

  if (result.state !== 'ready' || !result.value.can_hear) return null
  const yours = result.value.yours

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }
  const hear = (w: string, s: string) =>
    run(async () => {
      const blob = await api.hearPronunciation(w, s)
      if (sound.current) URL.revokeObjectURL(sound.current)
      sound.current = URL.createObjectURL(blob)
      await new Audio(sound.current).play()
    })
  const save = (entries: Record<string, string>) =>
    run(async () => {
      await api.savePronunciations(entries)
      reload()
    })

  return (
    <section className="card" aria-labelledby="pronunciations-heading">
      <h2 id="pronunciations-heading">How the podcast says medical words</h2>
      <p className="muted small">
        {result.value.built_in.length} medical words are said from a built-in list. Add a word it still gets wrong, with a spelling that sounds
        right (for example “lye SIN oh pril”), and hear it before you keep it. New episodes use it; an episode already made can be voiced again.
      </p>
      {Object.keys(yours).length > 0 ? (
        <ul className="list small">
          {Object.entries(yours).map(([w, s]) => (
            <li key={w}>
              <strong>{w}</strong> <span className="muted">said “{s}”</span>{' '}
              <button type="button" className="button ghost small" disabled={busy} onClick={() => void hear(w, s)}>
                Hear it
              </button>
              <button
                type="button"
                className="button ghost small"
                disabled={busy}
                onClick={() => {
                  const next = { ...yours }
                  delete next[w]
                  void save(next)
                }}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="pronunciation-add">
        <label className="field">
          <span>Word</span>
          <input type="text" value={word} maxLength={60} onChange={(event) => setWord(event.target.value)} />
        </label>
        <label className="field">
          <span>Said like</span>
          <input type="text" value={said} maxLength={120} placeholder="fyoo ROH seh mide" onChange={(event) => setSaid(event.target.value)} />
        </label>
        <div className="actions">
          <button type="button" className="button small" disabled={busy || !word.trim()} onClick={() => void hear(word.trim(), said.trim())}>
            Hear it
          </button>
          <button
            type="button"
            className="button primary small"
            disabled={busy || !word.trim() || !said.trim()}
            onClick={() => {
              void save({ ...yours, [word.trim()]: said.trim() })
              setWord('')
              setSaid('')
            }}
          >
            Keep it
          </button>
        </div>
      </div>
      {failure ? (
        <p className="failure small" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}
