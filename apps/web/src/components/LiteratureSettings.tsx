/**
 * Which topics the literature check watches, and how often.
 *
 * A topic is a few public words — the same words you would type into a search
 * box. Nothing of yours goes with them, and the disclosure above the button
 * says so before the button is pressed.
 */

import { useState } from 'react'
import { ApiError, api, asApiError } from '../lib/api'
import { momentLabel } from '../lib/format'
import type { CheckReport, LiteratureSettings as Settings, Topic } from '../lib/types'
import { useLoad } from '../lib/useLoad'
import { LITERATURE_DISCLOSURE, TransmissionDisclosure } from './TransmissionDisclosure'
import { Unavailable } from './Unavailable'
import { PhiWarning } from './PhiWarning'

function TopicRow({ topic, onChanged }: { topic: Topic; onChanged: () => void }) {
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)

  const act = async (action: () => Promise<unknown>) => {
    setBusy(true)
    setFailure(null)
    try {
      await action()
      onChanged()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li>
      <span className="title">{topic.label}</span>
      <span className="muted small"> · {topic.query}</span>
      <p className="muted small">
        Last checked {momentLabel(topic.last_checked_at)}
        {topic.last_status ? ` · ${topic.last_status}` : null}
      </p>
      {topic.last_failure ? (
        <p className="warn small">
          {topic.last_failure}
          {topic.consecutive_failures > 1
            ? ` (${topic.consecutive_failures} checks in a row have failed)`
            : null}
        </p>
      ) : null}
      <div className="actions">
        <button
          type="button"
          className="button ghost small"
          disabled={busy}
          onClick={() =>
            void act(() => api.updateLiteratureTopic(topic.id, { enabled: !topic.enabled }))
          }
        >
          {topic.enabled ? 'Stop watching' : 'Watch again'}
        </button>
        <button
          type="button"
          className="button ghost small"
          disabled={busy}
          onClick={() => void act(() => api.deleteLiteratureTopic(topic.id))}
        >
          Delete
        </button>
      </div>
      {failure ? (
        <p className="failure" role="alert">
          {failure.message} Refresh to confirm this topic’s state before retrying.
        </p>
      ) : null}
    </li>
  )
}

export function LiteratureSettings({ onChecked }: { onChecked: () => void }) {
  const [token, setToken] = useState(0)
  const topics = useLoad(() => api.literatureTopics(), [token])
  const settings = useLoad(() => api.literatureSettings(), [token])
  const suggestions = useLoad(() => api.literatureSuggestions(), [token])
  const [label, setLabel] = useState('')
  const [queryWords, setQueryWords] = useState('')
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [report, setReport] = useState<CheckReport | null>(null)
  const [action, setAction] = useState('')

  const refresh = () => setToken((value) => value + 1)

  const add = async () => {
    if (label.trim() === '' || queryWords.trim() === '' || busy) return
    setBusy(true)
    setFailure(null)
    try {
      await api.createLiteratureTopic({ label: label.trim(), query: queryWords.trim() })
      setLabel('')
      setQueryWords('')
      refresh()
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const check = async () => {
    setBusy(true)
    setAction('check')
    setFailure(null)
    try {
      setReport(await api.literatureCheck())
      refresh()
      onChecked()
    } catch (error) {
      setReport(null)
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  const weekly = async (enabled: boolean) => {
    setBusy(true)
    setFailure(null)
    setAction('weekly')
    try {
      await api.saveLiteratureSettings({ weekly_enabled: enabled, interval_hours: 168 })
      refresh()
    } catch (error) {
      setFailure(asApiError(error))
      refresh()
    } finally { setBusy(false) }
  }

  const watch = async (topic: string, query: string) => {
    setBusy(true)
    setAction('watch')
    setFailure(null)
    try {
      await api.createLiteratureTopic({ label: topic, query })
      refresh()
    } catch (error) { setFailure(asApiError(error)) }
    finally { setBusy(false) }
  }

  const current: Settings | null = settings.result.state === 'ready' ? settings.result.value : null

  return (
    <section className="card" id="literature-settings" aria-labelledby="literature-settings-heading">
      <h2 id="literature-settings-heading">Literature settings</h2>

      {current ? (
        <p className="body">
          {current.weekly_enabled
            ? 'Weekly checks are enabled and run only while Vademecum is running.'
            : 'Automatic checks are off. You can still check by hand below.'}
          {!current.enabled ? ' The literature provider is unavailable.' : null}
          {current.running ? ' The weekly watcher is active.' : null}
        </p>
      ) : null}

      <TransmissionDisclosure disclosure={LITERATURE_DISCLOSURE} />

      {current ? (
        <label className="field inline">
          <input type="checkbox" checked={current.weekly_enabled} disabled={busy || !current.enabled}
            onChange={(event) => void weekly(event.target.checked)} />
          <span>Check weekly while Vademecum is running</span>
        </label>
      ) : null}
      {settings.result.state === 'failed' ? <Unavailable error={settings.result.error} onRetry={refresh} /> : null}

      <div className="actions">
        <button type="button" className="button" disabled={busy} onClick={() => void check()}>
          {busy && action === 'check' ? 'Checking…' : 'Check for new literature now'}
        </button>
      </div>

      {report ? (
        <div role="status">
          <p className="body">{report.message || `Literature check: ${report.status}.`}
            {report.unread === null ? '' : ` ${report.unread} unread updates.`}</p>
          {report.outcomes.length > 0 ? <ul className="list small">{report.outcomes.map((outcome, index) => <li key={index}>{outcome}</li>)}</ul> : null}
        </div>
      ) : null}

      {failure ? (
        <p className="failure" role="alert">
          {failure.message}
        </p>
      ) : null}

      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void add()
        }}
      >
        <label className="field">
          <span>Topic name</span>
          <input
            type="text"
            value={label}
            maxLength={120}
            placeholder="Infective endocarditis"
            onChange={(event) => setLabel(event.target.value)}
          />
        </label>
        <label className="field">
          <span>Search words</span>
          <input
            type="text"
            value={queryWords}
            maxLength={180}
            placeholder="infective endocarditis oral antibiotics"
            onChange={(event) => setQueryWords(event.target.value)}
          />
        </label>
        <PhiWarning />
        <button
          type="submit"
          className="button primary"
          disabled={label.trim() === '' || queryWords.trim() === '' || busy}
        >
          Add topic
        </button>
      </form>

      {suggestions.result.state === 'ready' && suggestions.result.value.length > 0 ? (
        <section aria-label="Suggested watch topics">
          <h3>Suggested watch topics</h3>
          <p className="muted small">From topics in your learning material. Review the public search words before choosing Watch. Adding a topic does not enable weekly checks.</p>
          <ul className="list">{suggestions.result.value.map((suggestion) => (
            <li key={suggestion.topic}>
              <span className="title">{suggestion.topic}</span>
              <p className="muted small">Search words: {suggestion.query} · {suggestion.point_count} learning points</p>
              <button type="button" className="button small" disabled={busy || suggestion.already_watched}
                onClick={() => void watch(suggestion.topic, suggestion.query)}>
                {suggestion.already_watched ? `Watching ${suggestion.topic}` : `Watch ${suggestion.topic}`}
              </button>
            </li>
          ))}</ul>
        </section>
      ) : null}
      {suggestions.result.state === 'failed' ? <Unavailable error={suggestions.result.error} onRetry={refresh} /> : null}

      {topics.result.state === 'loading' ? <p className="muted">Reading…</p> : null}
      {topics.result.state === 'failed' ? (
        <Unavailable error={topics.result.error} onRetry={refresh} />
      ) : null}
      {topics.result.state === 'ready' ? (
        topics.result.value.length === 0 ? (
          <p className="muted">
            No topics yet. Add one above and the check will have something to look for.
          </p>
        ) : (
          <ul className="list">
            {topics.result.value.map((topic) => (
              <TopicRow key={topic.id} topic={topic} onChanged={refresh} />
            ))}
          </ul>
        )
      ) : null}
    </section>
  )
}
