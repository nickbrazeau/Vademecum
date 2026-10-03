/**
 * Sources: the library everything else is built from.
 *
 * This is the intake, not the study surface — Today and Tutor are where the
 * material is actually used. Piles are grouped by tier confidence, which is
 * how much you trust the material and nothing else.
 */

import { useState } from 'react'
import { BuildPanel } from '../components/BuildPanel'
import { BuildSchedule } from '../components/BuildSchedule'
import { LocalData } from '../components/LocalData'
import { PrivacyNote } from '../components/PrivacyNote'
import { PointCard } from '../components/PointCard'
import { MachineReviewedNote } from '../components/SupportBadge'
import { ConfidenceBadge, ConfidenceMeaning } from '../components/ConfidenceBadge'
import { PhiWarning } from '../components/PhiWarning'
import { SourceList } from '../components/SourceList'
import { UploadPanel } from '../components/UploadPanel'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { clearDraft, itemDraftKey, loadDraft, saveDraft } from '../lib/drafts'
import {
  MAX_ITEM_BODY_LENGTH,
  MAX_ITEM_SOURCE_LENGTH,
  MAX_ITEM_TITLE_LENGTH,
  MAX_PILE_TITLE_LENGTH,
  TIERS,
  TIER_LABEL
} from '../lib/types'
import type { LearningItem, Pile, Tier, Coverage } from '../lib/types'
import { useLoad } from '../lib/useLoad'

/**
 * The older plain-text notes.
 *
 * They predate file upload and are still perfectly good source material, so
 * nothing here was retired — it is just no longer the only way in.
 */
function ItemPanel({ pile, onChanged }: { pile: Pile; onChanged: () => void }) {
  const [token, setToken] = useState(0)
  const { result, reload } = useLoad(() => api.listItems(pile.id), [pile.id, token])
  const draftKey = itemDraftKey(pile.id)
  const [title, setTitle] = useState('')
  // The body is what the item actually is; the title is only its handle. It is
  // autosaved locally as it is typed, the same way flag capture is: that write
  // goes to `localStorage` and nowhere else.
  const [body, setBody] = useState(() => loadDraft(draftKey))
  const [source, setSource] = useState('')
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [deleteFailure, setDeleteFailure] = useState<ApiError | null>(null)
  const [conversion, setConversion] = useState('')

  const refresh = () => {
    setToken((value) => value + 1)
    onChanged()
  }

  const onChangeBody = (value: string) => {
    setBody(value)
    saveDraft(draftKey, value)
  }

  const tooLong = body.length > MAX_ITEM_BODY_LENGTH

  const add = async () => {
    if (title.trim() === '' || saving) return
    setFailure(null)
    if (tooLong) {
      // The API would refuse this. Saying so here costs no round trip and no
      // words: the draft stays exactly where it is.
      setFailure(
        new ApiError(
          'invalid',
          `That body is ${body.length} characters. The most Vademecum stores is ${MAX_ITEM_BODY_LENGTH}.`
        )
      )
      return
    }
    setSaving(true)
    try {
      await api.createItem(pile.id, {
        title: title.trim(),
        body,
        source: source.trim()
      })
      // Saved, so the draft has somewhere better to live now.
      clearDraft(draftKey)
      setTitle('')
      setBody('')
      setSource('')
      refresh()
    } catch (error) {
      // Nothing is cleared: a failed save must not cost what was written.
      setFailure(asApiError(error))
    } finally {
      setSaving(false)
    }
  }

  const remove = async (itemId: string) => {
    setDeleteFailure(null)
    try {
      await api.deleteItem(itemId)
      refresh()
    } catch (error) {
      setDeleteFailure(asApiError(error))
    }
  }

  const convert = async (item: LearningItem) => {
    setSaving(true)
    setFailure(null)
    setConversion('')
    try {
      const text = [item.title, item.body, item.source ? `Source: ${item.source}` : ''].filter(Boolean).join('\n\n')
      const filename = `${item.title.replace(/[^a-zA-Z0-9 -]/g, '').slice(0, 80) || 'note'}.txt`
      const report = await api.uploadSources(pile.id, [new File([text], filename, { type: 'text/plain' })], pile.tier)
      const first = report.results[0]
      setConversion(first?.outcome === 'stored' || first?.outcome === 'duplicate'
        ? 'A local text source is ready. The original note is kept. Preview Build learning material to include it; no model request has started.'
        : first?.message || 'Conversion was not confirmed. Check the source list before retrying.')
      refresh()
    } catch (error) { setFailure(asApiError(error)) }
    finally { setSaving(false) }
  }

  return (
    <div className="items">
      <h4>Plain-text notes</h4>
      <p className="muted small">
        Notes stay local and are not automatically included in builds. Choose Use as source to copy
        a saved note into an extractable text file, then preview a build. The original note is kept.
      </p>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void add()
        }}
      >
        <label className="field">
          <span>Add a learning item</span>
          <input
            type="text"
            value={title}
            maxLength={MAX_ITEM_TITLE_LENGTH}
            placeholder="What is it?"
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="field">
          <span>
            What it says <span className="muted">(optional)</span>
          </span>
          <textarea
            name="body"
            rows={6}
            value={body}
            maxLength={MAX_ITEM_BODY_LENGTH}
            spellCheck
            placeholder="The lecture point, the guideline wording, the thing you want back later…"
            onChange={(event) => onChangeBody(event.target.value)}
          />
        </label>
        <label className="field">
          <span>
            Where it came from <span className="muted">(optional)</span>
          </span>
          <input
            type="text"
            value={source}
            maxLength={MAX_ITEM_SOURCE_LENGTH}
            placeholder="Lecture, guideline, paper…"
            onChange={(event) => setSource(event.target.value)}
          />
        </label>
        <PhiWarning />
        <p className="muted small">Saved on this Mac. Typing it transmits nothing.</p>
        <button type="submit" className="button primary" disabled={title.trim() === '' || saving}>
          {saving ? 'Saving…' : 'Add item'}
        </button>
        {failure ? (
          <p className="failure" role="alert">
            {failure.message} Your text is still here.
          </p>
        ) : null}
      </form>

      {result.state === 'loading' ? <p className="muted">Reading…</p> : null}
      {result.state === 'failed' ? <Unavailable error={result.error} onRetry={reload} /> : null}
      {deleteFailure ? (
        <p className="failure" role="alert">
          {deleteFailure.message} Refresh to confirm the note’s state before retrying.
        </p>
      ) : null}
      {conversion ? <p className="body" role="status">{conversion}</p> : null}
      {result.state === 'ready' ? (
        result.value.length === 0 ? (
          <p className="muted">No plain-text notes in this pile.</p>
        ) : (
          <ul className="list">
            {result.value.map((item) => (
              <li key={item.id}>
                <span className="title">{item.title}</span>
                {item.source ? <span className="muted small"> · {item.source}</span> : null}
                {item.body ? <p className="muted small body">{item.body}</p> : null}
                <button type="button" className="button small" disabled={saving} onClick={() => void convert(item)}>
                  Use as source
                </button>
                <button
                  type="button"
                  className="button ghost small"
                  onClick={() => void remove(item.id)}
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  )
}

function GeneratedMaterial({ pileId, token }: { pileId: string; token: number }) {
  const { result, reload } = useLoad(() => api.listPoints({ pile_id: pileId }), [pileId, token])
  if (result.state === 'loading') return <p className="muted">Reading learning material…</p>
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />
  if (!result.value.length) return <p className="muted">No learning points have been built for this pile yet.</p>
  return <section aria-label="Learning points in this pile">
    <h4>Learning points</h4>
    <MachineReviewedNote />
    <ul className="list">{result.value.map((point) => <PointCard key={point.id} point={point} />)}</ul>
  </section>
}

function PilePanel({ pile, onChanged }: { pile: Pile; onChanged: () => void }) {
  const [token, setToken] = useState(0)
  const [failure, setFailure] = useState<ApiError | null>(null)

  const refresh = () => {
    setToken((value) => value + 1)
    onChanged()
  }

  const removePile = async () => {
    setFailure(null)
    try {
      await api.deletePile(pile.id)
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    }
  }

  return (
    <div className="pile-panel">
      <UploadPanel pileId={pile.id} defaultConfidence={pile.tier} onUploaded={refresh} />

      <h4>Files in this pile</h4>
      <SourceList pileId={pile.id} token={token} onChanged={refresh} />

      <BuildPanel pileId={pile.id} onChanged={refresh} />

      <ItemPanel pile={pile} onChanged={refresh} />
      <GeneratedMaterial pileId={pile.id} token={token} />

      <div className="actions">
        <button type="button" className="button ghost small" onClick={() => void removePile()}>
          Delete this pile
        </button>
      </div>
      {failure ? (
        <p className="failure" role="alert">
          {failure.kind === 'conflict'
            ? `${failure.message} The pile is still here.`
            : `${failure.message} Deletion could not be confirmed. Refresh the list before trying again.`}
        </p>
      ) : null}
    </div>
  )
}

/**
 * The pile's coverage in words. The counts stay, even in a dense row: a
 * percentage reads as progress, and "0 of 25 extracted-text characters" says
 * what was actually read (CoverageBar.tsx makes the same choice).
 */
function coverageLabel(coverage: Coverage): string {
  return coverage.complete
    ? 'fully processed'
    : `${coverage.covered} of ${coverage.total} extracted-text ${coverage.unit} processed`
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={open ? 'chevron open' : 'chevron'}
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 6l4 4 4-4" />
    </svg>
  )
}

export function Sources() {
  const [token, setToken] = useState(0)
  const { result, reload } = useLoad(() => api.listPiles(), [token])
  const [openPile, setOpenPile] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [tier, setTier] = useState<Tier>('mid')
  const [failure, setFailure] = useState<ApiError | null>(null)

  const refresh = () => setToken((value) => value + 1)

  const create = async () => {
    if (title.trim() === '') return
    setFailure(null)
    try {
      await api.createPile({ title: title.trim(), tier })
      setTitle('')
      refresh()
    } catch (error) {
      setFailure(asApiError(error))
    }
  }

  return (
    <div className="stack">
      <BuildSchedule onBuilt={refresh} />

      <section className="card" aria-labelledby="sources-intro-heading">
        <h2 id="sources-intro-heading">Sources</h2>
        <p className="muted">
          Add source files here to build learning points and Tutor questions. Saving a file or note
          is local. Open a pile to preview Build learning material, then choose Send this batch to
          authorize the model checks. Today also shows relevant public literature updates.
        </p>
        <ConfidenceMeaning />
      </section>

      <section className="card" aria-labelledby="new-pile-heading">
        <h2 id="new-pile-heading">New pile</h2>
        <form
          className="stack"
          onSubmit={(event) => {
            event.preventDefault()
            void create()
          }}
        >
          <label className="field">
            <span>Title</span>
            <input
              type="text"
              value={title}
              maxLength={MAX_PILE_TITLE_LENGTH}
              placeholder="Antimicrobial stewardship"
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <fieldset className="field tier-picker">
            <legend>Tier confidence</legend>
            {TIERS.map((option) => (
              <label key={option} className={`tier-option tier-${option}`}>
                <input
                  type="radio"
                  name="tier"
                  value={option}
                  checked={tier === option}
                  onChange={() => setTier(option)}
                />
                <span>{TIER_LABEL[option]}</span>
              </label>
            ))}
          </fieldset>
          <button type="submit" className="button primary" disabled={title.trim() === ''}>
            Create pile
          </button>
          {failure ? (
            <p className="failure" role="alert">
              {failure.message}
            </p>
          ) : null}
        </form>
      </section>

      {result.state === 'loading' ? <p className="muted">Reading from this Mac…</p> : null}
      {result.state === 'failed' ? <Unavailable error={result.error} onRetry={reload} /> : null}
      {result.state === 'ready'
        ? TIERS.map((option) => {
            const piles = result.value.filter((pile) => pile.tier === option)
            return (
              <section key={option} className="card dense" aria-labelledby={`tier-${option}-heading`}>
                <h2 id={`tier-${option}-heading`}>
                  <ConfidenceBadge tier={option} />
                  <span className="visually-hidden">tier confidence</span>
                </h2>
                {piles.length === 0 ? (
                  <p className="muted">No piles at this confidence.</p>
                ) : (
                  <ul className="list">
                    {piles.map((pile) => (
                      <li key={pile.id}>
                        <button
                          type="button"
                          className="disclosure pile-row"
                          aria-expanded={openPile === pile.id}
                          onClick={() => setOpenPile(openPile === pile.id ? null : pile.id)}
                        >
                          <span className="pile-row-main">
                            <span className="title">{pile.title}</span>
                            <span className="row-meta">
                              {pile.source_count} files · {pile.item_count} notes ·{' '}
                              {pile.point_count} points · {pile.question_count} questions ·{' '}
                              {coverageLabel(pile.coverage)}
                            </span>
                          </span>
                          <span className="mini-bar" aria-hidden="true">
                            <span
                              className="mini-fill"
                              style={{ width: `${Math.max(0, Math.min(100, pile.coverage.percent))}%` }}
                            />
                          </span>
                          <Chevron open={openPile === pile.id} />
                        </button>
                        {openPile === pile.id ? (
                          <PilePanel pile={pile} onChanged={refresh} />
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )
          })
        : null}
      <LocalData />
      <PrivacyNote />

    </div>
  )
}
