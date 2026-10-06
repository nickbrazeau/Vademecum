/**
 * Knowledge Gap Flag capture.
 *
 * One textarea, one required field, reachable from anywhere with one keystroke
 * (⌘K / Ctrl-K) or one thumb-sized button. Filing it against a topic is the
 * system's job, not the owner's — the topic field is optional and last.
 *
 * What you type is autosaved locally as you go. That autosave writes to
 * `localStorage` and nowhere else: it never calls the API and never calls a
 * model. Saving the flag is a deliberate act.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../lib/api'
import { FLAG_DRAFT_KEY, clearDraft, loadDraft, saveDraft } from '../lib/drafts'
import { PhiWarning } from './PhiWarning'

export const MAX_FLAG_LENGTH = 4000

export function QuickFlagDialog({
  open,
  onClose,
  onSaved
}: {
  open: boolean
  onClose: () => void
  onSaved: () => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const [text, setText] = useState(() => loadDraft(FLAG_DRAFT_KEY))
  const [topic, setTopic] = useState('')
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)

  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog === null) return
    if (open) {
      // `showModal` is the supported path; the attribute fallback keeps this
      // working anywhere the method is missing.
      if (typeof dialog.showModal === 'function') dialog.showModal()
      else dialog.setAttribute('open', '')
      textareaRef.current?.focus()
    } else if (dialog.hasAttribute('open')) {
      if (typeof dialog.close === 'function') dialog.close()
      else dialog.removeAttribute('open')
    }
  }, [open])

  const onChangeText = useCallback((value: string) => {
    setText(value)
    saveDraft(FLAG_DRAFT_KEY, value)
  }, [])

  const submit = useCallback(async () => {
    const trimmed = text.trim()
    if (trimmed === '' || saving) return
    setSaving(true)
    setFailure(null)
    try {
      await api.createFlag({
        text: trimmed,
        ...(topic.trim() === '' ? {} : { topic: topic.trim() })
      })
      clearDraft(FLAG_DRAFT_KEY)
      setText('')
      setTopic('')
      onSaved()
      onClose()
    } catch (error) {
      // The draft is deliberately kept: a failed save must not cost the words.
      setFailure(
        error instanceof ApiError
          ? error
          : new ApiError('server', 'Something went wrong on this machine.')
      )
    } finally {
      setSaving(false)
    }
  }, [text, topic, saving, onSaved, onClose])

  return (
    <dialog
      ref={dialogRef}
      className="quick-flag"
      aria-labelledby="quick-flag-heading"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <form
        method="dialog"
        onKeyDown={(event) => {
          // ⌘S (Ctrl-S elsewhere) saves the flag from any field, rather than the browser's Save Page.
          if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
            event.preventDefault()
            void submit()
          }
        }}
        onSubmit={(event) => {
          event.preventDefault()
          void submit()
        }}
      >
        <h2 id="quick-flag-heading">Flag a knowledge gap</h2>
        <p className="muted">
          Whatever you were unsure about, in your own words. Nothing else is required.
        </p>

        <PhiWarning />

        <label className="field">
          <span>What were you unsure about?</span>
          <textarea
            ref={textareaRef}
            name="text"
            rows={5}
            maxLength={MAX_FLAG_LENGTH}
            value={text}
            autoComplete="off"
            spellCheck
            placeholder="Had to look up…"
            onChange={(event) => onChangeText(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
                event.preventDefault()
                void submit()
              }
            }}
          />
        </label>

        <label className="field">
          <span>
            Topic <span className="muted">(optional)</span>
          </span>
          <input
            name="topic"
            type="text"
            maxLength={120}
            value={topic}
            autoComplete="off"
            onChange={(event) => setTopic(event.target.value)}
          />
        </label>

        <p className="muted small">Saved on this Mac. Not sent anywhere.</p>

        {failure ? (
          <p className="failure" role="alert">
            {failure.message} Your text is still here.
          </p>
        ) : null}

        <div className="actions">
          <button type="button" className="button ghost" onClick={onClose}>
            Close
          </button>
          <button type="submit" className="button primary" disabled={text.trim() === '' || saving}>
            {saving ? 'Saving…' : 'Save flag'}
            <kbd className="shortcut" aria-hidden="true">
              ⌘S
            </kbd>
          </button>
        </div>
      </form>
    </dialog>
  )
}
