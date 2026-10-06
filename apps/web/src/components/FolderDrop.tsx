/**
 * A new pile by dropping files on the web app (feedback of 5 October). The Mac files
 * them in the source folder at piles/<confidence>/<pile>/, where they can be seen and
 * moved like anything put there by hand, and reads them in straight away.
 */

import { useRef, useState } from 'react'
import type { DragEvent, FormEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { Tier } from '../lib/types'

const TIERS: { value: Tier; label: string }[] = [
  { value: 'high', label: 'High confidence (a guideline you rely on)' },
  { value: 'mid', label: 'Medium confidence (lectures, reviews)' },
  { value: 'low', label: 'Low confidence (notes to check)' }
]

interface Placed {
  filename: string
  status: string
  message?: string
}

export function FolderDrop({ onPlaced }: { onPlaced?: () => void }) {
  const [pile, setPile] = useState('')
  const [tier, setTier] = useState<Tier>('mid')
  const [files, setFiles] = useState<File[]>([])
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [result, setResult] = useState<{ folder: string; files: Placed[] } | null>(null)
  const input = useRef<HTMLInputElement>(null)

  const add = (incoming: FileList | null) => {
    if (!incoming) return
    setFiles((current) => [...current, ...Array.from(incoming)].slice(0, 20))
    setResult(null)
  }

  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    add(event.dataTransfer.files)
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setFailure(null)
    try {
      const placed = await api.dropIntoFolder(pile.trim(), tier, files)
      setResult(placed)
      setFiles([])
      onPlaced?.()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card" aria-labelledby="folder-drop-heading">
      <h2 id="folder-drop-heading">New pile</h2>
      <form onSubmit={(event) => void submit(event)}>
        <label className="field">
          <span>Pile name</span>
          <input type="text" value={pile} maxLength={100} placeholder="e.g. Sepsis lectures" onChange={(event) => setPile(event.target.value)} />
        </label>
        <label className="field">
          <span>How far you trust it</span>
          <select value={tier} onChange={(event) => setTier(event.target.value as Tier)}>
            {TIERS.map((choice) => (
              <option key={choice.value} value={choice.value}>
                {choice.label}
              </option>
            ))}
          </select>
        </label>
        <div
          className={`drop-zone${over ? ' over' : ''}`}
          role="button"
          tabIndex={0}
          aria-label="Drop files here, or choose them"
          onClick={() => input.current?.click()}
          onKeyDown={(event) => {
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              input.current?.click()
            }
          }}
          onDragOver={(event) => {
            event.preventDefault()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={drop}
        >
          <p>{files.length === 0 ? 'Drop files here, or click to choose them' : `${files.length} file${files.length === 1 ? '' : 's'} ready`}</p>
          {files.length > 0 ? (
            <ul className="list small">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>{file.name}</li>
              ))}
            </ul>
          ) : null}
          <input ref={input} type="file" multiple hidden onChange={(event) => add(event.target.files)} data-testid="folder-drop-input" />
        </div>
        <p className="muted small">
          PDF, Word, PowerPoint, text and pictures. They are filed on this Mac in your source folder under piles/, then read in.
        </p>
        <div className="actions">
          <button type="submit" className="button primary" disabled={busy || files.length === 0 || pile.trim() === ''}>
            {busy ? 'Filing…' : 'File them in the source folder'}
          </button>
          {files.length > 0 ? (
            <button type="button" className="button ghost" disabled={busy} onClick={() => setFiles([])}>
              Clear
            </button>
          ) : null}
        </div>
      </form>
      {result ? (
        <div role="status">
          <p className="ok">
            Filed in <code>{result.folder}</code>; reading them in now.
          </p>
          <ul className="list small">
            {result.files.map((file) => (
              <li key={file.filename}>
                {file.filename}{' '}
                <span className="muted">
                  · {file.status === 'placed' ? 'filed' : file.status === 'already_there' ? 'already there' : file.message || 'not filed'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}
