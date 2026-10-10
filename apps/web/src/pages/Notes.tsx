/**
 * Notes (ADR 0032, feedback of 10 October): the owner's own notebooks, each holding notes
 * that can hold notes of their own, written in Markdown. The same on the Mac and the
 * phone; on the Mac each note is also a Markdown file in the source folder, and a note
 * marked "Use as a source" is built into pages, questions and cards.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import { Loading } from '../components/Loading'
import { Markdown } from '../components/Markdown'
import { Switch } from '../components/Switch'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { clearDraft, loadDraft, noteDraftKey, saveDraft } from '../lib/drafts'
import type { Note, NoteSummary } from '../lib/types'
import { useLoad } from '../lib/useLoad'

type Tree = Map<string | null, NoteSummary[]>

function grow(notes: NoteSummary[]): Tree {
  const tree: Tree = new Map()
  for (const entry of notes) {
    const siblings = tree.get(entry.parent_id) ?? []
    siblings.push(entry)
    tree.set(entry.parent_id, siblings)
  }
  for (const siblings of tree.values()) siblings.sort((a, b) => a.position - b.position || a.title.localeCompare(b.title))
  return tree
}

/** Everything a note could move under: anything but itself and what is inside it. */
function destinations(notes: NoteSummary[], tree: Tree, moving: string): NoteSummary[] {
  const inside = new Set<string>([moving])
  const stack = [moving]
  while (stack.length) {
    for (const child of tree.get(stack.pop()!) ?? []) {
      inside.add(child.id)
      stack.push(child.id)
    }
  }
  return notes.filter((entry) => !inside.has(entry.id))
}

function Branch({
  parent,
  tree,
  open,
  onToggle,
  selected,
  onSelect,
  depth
}: {
  parent: string | null
  tree: Tree
  open: ReadonlySet<string>
  onToggle: (id: string) => void
  selected: string | null
  onSelect: (id: string) => void
  depth: number
}) {
  const children = tree.get(parent) ?? []
  if (children.length === 0) return null
  return (
    <ul className="note-tree" role={depth === 0 ? 'tree' : 'group'} aria-label={depth === 0 ? 'Notebooks and notes' : undefined}>
      {children.map((entry) => {
        const hasChildren = (tree.get(entry.id) ?? []).length > 0
        const expanded = open.has(entry.id)
        return (
          <li key={entry.id} role="treeitem" aria-expanded={hasChildren ? expanded : undefined} aria-selected={selected === entry.id}>
            <div className={`note-row${selected === entry.id ? ' selected' : ''}`} style={{ paddingLeft: `${depth * 0.9}rem` }}>
              {hasChildren ? (
                <button type="button" className="note-twisty" aria-label={`${expanded ? 'Close' : 'Open'} ${entry.title}`} onClick={() => onToggle(entry.id)}>
                  {expanded ? '▾' : '▸'}
                </button>
              ) : (
                <span className="note-twisty" aria-hidden="true" />
              )}
              <button type="button" className="note-name" onClick={() => onSelect(entry.id)}>
                <span aria-hidden="true">{entry.notebook ? '📓 ' : ''}</span>
                {entry.title}
                {entry.use_as_source ? <span className="muted small"> · a source</span> : null}
              </button>
            </div>
            {hasChildren && expanded ? (
              <Branch parent={entry.id} tree={tree} open={open} onToggle={onToggle} selected={selected} onSelect={onSelect} depth={depth + 1} />
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function Editor({
  noteId,
  notes,
  tree,
  onChanged,
  onDeleted
}: {
  noteId: string
  notes: NoteSummary[]
  tree: Tree
  onChanged: (note: Note) => void
  onDeleted: () => void
}) {
  const { result } = useLoad(() => api.note(noteId), [noteId])
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [preview, setPreview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(true)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const loaded = useRef<string | null>(null)

  useEffect(() => {
    if (result.state !== 'ready' || loaded.current === noteId) return
    loaded.current = noteId
    const draft = loadDraft(noteDraftKey(noteId))
    setTitle(result.value.title)
    setBody(draft || result.value.body_md)
    setSaved(!draft || draft === result.value.body_md)
    setPreview(!draft && result.value.body_md.trim() !== '')
  }, [result, noteId])

  if (result.state === 'loading') return <Loading />
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={() => undefined} />
  const note = result.value

  const act = async (action: () => Promise<Note | void>) => {
    setBusy(true)
    setFailure(null)
    try {
      const changed = await action()
      if (changed) onChanged(changed)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }
  const save = () =>
    act(async () => {
      const changed = await api.changeNote(note.id, { title, body_md: body })
      clearDraft(noteDraftKey(note.id))
      setSaved(true)
      return changed
    })
  const add = (notebook: boolean) =>
    act(async () => {
      const created = await api.createNote({ title: notebook ? 'New notebook' : 'New note', parent_id: note.id, notebook })
      onChanged(created)
      return undefined
    })
  const remove = () => {
    const inside = (tree.get(note.id) ?? []).length
    if (!window.confirm(`Delete “${note.title}”${inside ? ' and everything inside it' : ''}? This cannot be undone.`)) return
    void act(async () => {
      await api.deleteNote(note.id)
      clearDraft(noteDraftKey(note.id))
      onDeleted()
    })
  }

  return (
    <section className="card note-editor" aria-labelledby="note-heading">
      <p className="muted small">{note.path.slice(0, -1).join(' › ') || (note.notebook ? 'Notebook' : 'Note')}</p>
      <label className="field">
        <span className="visually-hidden">Title</span>
        <input
          id="note-heading"
          className="note-title"
          type="text"
          value={title}
          maxLength={200}
          onChange={(event) => {
            setTitle(event.target.value)
            setSaved(false)
          }}
        />
      </label>
      <div className="chips" role="group" aria-label="Write or read">
        <button type="button" className={`chip${preview ? '' : ' on'}`} aria-pressed={!preview} onClick={() => setPreview(false)}>
          Write
        </button>
        <button type="button" className={`chip${preview ? ' on' : ''}`} aria-pressed={preview} onClick={() => setPreview(true)}>
          Read
        </button>
      </div>
      {preview ? (
        <div className="note-preview">{body.trim() ? <Markdown text={body} /> : <p className="muted">Nothing written yet.</p>}</div>
      ) : (
        <label className="field">
          <span className="visually-hidden">Note, in Markdown</span>
          <textarea
            rows={14}
            value={body}
            maxLength={200000}
            placeholder={'Write in Markdown: # headings, **bold**, - lists, [links](address)'}
            onChange={(event) => {
              setBody(event.target.value)
              saveDraft(noteDraftKey(note.id), event.target.value)
              setSaved(false)
            }}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
                event.preventDefault()
                void save()
              }
            }}
          />
        </label>
      )}
      <div className="actions">
        <button type="button" className="button primary" disabled={busy || saved} onClick={() => void save()}>
          {saved ? 'Saved' : 'Save'}
          <kbd className="shortcut" aria-hidden="true">
            ⌘S
          </kbd>
        </button>
        <button type="button" className="button small" disabled={busy} onClick={() => void add(false)}>
          Add a note inside
        </button>
        {note.notebook ? (
          <button type="button" className="button small ghost" disabled={busy} onClick={() => void add(true)}>
            Add a notebook inside
          </button>
        ) : null}
      </div>
      <Switch
        label="Use as a source"
        checked={note.use_as_source}
        disabled={busy}
        onChange={(on) => void act(() => api.changeNote(note.id, { use_as_source: on }))}
        hint="built into pages, questions and cards as low-confidence material"
      />
      <details className="support-details">
        <summary>Move or delete</summary>
        <label className="field">
          <span>Move into</span>
          <select
            value={note.parent_id ?? ''}
            disabled={busy}
            onChange={(event) => void act(() => api.changeNote(note.id, { parent_id: event.target.value }))}
          >
            <option value="">The top level</option>
            {destinations(notes, tree, note.id).map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.title}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="button ghost danger" disabled={busy} onClick={remove}>
          Delete this {note.notebook ? 'notebook' : 'note'}
        </button>
      </details>
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}

export function Notes() {
  const [token, setToken] = useState(0)
  const { result, reload } = useLoad(() => api.notes(), [token])
  const [selected, setSelected] = useState<string | null>(null)
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  const [words, setWords] = useState('')
  const [found, setFound] = useState<NoteSummary[] | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const notes = result.state === 'ready' ? result.value : []
  const tree = useMemo(() => grow(notes), [notes])

  useEffect(() => {
    const query = words.trim()
    if (query.length < 2) {
      setFound(null)
      return undefined
    }
    let live = true
    const timer = window.setTimeout(() => {
      api.searchNotes(query).then(
        (hits) => live && setFound(hits),
        () => live && setFound([])
      )
    }, 250)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [words, token])

  const toggle = (id: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const changed = (note: Note | NoteSummary) => {
    // Show where it now is: every notebook above it opens.
    setOpen((current) => {
      const next = new Set(current)
      let parent = note.parent_id
      while (parent) {
        next.add(parent)
        parent = notes.find((entry) => entry.id === parent)?.parent_id ?? null
      }
      return next
    })
    setSelected(note.id)
    setToken((value) => value + 1)
  }
  const create = async (notebook: boolean) => {
    setFailure(null)
    try {
      changed(await api.createNote({ title: notebook ? 'New notebook' : 'New note', notebook }))
    } catch (error) {
      setFailure(asApiError(error))
    }
  }

  if (result.state === 'loading') return <Loading />
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />

  return (
    <div className="notes-layout">
      <section className="card notes-index" aria-labelledby="notes-index-heading">
        <h2 id="notes-index-heading">Notes</h2>
        <div className="actions">
          <button type="button" className="button primary small" onClick={() => void create(true)}>
            New notebook
          </button>
          <button type="button" className="button small" onClick={() => void create(false)}>
            New note
          </button>
        </div>
        <label className="field">
          <span className="visually-hidden">Search notes</span>
          <input type="search" value={words} placeholder="Search notes" onChange={(event) => setWords(event.target.value)} />
        </label>
        {found !== null ? (
          found.length === 0 ? (
            <p className="muted small">No note holds those words.</p>
          ) : (
            <ul className="list small">
              {found.map((entry) => (
                <li key={entry.id}>
                  <button type="button" className="link-button" onClick={() => changed(entry)}>
                    {entry.title}
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : notes.length === 0 ? (
          <p className="muted">Start a notebook for a rotation, a subject or a question you keep coming back to.</p>
        ) : (
          <Branch parent={null} tree={tree} open={open} onToggle={toggle} selected={selected} onSelect={setSelected} depth={0} />
        )}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>
      {selected !== null && notes.some((entry) => entry.id === selected) ? (
        <Editor
          key={selected}
          noteId={selected}
          notes={notes}
          tree={tree}
          onChanged={changed}
          onDeleted={() => {
            setSelected(null)
            setToken((value) => value + 1)
          }}
        />
      ) : null}
    </div>
  )
}
