/**
 * The Encyclopedia (ADR 0023): every page compiled from your sources, one per
 * topic, shelved by subject under a table of contents, with a search, a compile
 * card that says what compiling sends, and the page itself when you open one.
 */

import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { EncyclopediaPage } from '../components/EncyclopediaPage'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { EncyclopediaEntry, EncyclopediaList, Specialty } from '../lib/types'
import { takePendingPage } from '../lib/pageLink'
import { useLoad } from '../lib/useLoad'

export function CompileCard({ state, onChanged }: { state: EncyclopediaList; onChanged: () => void }) {
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

const PHASE_LABEL: Record<string, string> = {
  starting: 'starting',
  building: 'building the next batch',
  compiling: 'compiling pages, reviewing the literature, writing questions',
  waiting: 'waiting for a build already running',
  backing_off: 'paused after a failure; it will try again',
  complete: 'complete: the pile is built and every page is current. Still watching for new files.',
  stopped: 'stopped'
}

export function DissectionCard({ onChanged }: { onChanged: () => void }) {
  const { result, reload } = useLoad(() => api.dissection(), [])
  const piles = useLoad(() => api.listPiles(), [])
  const [pileId, setPileId] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  useEffect(() => {
    if (result.state !== 'ready' || !result.value.running) return undefined
    const timer = window.setInterval(reload, 15000)
    return () => window.clearInterval(timer)
  }, [result, reload])

  if (result.state !== 'ready') return null
  const state = result.value
  const chosen = pileId || state.pile_id || 'all'

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      reload()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="dissection-heading">
      <h2 id="dissection-heading">Dissect a pile</h2>
      {!state.can_run ? (
        <p className="muted small">Building happens on your Mac; this copy shows what it has built.</p>
      ) : (
        <>
          <p className="muted small">{state.disclosure}</p>
          {state.status === 'running' ? (
            <>
              <p className="body">
                Working through <strong>{state.pile_title}</strong>: {PHASE_LABEL[state.phase] ?? state.phase}.
              </p>
              <p className="muted small">
                {state.batches_done} batch{state.batches_done === 1 ? '' : 'es'} built · {state.points_built} points ·{' '}
                {state.pages_compiled} page{state.pages_compiled === 1 ? '' : 's'} compiled · {state.questions_written} question
                {state.questions_written === 1 ? '' : 's'} written
                {state.coverage ? ` · ${state.coverage.percent}% of the pile’s text processed` : null}
                {state.consent_at ? ` · consent given ${momentLabel(state.consent_at)}` : null}
              </p>
              {state.last_error ? (
                <p className="warn small">
                  {state.last_error}
                  {state.next_retry_at ? ` Next try ${momentLabel(state.next_retry_at)}.` : null}
                </p>
              ) : null}
              <div className="actions">
                <button type="button" className="button" disabled={busy} onClick={() => void act(() => api.stopDissection())}>
                  Stop the agent
                </button>
              </div>
            </>
          ) : (
            <>
              {state.status === 'stopped' ? (
                <p className="muted small">
                  Stopped on <strong>{state.pile_title}</strong> after {state.batches_done} batch{state.batches_done === 1 ? '' : 'es'}. Starting again resumes it.
                </p>
              ) : null}
              <label htmlFor="dissect-pile">Pile</label>
              <select id="dissect-pile" value={chosen} disabled={busy} onChange={(event) => setPileId(event.target.value)}>
                <option value="all">Every pile, whatever is dropped in</option>
                {piles.result.state === 'ready'
                  ? piles.result.value.map((pile) => (
                      <option key={pile.id} value={pile.id}>
                        {pile.title}
                      </option>
                    ))
                  : null}
              </select>
              <div className="actions">
                <button type="button" className="button primary" disabled={busy || !chosen} onClick={() => void act(() => api.startDissection(chosen))}>
                  Dissect this pile
                </button>
              </div>
            </>
          )}
        </>
      )}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}

/**
 * A page, and on the Mac its editor (ADR 0026). The edit is Markdown, kept beside
 * the compiled text and written to the page's file in the source folder, where
 * any editor can change it too; the next scan reads that change back.
 */
function EditablePage({ page, onSaved, canEdit }: { page: EncyclopediaEntry; onSaved: (page: EncyclopediaEntry) => void; canEdit: boolean }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(page.markdown)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const act = async (action: () => Promise<EncyclopediaEntry>) => {
    setBusy(true)
    setFailure(null)
    try {
      const next = await action()
      onSaved(next)
      setDraft(next.markdown)
      setEditing(false)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {editing ? (
        <div className="page-editor">
          <label className="field">
            <span>Edit this page (Markdown)</span>
            <textarea rows={24} value={draft} disabled={busy} spellCheck onChange={(event) => setDraft(event.target.value)} />
          </label>
          <p className="muted small">
            # heading, ## section, a blank line between paragraphs, - for a list, **bold**, *italic*. Saved on this Mac and to the page’s file in
            your source folder under encyclopedia/.
          </p>
          <div className="actions">
            <button type="button" className="button primary" disabled={busy} onClick={() => void act(() => api.editPage(page.id, draft))}>
              {busy ? 'Saving…' : 'Save'}
            </button>
            <button type="button" className="button ghost" disabled={busy} onClick={() => { setDraft(page.markdown); setEditing(false) }}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <EncyclopediaPage page={page} />
          {/* Edited on the Mac, whose copy is the page's: the cloud copy shows the edit once it syncs. */}
          {canEdit ? <div className="actions">
            <button type="button" className="button" onClick={() => { setDraft(page.markdown); setEditing(true) }}>
              Edit
            </button>
            {page.edited ? (
              <button type="button" className="button ghost" disabled={busy} onClick={() => void act(() => api.revertPage(page.id))}>
                Go back to the compiled page
              </button>
            ) : null}
          </div> : null}
        </>
      )}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </>
  )
}

export interface Subject {
  id: string
  name: string
  entries: EncyclopediaEntry[]
}

const NO_SUBJECT = 'other'

/** Pages under their subject, in the subjects' own order; a page with none comes last. */
export function pagesBySubject(entries: EncyclopediaEntry[], specialties: Specialty[]): Subject[] {
  const subjects = new Map<string, Subject>(specialties.map((entry) => [entry.id, { ...entry, entries: [] }]))
  const other: Subject = { id: NO_SUBJECT, name: 'Other topics', entries: [] }
  for (const entry of entries) (subjects.get(entry.specialty_id ?? '') ?? other).entries.push(entry)
  return [...subjects.values(), other].filter((subject) => subject.entries.length > 0)
}

function jumpTo(id: string) {
  // Scrolled by hand: inside the conversation the app has no address bar for a fragment to land in.
  const target = document.getElementById(id)
  if (target instanceof HTMLDetailsElement) target.open = true
  target?.scrollIntoView?.({ block: 'start' })
}

export function Encyclopedia() {
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [reloadToken] = useState(0)
  const [open, setOpen] = useState<EncyclopediaEntry | null>(null)
  const [opening, setOpening] = useState<ApiError | null>(null)
  const { result, reload } = useLoad(() => api.encyclopediaList(q || undefined), [q, reloadToken])

  const search = (event: FormEvent) => {
    event.preventDefault()
    setQ(typed.trim())
  }

  useEffect(() => {
    const asked = takePendingPage()
    if (asked) void show(asked)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
          <EditablePage page={open} onSaved={setOpen} canEdit={result.state === 'ready' && result.value.can_compile} />
        </section>
      </div>
    )
  }

  const subjects = result.state === 'ready' ? pagesBySubject(result.value.entries, result.value.specialties) : []
  const pageLink = (entry: EncyclopediaEntry) => (
    <a
      href={`/encyclopedia#${entry.id}`}
      onClick={(event) => {
        event.preventDefault()
        void show(entry.id)
      }}
    >
      {entry.title}
    </a>
  )

  return (
    <div className="stack">
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
        {subjects.length > 0 ? (
          <>
            <nav className="page-contents" aria-labelledby="contents-heading">
              <h3 id="contents-heading">Contents</h3>
              <ol>
                {subjects.map((subject) => (
                  <li key={subject.id}>
                    <a
                      href={`#subject-${subject.id}`}
                      onClick={(event) => {
                        event.preventDefault()
                        jumpTo(`subject-${subject.id}`)
                      }}
                    >
                      {subject.name}
                    </a>
                    <ul>
                      {subject.entries.map((entry) => (
                        <li key={entry.id}>{pageLink(entry)}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            </nav>
            {subjects.map((subject) => (
              <details key={subject.id} id={`subject-${subject.id}`} className="page-subject" open={subjects.length <= 2 || q !== ''}>
                <summary>
                  <span className="page-subject-name">{subject.name}</span>{' '}
                  <span className="muted small">
                    {subject.entries.length} page{subject.entries.length === 1 ? '' : 's'}
                  </span>
                </summary>
                <ul className="list page-list">
                  {subject.entries.map((entry) => (
                    <li key={entry.id}>
                      <p className="title">{pageLink(entry)}</p>
                      <p className="muted small">
                        {entry.point_count} point{entry.point_count === 1 ? '' : 's'} · {entry.question_count} question{entry.question_count === 1 ? '' : 's'}
                        {entry.compiled_at ? ` · compiled ${momentLabel(entry.compiled_at)}` : null}
                      </p>
                      {entry.summary ? <p className="body small">{entry.summary}</p> : null}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
          </>
        ) : null}
      </section>
    </div>
  )
}
