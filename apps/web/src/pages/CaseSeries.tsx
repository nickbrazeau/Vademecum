/**
 * Where the Case Series comes from (ADR 0022; feedback of 10 October), in Settings: the
 * series the Mac gathers teaching cases from, each switched on or off, feeds the owner
 * adds, and the timer. New cases themselves appear on Today, with the study notes the Mac
 * wrote beside them; there is no list of every case here.
 */

import { useState } from 'react'
import { TransmissionDisclosure } from '../components/TransmissionDisclosure'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { CaseSettings } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { Switch } from '../components/Switch'
import { Loading } from '../components/Loading'

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

/**
 * A feed of one's own (feedback of 10 October): the address is checked here, the one host
 * it would contact is named, and only on a yes is anything contacted.
 */
function AddFeed({ disabled, onAdded }: { disabled: boolean; onAdded: () => void }) {
  const [url, setUrl] = useState('')
  const [ask, setAsk] = useState<{ url: string; host: string; ask: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [done, setDone] = useState('')
  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="add-feed">
      <label className="field">
        <span>Add a feed of teaching cases (an RSS, podcast or Atom address)</span>
        <input
          type="url"
          value={url}
          placeholder="https://"
          disabled={disabled || busy}
          onChange={(event) => {
            setUrl(event.target.value)
            setAsk(null)
            setDone('')
          }}
        />
      </label>
      {ask === null ? (
        <button
          type="button"
          className="button small"
          disabled={disabled || busy || url.trim().length < 9}
          onClick={() => void run(async () => setAsk(await api.proposeFeed(url.trim())))}
        >
          Check this address
        </button>
      ) : (
        <div className="confirm-feed" role="group" aria-label="Confirm the feed">
          <p className="small">{ask.ask}</p>
          <div className="actions">
            <button
              type="button"
              className="button primary small"
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const added = await api.addFeed(ask.url)
                  setDone(`Added, with ${added.new} case${added.new === 1 ? '' : 's'}.`)
                  setAsk(null)
                  setUrl('')
                  onAdded()
                })
              }
            >
              Yes, add it
            </button>
            <button type="button" className="button ghost small" disabled={busy} onClick={() => setAsk(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      {done ? (
        <p className="ok small" role="status">
          {done}
        </p>
      ) : null}
      {failure ? (
        <p className="failure small" role="alert">
          {failure.message}
        </p>
      ) : null}
    </div>
  )
}

export function HubSettings({ onChanged }: { onChanged: () => void }) {
  const { result, reload } = useLoad(() => api.caseSettings(), [])
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [chosen, setChosen] = useState<Record<string, boolean> | null>(null)
  const [hours, setHours] = useState<string | null>(null)

  if (result.state === 'loading') return <Loading />
  if (result.state === 'failed') {
    return (
      <section className="card" aria-labelledby="case-hub-heading">
        <h2 id="case-hub-heading">Keeping the hub updated</h2>
        <p className="muted small">Not readable right now. {result.error.message}</p>
      </section>
    )
  }
  const settings: CaseSettings = result.value
  // Only the Mac gathers cases; anywhere else there is nothing here to set.
  if (!settings.fetches_here) return null
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
      <TransmissionDisclosure disclosure={CASES_DISCLOSURE} />
      {settings.note ? <p className="muted small">{settings.note}</p> : null}
      <fieldset className="case-series-picker">
        <legend>Where cases come from</legend>
        {settings.catalogue.map((entry) => (
          <div key={entry.id} className="case-source">
            <Switch
              label={entry.name}
              hint={entry.custom ? `your feed · contacts ${entry.host}` : entry.publisher}
              checked={series[entry.id] !== false}
              disabled={busy}
              onChange={(on) => setChosen({ ...series, [entry.id]: on })}
            />
            {entry.custom ? (
              <button type="button" className="button ghost small" disabled={busy} onClick={() => void act(() => api.removeFeed(entry.id))}>
                Remove
              </button>
            ) : null}
          </div>
        ))}
      </fieldset>
      <AddFeed disabled={busy} onAdded={() => void act(async () => undefined)} />
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
      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}
    </section>
  )
}
