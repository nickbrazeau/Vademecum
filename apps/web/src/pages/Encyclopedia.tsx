/**
 * The Encyclopedia (ADR 0023): every page compiled from your sources, one per
 * topic, with a search, a compile card that says what compiling sends, and the
 * page itself when you open one.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { EncyclopediaPage } from '../components/EncyclopediaPage'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { EncyclopediaEntry, EncyclopediaList } from '../lib/types'
import { useLoad } from '../lib/useLoad'

function CompileCard({ state, onChanged }: { state: EncyclopediaList; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const compile = async () => {
    setBusy(true)
    setFailure(null)
    try {
      await api.compileEncyclopedia()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const last = state.last_refresh
  return (
    <section className="card" aria-labelledby="compile-heading">
      <h2 id="compile-heading">Compiling</h2>
      <p className="muted small">
        {state.counts.entries} page{state.counts.entries === 1 ? '' : 's'}
        {state.counts.stale > 0 ? ` · ${state.counts.stale} topic${state.counts.stale === 1 ? '' : 's'} waiting to be compiled or recompiled` : ' · every topic is current'}
        {' · '}
        {state.counts.questions_eligible} board question{state.counts.questions_eligible === 1 ? '' : 's'} ready
        {state.counts.questions_held > 0 ? `, ${state.counts.questions_held} held` : ''}.
      </p>
      {state.can_compile ? (
        <>
          <p className="muted small">{state.disclosure}</p>
          <p className="muted small">
            Pages are compiled after each scheduled build run as well.{' '}
            {last
              ? `Last compile ${momentLabel(last.at)}: ${last.pages.compiled} page${last.pages.compiled === 1 ? '' : 's'} written, ${last.questions.written} question${last.questions.written === 1 ? '' : 's'} added${last.questions.held ? `, ${last.questions.held} held` : ''}${last.pages.failed ? `, ${last.pages.failed} topic${last.pages.failed === 1 ? '' : 's'} failed` : ''}.`
              : 'Not compiled yet.'}
          </p>
          <div className="actions">
            <button type="button" className="button primary" disabled={busy || state.running} onClick={() => void compile()}>
              {state.running ? 'Compiling…' : 'Compile now'}
            </button>
          </div>
        </>
      ) : (
        <p className="muted small">{state.note}</p>
      )}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}

export function Encyclopedia() {
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const [open, setOpen] = useState<EncyclopediaEntry | null>(null)
  const [opening, setOpening] = useState<ApiError | null>(null)
  const { result, reload } = useLoad(() => api.encyclopediaList(q || undefined), [q, reloadToken])

  const search = (event: FormEvent) => {
    event.preventDefault()
    setQ(typed.trim())
  }

  const show = async (entryId: string) => {
    setOpening(null)
    try {
      setOpen(await api.encyclopediaEntry(entryId))
      try {
        window.scrollTo({ top: 0 })
      } catch {
        /* a frame without scrolling, or a test document */
      }
    } catch (error) {
      setOpening(asApiError(error))
    }
  }

  if (open !== null) {
    return (
      <div className="stack">
        <section className="card">
          <div className="actions">
            <button type="button" className="button ghost" onClick={() => setOpen(null)}>
              ← All pages
            </button>
          </div>
          <EncyclopediaPage page={open} />
        </section>
      </div>
    )
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="encyclopedia-heading">
        <h2 id="encyclopedia-heading">Encyclopedia</h2>
        <p className="muted">
          Your sources, compiled: one page per topic, written from the learning points a Build made,
          with every paragraph naming the points and sources it rests on. Today shows one page to
          review each day, and the Tutor’s board questions are written from these pages.
        </p>
      </section>

      {result.state === 'ready' ? <CompileCard state={result.value} onChanged={() => setReloadToken((value) => value + 1)} /> : null}

      <section className="card" aria-labelledby="pages-heading">
        <h2 id="pages-heading">Pages</h2>
        <form className="case-search" onSubmit={search}>
          <label htmlFor="page-search">Search pages</label>
          <input id="page-search" type="search" value={typed} onChange={(event) => setTyped(event.target.value)} />
          <div className="actions">
            <button type="submit" className="button">
              Search
            </button>
            {q ? (
              <button
                type="button"
                className="button ghost"
                onClick={() => {
                  setTyped('')
                  setQ('')
                }}
              >
                Clear
              </button>
            ) : null}
          </div>
        </form>
        {result.state === 'loading' ? <p className="muted">Reading from this Mac…</p> : null}
        {result.state === 'failed' ? <Unavailable error={result.error} onRetry={reload} /> : null}
        {opening ? (
          <p className="failure" role="alert">
            {opening.message}
          </p>
        ) : null}
        {result.state === 'ready' && result.value.entries.length === 0 ? (
          <p className="muted">
            {result.value.counts.entries === 0
              ? 'No pages yet. Build learning material in Sources, then compile: pages are written from what a Build made.'
              : 'No page matches that search.'}
          </p>
        ) : null}
        {result.state === 'ready' && result.value.entries.length > 0 ? (
          <ul className="list page-list">
            {result.value.entries.map((entry) => (
              <li key={entry.id}>
                <p className="title">
                  <a
                    href={`/encyclopedia#${entry.id}`}
                    onClick={(event) => {
                      event.preventDefault()
                      void show(entry.id)
                    }}
                  >
                    {entry.title}
                  </a>
                </p>
                <p className="muted small">
                  {entry.point_count} point{entry.point_count === 1 ? '' : 's'} · {entry.question_count} question{entry.question_count === 1 ? '' : 's'}
                  {entry.compiled_at ? ` · compiled ${momentLabel(entry.compiled_at)}` : null}
                </p>
                {entry.summary ? <p className="body small">{entry.summary}</p> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  )
}
