/**
 * What is being processed, and what has been (feedback of 6 October): files waiting in
 * the source folder, sources read in, and how far the encyclopedia has been built from
 * each, with the last scan's take and anything it turned away.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { ConstructionSource } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { Loading } from './Loading'

const STATE_LABEL: Record<ConstructionSource['state'], string> = {
  not_started: 'Not built yet',
  partly: 'Partly built',
  built: 'Built',
  unreadable: 'No text to build from'
}
const PAGE = 50

export function ConstructionProgress({ reloadToken = 0 }: { reloadToken?: number }) {
  const { result, reload } = useLoad(() => api.construction(), [reloadToken])
  const [filter, setFilter] = useState<'all' | ConstructionSource['state']>('all')
  const [query, setQuery] = useState('')
  const [shown, setShown] = useState(PAGE)

  // While files are waiting or a scan is running, look again every twenty seconds.
  const ready = result.state === 'ready' && result.value?.folder !== undefined && result.value?.sources !== undefined
  const busy =
    ready &&
    result.state === 'ready' &&
    (result.value.folder.waiting_count > 0 || result.value.folder.scanning || result.value.builder?.builder?.state === 'building')
  useEffect(() => {
    if (!busy) return undefined
    const timer = window.setInterval(reload, 20_000)
    return () => window.clearInterval(timer)
  }, [busy, reload])

  const items = useMemo(() => {
    if (result.state !== 'ready' || !ready) return []
    const words = query.trim().toLowerCase()
    return result.value.sources.items.filter(
      (item) => (filter === 'all' || item.state === filter) && (!words || `${item.filename} ${item.pile}`.toLowerCase().includes(words))
    )
  }, [result, filter, query])

  if (result.state === 'loading') return <Loading />
  if (result.state === 'failed' || !ready) return null
  const { folder, sources } = result.value
  const builder = result.value.builder
  const counts = sources.counts
  const total = Math.max(1, folder.waiting_count + sources.total)
  const share = (n: number) => `${(100 * n) / total}%`

  return (
    <section className="card" aria-labelledby="progress-heading">
      <h2 id="progress-heading">Progress</h2>
      <div className="progress-figures">
        <div>
          <strong>{folder.waiting_count}</strong>
          <span className="muted small">waiting to be read in{folder.scanning ? ' · reading now' : ''}</span>
        </div>
        <div>
          <strong>{sources.total}</strong>
          <span className="muted small">read in</span>
        </div>
        <div>
          <strong>{counts.built}</strong>
          <span className="muted small">built into the encyclopedia{counts.partly ? ` · ${counts.partly} partly` : ''}</span>
        </div>
      </div>
      <div className="progress-bar" role="img" aria-label={`${folder.waiting_count} waiting, ${counts.not_started} read in but not built, ${counts.partly} partly built, ${counts.built} built`}>
        <span className="seg built" style={{ width: share(counts.built) }} />
        <span className="seg partly" style={{ width: share(counts.partly) }} />
        <span className="seg read" style={{ width: share(counts.not_started + counts.unreadable) }} />
        <span className="seg waiting" style={{ width: share(folder.waiting_count) }} />
      </div>
      <p className="muted small">
        <span className="key built" /> built · <span className="key partly" /> partly · <span className="key read" /> read in, not built yet ·{' '}
        <span className="key waiting" /> waiting
      </p>
      {/* What the builder is doing, so a file not yet built always has a reason (feedback of 10 October). */}
      {builder ? (
        <p className="small builder-status" role="status">
          <strong>Building: </strong>
          {!builder.enabled ? 'off. Turn on building in the background below.' : builder.builder?.reason || 'starting.'}
          {builder.builder?.next_attempt_at && builder.builder.state !== 'building' ? ` Next try ${momentLabel(builder.builder.next_attempt_at)}.` : ''}
        </p>
      ) : null}
      {builder?.builder?.last_error && builder.builder.state === 'waiting' ? (
        <p className="muted small">Last problem: {builder.builder.last_error}</p>
      ) : null}
      {folder.last_scan ? (
        <p className="muted small">
          Last scan {momentLabel(folder.last_scan.at)}: {folder.last_scan.stored} read in
          {folder.last_scan.rejected.length ? `, ${folder.last_scan.rejected.length} turned away` : ''}
          {folder.last_scan.more_waiting ? '; more to come, eight at a time' : ''}.
        </p>
      ) : null}
      {folder.last_scan && folder.last_scan.rejected.length > 0 ? (
        <details className="support-details">
          <summary>Turned away ({folder.last_scan.rejected.length})</summary>
          <ul className="list small">
            {folder.last_scan.rejected.map((item) => (
              <li key={`${item.pile}/${item.filename}`}>
                <span className="title">{item.filename}</span> <span className="muted">· {item.pile} · {item.message}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {folder.waiting.length > 0 ? (
        <details className="support-details">
          <summary>Waiting in the source folder ({folder.waiting_count})</summary>
          <ul className="list small">
            {folder.waiting.map((item) => (
              <li key={`${item.pile}/${item.filename}`}>
                {item.filename} <span className="muted">· {item.pile}</span>
                {item.reason ? <span className="muted small item-reason">{item.reason}</span> : null}
              </li>
            ))}
          </ul>
          {folder.waiting_count > folder.waiting.length ? <p className="muted small">and {folder.waiting_count - folder.waiting.length} more.</p> : null}
        </details>
      ) : null}
      {folder.duplicates && folder.duplicates.length > 0 ? (
        <details className="support-details">
          <summary>Already here under another name ({folder.duplicates.length})</summary>
          <ul className="list small">
            {folder.duplicates.map((item) => (
              <li key={`${item.pile}/${item.filename}`}>
                {item.filename} <span className="muted">· {item.pile}</span>
                <span className="muted small item-reason">{item.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="muted small">PDF, PowerPoint (.pptx), Word (.docx), Markdown, text and pictures are read in and built.</p>
      <details className="support-details">
        <summary>Every source, and how far it is built ({sources.total})</summary>
        <div className="chips" role="group" aria-label="Show">
          {(['all', 'not_started', 'partly', 'built', 'unreadable'] as const).map((value) => (
            <button key={value} type="button" className={`chip${filter === value ? ' on' : ''}`} aria-pressed={filter === value} onClick={() => { setFilter(value); setShown(PAGE) }}>
              {value === 'all' ? 'All' : STATE_LABEL[value]}
            </button>
          ))}
        </div>
        <label className="field">
          <span>Find a file</span>
          <input type="search" value={query} onChange={(event) => { setQuery(event.target.value); setShown(PAGE) }} />
        </label>
        <table className="progress-table">
          <thead>
            <tr>
              <th scope="col">File</th>
              <th scope="col">Built</th>
              <th scope="col">Points</th>
            </tr>
          </thead>
          <tbody>
            {items.slice(0, shown).map((item) => (
              <tr key={item.id}>
                <th scope="row">
                  <span className="title">{item.filename}</span>
                  <span className="muted small"> · {item.pile}</span>
                  {item.reason ? <span className="muted small item-reason">{item.reason}</span> : null}
                </th>
                <td>{item.state === 'unreadable' ? STATE_LABEL.unreadable : `${item.percent}%`}</td>
                <td>{item.points}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {items.length > shown ? (
          <button type="button" className="button small" onClick={() => setShown((value) => value + PAGE)}>
            Show more ({items.length - shown} left)
          </button>
        ) : null}
      </details>
    </section>
  )
}
