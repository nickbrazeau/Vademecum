/**
 * The Case Series hub (ADR 0022).
 *
 * Other people's teaching cases in one place: the NEJM's Case Records and
 * Clinical Problem-Solving, the Clinical Problem Solvers, The Curbsiders.
 * Each entry is a title, a link to the original, who made it, and the study
 * notes the Mac wrote beside it: a one-liner, teaching points that each rest
 * on a quote from the publisher's notes, and think-first prompts. The hub is
 * kept updated on a timer the owner switches on here, having read what that
 * sends; it never invents a summary from a title.
 */

import { useState } from 'react'
import type { FormEvent } from 'react'
import { TransmissionDisclosure } from '../components/TransmissionDisclosure'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { dateLabel, momentLabel } from '../lib/format'
import type { CaseEntry, CaseSettings } from '../lib/types'
import { useLoad } from '../lib/useLoad'

export const CASES_DISCLOSURE = {
  headline: 'Keeping the hub updated sends fixed public requests, with nothing of yours in them:',
  bullets: [
    'one fixed PubMed query naming the journal and article type, for the two NEJM series',
    'one fixed request each to clinicalproblemsolving.com and thecurbsiders.com for their latest episodes',
    'for the teaching points: each case’s public title and show notes, once, to the Mac’s own model connection'
  ],
  destination:
    'PubMed (NCBI), the two podcast sites, and the Mac’s own model connection (Codex or Claude, on your sign-in). No API key is used, and none of your material, notes, flags or answers is included.'
}

const SERIES_ORDER = ['nejm_cpc', 'nejm_cps', 'cps', 'curbsiders']

function CaseCard({ entry }: { entry: CaseEntry }) {
  return (
    <li className="case-entry">
      <p className="badges">
        <span className="badge">{entry.series_short}</span>
        {entry.subseries ? <span className="badge">{entry.subseries}</span> : null}
      </p>
      <p className="title">
        <a href={entry.url} target="_blank" rel="noopener noreferrer">
          {entry.title}
        </a>
      </p>
      <p className="muted small">
        {entry.published_on ? `${dateLabel(entry.published_on)} · ` : null}
        {entry.credit ? `By ${entry.credit} · ` : null}
        {entry.publisher}
      </p>
      {entry.one_liner ? <p className="body">{entry.one_liner}</p> : null}
      {entry.points.length > 0 ? (
        <>
          <h4>Teaching points</h4>
          <ol className="steps case-points">
            {entry.points.map((point) => (
              <li key={point.point}>
                <p>{point.point}</p>
                {point.quote ? (
                  <details className="support-details">
                    <summary>From the notes</summary>
                    <blockquote className="quote">{point.quote}</blockquote>
                  </details>
                ) : null}
              </li>
            ))}
          </ol>
        </>
      ) : null}
      {entry.think_first.length > 0 ? (
        <>
          <h4>Think first</h4>
          <ul className="case-prompts">
            {entry.think_first.map((prompt) => (
              <li key={prompt}>{prompt}</li>
            ))}
          </ul>
        </>
      ) : null}
      {entry.status === 'synthesised' && entry.points.length === 0 && entry.snippet === '' ? (
        <p className="muted small">The publisher offers only the title here, so there are prompts and no teaching points. The case itself is one click away.</p>
      ) : null}
      {entry.status === 'new' ? <p className="muted small">Teaching points not written yet.</p> : null}
      {entry.status === 'failed' ? <p className="muted small">{entry.status_detail}</p> : null}
      {entry.snippet && entry.points.length === 0 ? <p className="muted small">{entry.snippet}</p> : null}
      <p className="small">
        <a href={entry.url} target="_blank" rel="noopener noreferrer">
          Open the original
        </a>
      </p>
    </li>
  )
}

function HubSettings({ onChanged }: { onChanged: () => void }) {
  const { result, reload } = useLoad(() => api.caseSettings(), [])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [chosen, setChosen] = useState<Record<string, boolean> | null>(null)
  const [hours, setHours] = useState<string | null>(null)

  if (result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (result.state === 'failed') {
    return (
      <section className="card" aria-labelledby="case-hub-heading">
        <h2 id="case-hub-heading">Keeping the hub updated</h2>
        <p className="muted small">Not readable right now. {result.error.message}</p>
      </section>
    )
  }
  const settings: CaseSettings = result.value
  const series = chosen ?? settings.series
  const hoursText = hours ?? String(settings.interval_hours)

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      setChosen(null)
      setHours(null)
      reload()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const save = (enabled: boolean) =>
    act(() =>
      api.saveCaseSettings({
        enabled,
        interval_hours: Math.max(1, Math.min(168, Number(hoursText) || settings.interval_hours)),
        series
      })
    )

  const refreshLine = settings.last_refresh
    ? `Last refresh ${momentLabel(settings.last_refresh.at)}: ${Object.entries(settings.last_refresh.fetched)
        .map(([name, group]) => (group.error ? `${name} could not be reached (${group.error})` : `${name}: ${group.new} new`))
        .join('; ')}${settings.last_refresh.synthesised ? `; teaching points written for ${settings.last_refresh.synthesised}` : ''}.`
    : 'Not refreshed yet.'

  return (
    <section className="card" aria-labelledby="case-hub-heading">
      <h2 id="case-hub-heading">Keeping the hub updated</h2>
      <p className="muted small">{settings.credit}</p>
      {settings.fetches_here ? (
        <>
          <TransmissionDisclosure disclosure={CASES_DISCLOSURE} />
          {settings.note ? <p className="muted small">{settings.note}</p> : null}
          <fieldset className="case-series-picker">
            <legend>Series to follow</legend>
            {settings.catalogue.map((entry) => (
              <label key={entry.id} className="case-series-option">
                <input
                  type="checkbox"
                  checked={series[entry.id] !== false}
                  disabled={busy}
                  onChange={(event) => setChosen({ ...series, [entry.id]: event.target.checked })}
                />{' '}
                {entry.name} <span className="muted small">({entry.publisher})</span>
              </label>
            ))}
          </fieldset>
          <label htmlFor="case-hours">Hours between refreshes</label>
          <input
            id="case-hours"
            type="number"
            min={1}
            max={168}
            value={hoursText}
            disabled={busy}
            onChange={(event) => setHours(event.target.value)}
          />
          <p className="muted small">
            {settings.enabled ? `On, every ${settings.interval_hours} hour${settings.interval_hours === 1 ? '' : 's'} while Vademecum runs on this Mac.` : 'Off until you turn it on.'}{' '}
            {refreshLine}
          </p>
          <div className="actions">
            <button type="button" className="button primary" disabled={busy} onClick={() => void save(!settings.enabled)}>
              {settings.enabled ? 'Turn the hub off' : 'Turn the hub on'}
            </button>
            {settings.enabled && (chosen !== null || hours !== null) ? (
              <button type="button" className="button ghost" disabled={busy} onClick={() => void save(true)}>
                Save changes
              </button>
            ) : null}
            <button
              type="button"
              className="button"
              disabled={busy || settings.running}
              onClick={() => void act(() => api.refreshCases())}
            >
              {settings.running ? 'Refreshing…' : 'Refresh now'}
            </button>
          </div>
        </>
      ) : (
        <p className="muted small">
          {settings.note} {refreshLine}
        </p>
      )}
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}

export function CaseSeries() {
  const [series, setSeries] = useState('')
  const [typed, setTyped] = useState('')
  const [q, setQ] = useState('')
  const [reloadToken, setReloadToken] = useState(0)
  const { result, reload } = useLoad(
    () => api.listCases({ series: series || undefined, q: q || undefined }),
    [series, q, reloadToken]
  )

  const search = (event: FormEvent) => {
    event.preventDefault()
    setQ(typed.trim())
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="case-series-heading">
        <h2 id="case-series-heading">Case Series</h2>
        <p className="muted">
          Other people’s teaching cases, gathered in one place: the NEJM’s Case Records of the
          Massachusetts General Hospital and Clinical Problem-Solving, the Clinical Problem Solvers,
          and The Curbsiders. Each links to the original and names who made it. The teaching points
          beside a case are written on this Mac from the publisher’s public notes, and each rests on a
          quote; where the publisher offers only a title, you get think-first prompts instead.
        </p>
      </section>

      <HubSettings onChanged={() => setReloadToken((value) => value + 1)} />

      <section className="card" aria-labelledby="case-list-heading">
        <h2 id="case-list-heading">Cases</h2>
        {result.state === 'ready' ? (
          <div className="chips" role="group" aria-label="Series">
            <button type="button" className={`chip${series === '' ? ' on' : ''}`} onClick={() => setSeries('')}>
              All ({result.value.counts.total})
            </button>
            {SERIES_ORDER.map((identifier) => {
              const entry = result.value.catalogue.find((item) => item.id === identifier)
              if (!entry) return null
              return (
                <button
                  key={identifier}
                  type="button"
                  className={`chip${series === identifier ? ' on' : ''}`}
                  onClick={() => setSeries(identifier)}
                >
                  {entry.short} ({result.value.counts.by_series[identifier] ?? 0})
                </button>
              )
            })}
          </div>
        ) : null}
        <form className="case-search" onSubmit={search}>
          <label htmlFor="case-search">Search titles and teaching points</label>
          <input id="case-search" type="search" value={typed} onChange={(event) => setTyped(event.target.value)} />
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
        {result.state === 'ready' && result.value.entries.length === 0 ? (
          <p className="muted">
            {result.value.counts.total === 0
              ? 'Nothing gathered yet. Turn the hub on above, or press Refresh now.'
              : 'Nothing matches that series or search.'}
          </p>
        ) : null}
        {result.state === 'ready' && result.value.entries.length > 0 ? (
          <ul className="list case-list">
            {result.value.entries.map((entry) => (
              <CaseCard key={entry.id} entry={entry} />
            ))}
          </ul>
        ) : null}
      </section>
    </div>
  )
}
