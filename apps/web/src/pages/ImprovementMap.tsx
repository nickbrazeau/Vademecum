/**
 * The Improvement Map.
 *
 * A description of where the gaps are, not a list of work to get through. It
 * reports what you flagged and where it sits; what you do about it is yours.
 *
 * The map is drawn as a graph (TopicGraph.tsx) and the full flag list stays
 * underneath it: the graph is a view of the same facts, never the only view.
 */

import { useEffect, useMemo, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { ExamReports } from '../components/ExamReports'
import { StrengthsView } from '../components/StrengthsView'
import { StudyNext, TopicKnowledge } from '../components/StudyNext'
import { PAGE_PREFIX, TopicGraph, isShown } from '../components/TopicGraph'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import type { EncyclopediaEntry, Flag, KnowledgeState, LearnerModel, LearnerUnit, MapPage, MapPosition } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { openPageLater, openTutorLater } from '../lib/pageLink'
import type { RouteName } from '../lib/router'

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

/** Flags by topic, each topic opening on its own (ADR 0026). */
function FlagGroups({ flags, onChanged }: { flags: Flag[]; onChanged: () => void }) {
  if (flags.length === 0) {
    return <p className="muted">Nothing flagged yet. Press ⌘K whenever something comes up.</p>
  }
  const groups = new Map<string, Flag[]>()
  for (const flag of flags) {
    const key = flag.topic ?? 'Not filed yet'
    groups.set(key, [...(groups.get(key) ?? []), flag])
  }
  const ordered = [...groups.entries()].sort(
    (a, b) => b[1].filter((f) => f.status === 'open').length - a[1].filter((f) => f.status === 'open').length || a[0].localeCompare(b[0])
  )
  return (
    <div className="flag-groups">
      {ordered.map(([topic, items]) => (
        <details key={topic} className="flag-group">
          <summary>
            <span className="strength-name">{topic}</span>{' '}
            <span className="muted small">
              {items.filter((f) => f.status === 'open').length} open · {items.length} in all
            </span>
          </summary>
          <FlagList flags={items} onChanged={onChanged} />
        </details>
      ))}
    </div>
  )
}

/** A clicked topic's own pages and its neighbours', each a link into the Encyclopedia (feedback of 5 October). */
function TopicConnections({
  topic,
  neighbours,
  onSelect,
  onNavigate,
  mapped = []
}: {
  topic: string
  neighbours: string[]
  onSelect: (topic: string) => void
  onNavigate?: (name: RouteName) => void
  /** The pages the map itself matched to this topic, best first. */
  mapped?: MapPage[]
}) {
  const [pages, setPages] = useState<Record<string, EncyclopediaEntry[]> | null>(null)
  useEffect(() => {
    let live = true
    setPages(null)
    const topics = [topic, ...neighbours.slice(0, 8)]
    Promise.all(
      topics.map((name) =>
        api.encyclopediaList(name).then(
          (list) => [name, list.entries.slice(0, 3)] as const,
          () => [name, [] as EncyclopediaEntry[]] as const
        )
      )
    ).then((found) => {
      if (live) setPages(Object.fromEntries(found))
    })
    return () => {
      live = false
    }
  }, [topic, neighbours.join('|')])

  const open = (entryId: string) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    openPageLater(entryId)
    onNavigate('encyclopedia')
  }
  const links = (entries: EncyclopediaEntry[] | undefined) =>
    entries && entries.length > 0 ? (
      entries.map((entry, index) => (
        <span key={entry.id}>
          {index > 0 ? ', ' : ''}
          <a href={`/encyclopedia?page=${encodeURIComponent(entry.id)}`} onClick={open(entry.id)}>
            {entry.title}
          </a>
        </span>
      ))
    ) : (
      <span className="muted">no page yet</span>
    )

  // Tutor mode on this topic's page (feedback of 6 October): the Socratic tutor or its board questions.
  const own: { id: string; title: string }[] = mapped.length > 0 ? mapped : (pages?.[topic] ?? []).map((entry) => ({ id: entry.id, title: entry.title }))
  const tutor = (mode: 'socratic' | 'questions', page: { id: string; title: string }) => (event: MouseEvent) => {
    if (onNavigate === undefined) return
    event.preventDefault()
    openTutorLater({ mode, entryId: page.id, title: page.title })
    onNavigate('tutor')
  }

  return (
    <div className="topic-connections">
      <h4>Encyclopedia</h4>
      <p className="small">
        {mapped.length > 0
          ? links(mapped.map((page) => ({ id: page.id, title: page.title }) as EncyclopediaEntry))
          : pages === null
            ? <span className="muted">Looking…</span>
            : links(pages[topic])}
      </p>
      {own.length > 0 ? (
        <>
          <h4>Tutor mode</h4>
          {own.slice(0, 2).map((page) => (
            <div key={page.id} className="actions tutor-mode">
              <span className="small">{page.title}:</span>
              <a className="button small primary" href={`/tutor?mode=socratic&page=${encodeURIComponent(page.id)}`} onClick={tutor('socratic', page)}>
                Socratic tutor
              </a>
              <a className="button small" href={`/tutor?mode=questions&page=${encodeURIComponent(page.id)}`} onClick={tutor('questions', page)}>
                Board questions
              </a>
            </div>
          ))}
        </>
      ) : null}
      <h4>Connected topics</h4>
      {neighbours.length === 0 ? (
        <p className="muted small">No learning point is filed under this topic and another yet.</p>
      ) : (
        <ul className="list small">
          {neighbours.map((name) => (
            <li key={name}>
              <button type="button" className="link-button" onClick={() => onSelect(name)}>
                {name}
              </button>
              {pages && name in pages ? <span> · {links(pages[name])}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function ImprovementMap({ reloadToken, onNavigate }: { reloadToken: number; onNavigate?: (name: RouteName) => void }) {
  const map = useLoad(() => api.improvementMap(), [reloadToken])
  const flags = useLoad(() => api.listFlags(), [reloadToken])
  const strengths = useLoad(() => api.strengths(), [reloadToken])
  const learner = useLoad(() => api.learner(), [reloadToken])
  // Colour the graph by specialty, or by what you know (ADR 0031). A view preference, not stored.
  const [colourBy, setColourBy] = useState<'specialty' | 'knowledge'>('specialty')
  // Everything in the piles is an area to review, so the map starts with everything shown.
  // Open flags first: where the gaps are (feedback of 5 October).
  const [openOnly, setOpenOnly] = useState(true)
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
  const [filing, setFiling] = useState(false)
  const filingTimer = useRef<number | undefined>(undefined)
  useEffect(() => () => window.clearTimeout(filingTimer.current), [])
  const [filingFailure, setFilingFailure] = useState<ApiError | null>(null)
  const [specialtyFailure, setSpecialtyFailure] = useState<ApiError | null>(null)

  const reloadBoth = () => {
    map.reload()
    flags.reload()
    learner.reload()
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

  const known: LearnerModel | null = learner.result.state === 'ready' ? learner.result.value : null
  // Units by key, built once per load: the graph asks for a colour on every node, every tick.
  const unitsByKey = useMemo(() => {
    const found = new Map<string, LearnerUnit>()
    if (known) for (const unit of [...known.units, ...known.plan]) found.set(unit.key, unit)
    return found
  }, [known])
  if (map.result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (map.result.state === 'failed') {
    return <Unavailable error={map.result.error} onRetry={reloadBoth} />
  }

  const value = map.result.value
  const hasTopics = value.topics.length > 0 || value.covered_topics.length > 0 || value.report_areas.length > 0
  const selectedGap =
    selected === null
      ? null
      : value.topics.find((gap) => gap.topic === selected) ?? null
  const selectedCovered =
    selected === null ? null : value.covered_topics.find((entry) => entry.topic === selected) ?? null
  const selectedSpecialty = selectedGap?.specialty ?? selectedCovered?.specialty ?? null
  const selectedShown =
    selected === null ||
    isShown({ specialty: selectedSpecialty?.id ?? null }, hidden)
  const selectedFlags =
    flags.result.state === 'ready' && selected !== null
      ? flags.result.value.filter((flag) => flag.topic === selected)
      : []
  const unitFor = (name: string): LearnerUnit | null => {
    const key = known?.by_name[name.toLowerCase()]
    return key ? unitsByKey.get(key) ?? null : null
  }
  const knowledgeOf = (node: { id: string; label: string; entryId?: string }): KnowledgeState | null => {
    if (known === null) return null
    if (node.entryId) return known.by_entry[node.entryId] ?? 'untried'
    const key = known.by_name[node.label.toLowerCase()]
    if (!key || !key.startsWith('page:')) return null // no page yet
    return known.by_entry[key.slice('page:'.length)] ?? 'untried'
  }
  const selectedUnit = selected === null ? null : unitFor(selected)
  const neighbours =
    selected === null
      ? []
      : value.links
          .filter((link) => link.a === selected || link.b === selected)
          .map((link) => (link.a === selected ? link.b : link.a))

  return (
    <div className="stack">
      <section className="card" aria-labelledby="next-heading">
        <h2 id="next-heading">Where to go next</h2>
        {learner.result.state === 'ready' ? (
          <StudyNext model={learner.result.value} onNavigate={onNavigate} />
        ) : learner.result.state === 'failed' ? (
          <p className="muted small">{learner.result.error.message}</p>
        ) : (
          <p className="muted">Reading from this Mac…</p>
        )}
      </section>
      <section className="card" aria-labelledby="map-heading">
        <h2 id="map-heading">
          Where the gaps are<sup aria-hidden="true">*</sup>
        </h2>
        {!hasTopics ? (
          <p className="muted">
            Nothing to draw yet. This map fills in from your piles, from what you flag, and from any exam report you add below.
          </p>
        ) : (
          <>
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
            <div className="chips" role="group" aria-label="Colour the map by">
              <button
                type="button"
                className={`chip ${colourBy === 'specialty' ? 'on' : ''}`}
                aria-pressed={colourBy === 'specialty'}
                onClick={() => setColourBy('specialty')}
              >
                By specialty
              </button>
              <button
                type="button"
                className={`chip ${colourBy === 'knowledge' ? 'on' : ''}`}
                aria-pressed={colourBy === 'knowledge'}
                onClick={() => setColourBy('knowledge')}
              >
                By what you know
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
              pages={value.pages}
              pageLinks={value.page_links}
              pageEdges={value.page_edges}
              onOpenPage={(entryId) => {
                openPageLater(entryId)
                onNavigate?.('encyclopedia')
              }}
              colourBy={colourBy}
              knowledgeOf={knowledgeOf}
            />
            {layoutFailure ? (
              <p className="failure small" role="status">
                The layout could not be remembered: {layoutFailure.message} The map is unchanged.
              </p>
            ) : null}
          </>
        )}
        {value.unfiled_flag_count > 0 && !value.can_file_flags ? (
          <p className="muted small">
            {value.unfiled_flag_count} flag{value.unfiled_flag_count === 1 ? ' has' : 's have'} no topic yet. {value.filing_note}
          </p>
        ) : null}
        {value.unfiled_flag_count > 0 && value.can_file_flags ? (
          <div className="unfiled">
            <p className="muted small">
              {value.unfiled_flag_count} flag{value.unfiled_flag_count === 1 ? ' has' : 's have'} no topic yet.
              Filing sends their text, once, to the Mac&rsquo;s own model connection, which names a
              topic for each; nothing else goes. They are safe where they are either way.
            </p>
            <div className="actions">
              <button
                type="button"
                className="button small"
                disabled={filing}
                onClick={() => {
                  setFiling(true)
                  setFilingFailure(null)
                  api.fileFlags().then(
                    () => {
                      filingTimer.current = window.setTimeout(() => {
                        setFiling(false)
                        reloadBoth()
                      }, 4000)
                    },
                    (error) => {
                      setFiling(false)
                      setFilingFailure(asApiError(error))
                    }
                  )
                }}
              >
                {filing ? 'Filing…' : 'File them now'}
              </button>
            </div>
            {filingFailure ? (
              <p className="failure small" role="alert">
                {filingFailure.message}
              </p>
            ) : null}
          </div>
        ) : null}
      </section>

      {selected !== null && selectedShown ? (
        <section className="card selected-topic" aria-labelledby="selected-heading">
          <h3 id="selected-heading">{selected}</h3>
          <p className="muted small">
            {selectedGap ? `${selectedGap.open_flags} open · ${selectedGap.addressed_flags} addressed` : 'No flags'}
            {selectedCovered ? ` · ${selectedCovered.point_count} learning points` : ''}
            {selectedCovered?.cluster ? ` · mostly from ${selectedCovered.cluster.title}` : ''}
          </p>
          {selectedUnit ? <TopicKnowledge unit={selectedUnit} onNavigate={onNavigate} /> : null}
          <TopicConnections
            topic={selected}
            neighbours={neighbours.filter((name) => !name.startsWith(PAGE_PREFIX))}
            onSelect={setSelected}
            onNavigate={onNavigate}
            mapped={value.page_links
              .filter((link) => link.topic === selected)
              .map((link) => value.pages.find((page) => page.id === link.entry_id))
              .filter((page): page is MapPage => page !== undefined)}
          />
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

      <section className="card" aria-labelledby="strengths-heading">
        <h2 id="strengths-heading">Strong and weak, and why</h2>
        {strengths.result.state === 'loading' ? <p className="muted">Reading…</p> : null}
        {strengths.result.state === 'failed' ? <p className="muted small">Not readable right now. {strengths.result.error.message}</p> : null}
        {strengths.result.state === 'ready' ? <StrengthsView strengths={strengths.result.value} /> : null}
      </section>

      <ExamReports onChanged={reloadBoth} />

      <details className="card toggle-card" aria-labelledby="flags-heading">
        <summary>
          <h2 id="flags-heading">Everything you have flagged</h2>
          <span className="muted small">
            {flags.result.state === 'ready' ? `${flags.result.value.filter((flag) => flag.status === 'open').length} open` : ''}
          </span>
        </summary>
        {flags.result.state === 'loading' ? <p className="muted">Reading…</p> : null}
        {flags.result.state === 'failed' ? (
          <Unavailable
            error={asApiError(flags.result.error)}
            onRetry={reloadBoth}
          />
        ) : null}
        {flags.result.state === 'ready' ? (
          <FlagGroups flags={flags.result.value} onChanged={reloadBoth} />
        ) : null}
      </details>
      <p className="muted small map-footnote">
        * Size is open flags; colour is the specialty, and each legend entry switches its topics on or off. A dashed ring is an
        area an exam report placed below the mark. A line means one learning point was filed under both topics. Where things sit
        is remembered between opens; new topics settle in around the ones already placed.
      </p>
    </div>
  )
}
