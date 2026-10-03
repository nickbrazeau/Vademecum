/**
 * The Improvement Map.
 *
 * A description of where the gaps are, not a list of work to get through. It
 * reports what you flagged and where it sits; what you do about it is yours.
 *
 * The map is drawn as a graph (TopicGraph.tsx) and the full flag list stays
 * underneath it: the graph is a view of the same facts, never the only view.
 */

import { useEffect, useRef, useState } from 'react'
import { ConfidenceBadge, ConfidenceMeaning } from '../components/ConfidenceBadge'
import { ExamReports } from '../components/ExamReports'
import { TopicGraph, UNFILED_ID, isShown } from '../components/TopicGraph'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import type { Flag, MapPosition } from '../lib/types'
import { useLoad } from '../lib/useLoad'

function FlagList({ flags, onChanged }: { flags: Flag[]; onChanged: () => void }) {
  const [failure, setFailure] = useState<ApiError | null>(null)

  const markAddressed = async (flagId: string) => {
    setFailure(null)
    try {
      await api.updateFlag(flagId, { status: 'addressed' })
      onChanged()
    } catch (error) {
      // A flag that would not change state has to say so. Reloading as though
      // it had worked shows the old state and calls it the new one.
      setFailure(asApiError(error))
    }
  }

  if (flags.length === 0) {
    return <p className="muted">Nothing flagged yet. Press ⌘K whenever something comes up.</p>
  }
  return (
    <>
      {failure ? (
        <p className="failure" role="alert">
          {failure.message} That flag is unchanged.
        </p>
      ) : null}
      <ul className="list">
        {flags.map((flag) => (
          <li key={flag.id}>
            <span className="title">{flag.text}</span>
            <span className="muted small"> · {flag.topic ?? 'not filed yet'}</span>
            {flag.status === 'open' ? (
              <button
                type="button"
                className="button ghost small"
                onClick={() => void markAddressed(flag.id)}
              >
                Mark addressed
              </button>
            ) : (
              <span className="muted small"> · addressed</span>
            )}
          </li>
        ))}
      </ul>
    </>
  )
}

export function ImprovementMap({ reloadToken }: { reloadToken: number }) {
  const map = useLoad(() => api.improvementMap(), [reloadToken])
  const flags = useLoad(() => api.listFlags(), [reloadToken])
  // Everything in the piles is an area to review, so the map starts with everything shown.
  const [openOnly, setOpenOnly] = useState(false)
  const [selected, setSelected] = useState<string | null>(null)
  // Legend entries switched off. A view preference, not a fact: never stored.
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set())
  const toggleSpecialty = (id: string) =>
    setHidden((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  const [layoutFailure, setLayoutFailure] = useState<ApiError | null>(null)
  const [specialtyFailure, setSpecialtyFailure] = useState<ApiError | null>(null)

  const reloadBoth = () => {
    map.reload()
    flags.reload()
  }

  // The layout is remembered a moment after it stops changing: one write per
  // settle or drag, not one per pointer move. A failed save is said, not
  // retried in a loop; the picture on screen is unaffected.
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null)
  const rememberLayout = (positions: MapPosition[]) => {
    if (pending.current) clearTimeout(pending.current)
    pending.current = setTimeout(() => {
      pending.current = null
      api.saveMapPositions(positions).then(
        () => setLayoutFailure(null),
        (error) => setLayoutFailure(asApiError(error))
      )
    }, 600)
  }
  useEffect(() => () => {
    if (pending.current) clearTimeout(pending.current)
  }, [])

  const assignSpecialty = async (topic: string, specialtyId: string | null) => {
    setSpecialtyFailure(null)
    try {
      await api.setTopicSpecialty(topic, specialtyId)
      map.reload()
    } catch (error) {
      setSpecialtyFailure(asApiError(error))
    }
  }

  if (map.result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (map.result.state === 'failed') {
    return <Unavailable error={map.result.error} onRetry={reloadBoth} />
  }

  const value = map.result.value
  const hasTopics = value.topics.length > 0 || value.covered_topics.length > 0 || value.report_areas.length > 0
  const selectedGap =
    selected === null
      ? null
      : value.topics.find((gap) => (gap.topic ?? UNFILED_ID) === selected) ?? null
  const selectedCovered =
    selected === null ? null : value.covered_topics.find((entry) => entry.topic === selected) ?? null
  const selectedLabel = selected === UNFILED_ID ? 'Not filed yet' : selected
  const selectedSpecialty = selectedGap?.specialty ?? selectedCovered?.specialty ?? null
  const selectedShown =
    selected === null ||
    isShown({ specialty: selectedSpecialty?.id ?? null, unfiled: selected === UNFILED_ID }, hidden)
  const selectedFlags =
    flags.result.state === 'ready' && selected !== null
      ? flags.result.value.filter((flag) => (flag.topic ?? UNFILED_ID) === selected)
      : []
  const neighbours =
    selected === null
      ? []
      : value.links
          .filter((link) => link.a === selected || link.b === selected)
          .map((link) => (link.a === selected ? link.b : link.a))

  return (
    <div className="stack">
      <section className="card" aria-labelledby="map-heading">
        <h2 id="map-heading">Where the gaps are</h2>
        {!hasTopics ? (
          <p className="muted">
            Nothing to draw yet. This map fills in from your piles, from what you flag, and from any exam report you add below.
          </p>
        ) : (
          <>
            <p className="muted small">
              A map, not a list. Drag a topic to move it, tap it to see its flags, pinch or
              scroll to zoom. Nothing here is a queue; it describes, it does not assign.
            </p>
            <div className="chips" role="group" aria-label="Which topics to show">
              <button
                type="button"
                className={`chip ${openOnly ? 'on' : ''}`}
                aria-pressed={openOnly}
                onClick={() => setOpenOnly(true)}
              >
                Open flags
              </button>
              <button
                type="button"
                className={`chip ${openOnly ? '' : 'on'}`}
                aria-pressed={!openOnly}
                onClick={() => setOpenOnly(false)}
              >
                Everything covered
              </button>
            </div>
            <TopicGraph
              topics={value.topics}
              covered={value.covered_topics}
              links={value.links}
              reports={value.report_areas}
              specialties={value.specialties}
              positions={value.positions}
              openOnly={openOnly}
              selected={selected}
              onSelect={setSelected}
              onPositions={rememberLayout}
              hidden={hidden}
              onToggleSpecialty={toggleSpecialty}
            />
            <p className="muted small">
              Size is open flags; colour is the specialty, and each legend entry switches its
              topics on or off. A dashed ring is an area an exam report placed below the mark.
              A line means one learning point was filed under both topics.
              Where things sit is remembered between opens; new topics settle in around the ones
              already placed.
            </p>
            {layoutFailure ? (
              <p className="failure small" role="status">
                The layout could not be remembered: {layoutFailure.message} The map is unchanged.
              </p>
            ) : null}
          </>
        )}
        {value.unfiled_flag_count > 0 ? (
          <p className="muted small">
            {value.unfiled_flag_count} flags have no topic yet. Filing them is the system&rsquo;s
            job, and it has not done it in this version — they are safe where they are.
          </p>
        ) : null}
      </section>

      {selected !== null && selectedShown ? (
        <section className="card selected-topic" aria-labelledby="selected-heading">
          <h3 id="selected-heading">{selectedLabel}</h3>
          <p className="muted small">
            {selectedGap ? `${selectedGap.open_flags} open · ${selectedGap.addressed_flags} addressed` : 'No flags'}
            {selectedCovered ? ` · ${selectedCovered.point_count} learning points` : ''}
            {selectedCovered?.cluster ? ` · mostly from ${selectedCovered.cluster.title}` : ''}
            {neighbours.length > 0 ? ` · linked to ${neighbours.join(', ')}` : ''}
          </p>
          {selected !== UNFILED_ID ? (
            <label className="field specialty-field">
              <span>
                Specialty
                {selectedSpecialty?.assigned_by === 'name' ? (
                  <span className="muted"> (matched from the name — choose to confirm)</span>
                ) : null}
              </span>
              <select
                value={selectedSpecialty?.id ?? ''}
                onChange={(event) =>
                  void assignSpecialty(selected, event.target.value === '' ? null : event.target.value)
                }
              >
                <option value="">No specialty</option>
                {value.specialties.map((entry) => (
                  <option key={entry.id} value={entry.id}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {specialtyFailure ? (
            <p className="failure small" role="alert">
              {specialtyFailure.message} The specialty is unchanged.
            </p>
          ) : null}
          {flags.result.state === 'ready' ? (
            <FlagList flags={selectedFlags} onChanged={reloadBoth} />
          ) : null}
          <div className="actions">
            <button type="button" className="button ghost small" onClick={() => setSelected(null)}>
              Clear selection
            </button>
          </div>
        </section>
      ) : null}

      <ExamReports onChanged={reloadBoth} />

      <section className="card" aria-labelledby="material-heading">
        <h2 id="material-heading">Material behind it</h2>
        <ConfidenceMeaning />
        <ul className="tiers">
          {value.confidences.map((entry) => (
            <li key={entry.confidence}>
              <ConfidenceBadge tier={entry.confidence} label={entry.label} />
              <span className="muted small">
                {entry.pile_count} piles · {entry.source_count} files
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card" aria-labelledby="flags-heading">
        <h2 id="flags-heading">Everything you have flagged</h2>
        {flags.result.state === 'loading' ? <p className="muted">Reading…</p> : null}
        {flags.result.state === 'failed' ? (
          <Unavailable
            error={asApiError(flags.result.error)}
            onRetry={reloadBoth}
          />
        ) : null}
        {flags.result.state === 'ready' ? (
          <FlagList flags={flags.result.value} onChanged={reloadBoth} />
        ) : null}
      </section>
    </div>
  )
}
