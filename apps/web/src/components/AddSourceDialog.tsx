/**
 * Add a source from anywhere (feedback of 6 October): paste text or drop files, into a
 * pile you pick or a new one. On the Mac the files are filed in the source folder at
 * piles/<confidence>/<pile>/ and read in; on the phone they are uploaded to the cloud
 * copy, which reads them, and the Mac takes them in at its next sync.
 */

import { useEffect, useRef, useState } from 'react'
import type { DragEvent } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import type { Pile, Tier } from '../lib/types'
import { PhiWarning } from './PhiWarning'

// The cloud copy's front door carries about 100 MB in one request.
export const PHONE_MAX_BYTES = 95 * 1024 * 1024
const NEW_PILE = '__new__'

const TIER_LABEL: Record<Tier, string> = {
  high: 'High confidence (a guideline you rely on)',
  mid: 'Medium confidence (lectures, reviews)',
  low: 'Low confidence (notes to check)'
}

function textFile(title: string, text: string): File {
  const name = `${(title.trim() || 'Pasted note').replace(/[\\/:*?"<>|]+/g, ' ').slice(0, 80)}.md`
  return new File([text], name, { type: 'text/markdown' })
}

export function AddSourceDialog({ open, onClose, onPhone }: { open: boolean; onClose: () => void; onPhone: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const [piles, setPiles] = useState<Pile[]>([])
  const [pileId, setPileId] = useState(NEW_PILE)
  const [newPile, setNewPile] = useState('')
  const [tier, setTier] = useState<Tier>('mid')
  const [title, setTitle] = useState('')
  const [text, setText] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [over, setOver] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [done, setDone] = useState('')

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open) {
      if (!element.open) {
        try {
          element.showModal()
        } catch {
          element.setAttribute('open', '')
        }
      }
      api.listPiles().then(
        (list) => {
          setPiles(list)
          if (list.length > 0) setPileId((current) => (current === NEW_PILE ? list[0]!.id : current))
        },
        () => setPiles([])
      )
    } else if (element.open) {
      element.close()
    }
  }, [open])

  const chosen = piles.find((pile) => pile.id === pileId) ?? null
  const pileName = pileId === NEW_PILE ? newPile.trim() : chosen?.title ?? ''
  const tooBig = onPhone ? files.filter((file) => file.size > PHONE_MAX_BYTES) : []
  const ready = pileName !== '' && (text.trim() !== '' || files.length > 0) && tooBig.length === 0

  const add = (incoming: FileList | null) => {
    if (!incoming) return
    setFiles((current) => [...current, ...Array.from(incoming)].slice(0, 20))
    setDone('')
  }

  const drop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setOver(false)
    add(event.dataTransfer.files)
  }

  const submit = async () => {
    if (!ready || busy) return
    setBusy(true)
    setFailure(null)
    setDone('')
    const all = [...files, ...(text.trim() ? [textFile(title, text)] : [])]
    const confidence: Tier = pileId === NEW_PILE ? tier : chosen?.tier ?? tier
    try {
      if (onPhone) {
        const target = pileId === NEW_PILE ? await api.createPile({ title: pileName, tier: confidence }) : chosen!
        const report = await api.uploadSources(target.id, all, confidence)
        setDone(
          `${report.accepted} added to ${target.title}${report.rejected ? `, ${report.rejected} not added` : ''}. Your Mac takes ${
            report.accepted === 1 ? 'it' : 'them'
          } in at its next sync.`
        )
      } else {
        const placed = await api.dropIntoFolder(pileName, confidence, all)
        const filed = placed.files.filter((file) => file.status === 'placed').length
        setDone(`${filed} filed in ${placed.folder} and being read in now.`)
      }
      setFiles([])
      setText('')
      setTitle('')
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog ref={dialog} className="quick-flag add-source" aria-labelledby="add-source-heading" onClose={onClose}>
      <form
        method="dialog"
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
        onKeyDown={(event) => {
          if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
            event.preventDefault()
            void submit()
          }
        }}
      >
        <h2 id="add-source-heading">Add a source</h2>
        <label className="field">
          <span>Pile</span>
          <select value={pileId} onChange={(event) => setPileId(event.target.value)}>
            {piles.map((pile) => (
              <option key={pile.id} value={pile.id}>
                {pile.title}
              </option>
            ))}
            <option value={NEW_PILE}>New pile…</option>
          </select>
        </label>
        {pileId === NEW_PILE ? (
          <>
            <label className="field">
              <span>New pile name</span>
              <input type="text" value={newPile} maxLength={100} placeholder="e.g. Sepsis lectures" onChange={(event) => setNewPile(event.target.value)} />
            </label>
            <label className="field">
              <span>How far you trust it</span>
              <select value={tier} onChange={(event) => setTier(event.target.value as Tier)}>
                {(['high', 'mid', 'low'] as Tier[]).map((value) => (
                  <option key={value} value={value}>
                    {TIER_LABEL[value]}
                  </option>
                ))}
              </select>
            </label>
          </>
        ) : null}
        <label className="field">
          <span>Paste text (optional)</span>
          <textarea rows={5} value={text} maxLength={200000} placeholder="Notes, a passage, a summary…" onChange={(event) => setText(event.target.value)} />
        </label>
        {text.trim() ? (
          <label className="field">
            <span>Title for the text</span>
            <input type="text" value={title} maxLength={80} placeholder="Pasted note" onChange={(event) => setTitle(event.target.value)} />
          </label>
        ) : null}
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
          <p>{files.length === 0 ? 'Drop PDFs, pictures, slides or documents here, or tap to choose' : `${files.length} file${files.length === 1 ? '' : 's'} ready`}</p>
          {files.length > 0 ? (
            <ul className="list small">
              {files.map((file, index) => (
                <li key={`${file.name}-${index}`}>{file.name}</li>
              ))}
            </ul>
          ) : null}
          <input
            ref={input}
            type="file"
            multiple
            hidden
            accept=".pdf,.png,.jpg,.jpeg,.heic,.docx,.pptx,.txt,.md,.csv,.tsv,image/*,application/pdf"
            onChange={(event) => add(event.target.files)}
            data-testid="add-source-input"
          />
        </div>
        {tooBig.length > 0 ? (
          <p className="failure small" role="alert">
            {tooBig.map((file) => file.name).join(', ')} {tooBig.length === 1 ? 'is' : 'are'} over 95 MB, more than the phone can send. Add{' '}
            {tooBig.length === 1 ? 'it' : 'them'} on the Mac instead.
          </p>
        ) : null}
        <PhiWarning />
        {done ? (
          <p className="ok" role="status">
            {done}
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
        <div className="actions">
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
          <button type="submit" className="button primary" disabled={!ready || busy}>
            {busy ? 'Adding…' : 'Add'}
            <kbd className="shortcut" aria-hidden="true">
              ⌘S
            </kbd>
          </button>
        </div>
      </form>
    </dialog>
  )
}
