/**
 * Settings (ADR 0024): which tabs the app shows. Today and Settings stay, so
 * there is always a way back. The choice is kept on the Mac and follows you
 * to the phone through sync.
 */

import { useEffect, useState } from 'react'
import { LiteratureSettings } from '../components/LiteratureSettings'
import { PrivacyNote } from '../components/PrivacyNote'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { useLoad } from '../lib/useLoad'
import { CaseSeries, HubSettings } from './CaseSeries'
import { Model } from './Model'

/**
 * Everything that is set rather than used (ADR 0026): the tabs, the model
 * connection and its allowance, the literature watch, the Case Series hub, and
 * where your data lives.
 */
export function Settings({ onSaved, showModel = true }: { onSaved?: (visible: string[]) => void; showModel?: boolean }) {
  const { result, reload } = useLoad(() => api.preferences(), [])
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (result.state === 'ready') setChosen(result.value.visible_tabs)
  }, [result])

  if (result.state === 'loading') return <p className="muted">Reading from this Mac…</p>
  if (result.state === 'failed') return <Unavailable error={result.error} onRetry={reload} />
  const preferences = result.value
  const visible = chosen ?? preferences.visible_tabs

  const toggle = (name: string, on: boolean) => {
    setSaved(false)
    setChosen(on ? [...visible, name] : visible.filter((entry) => entry !== name))
  }

  const save = async () => {
    setBusy(true)
    setFailure(null)
    try {
      const next = await api.savePreferences(visible)
      setChosen(next.visible_tabs)
      setSaved(true)
      onSaved?.(next.visible_tabs)
    } catch (error) {
      setFailure(asApiError(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="stack">
      <section className="card" aria-labelledby="tabs-heading">
        <h2 id="tabs-heading">Tabs</h2>
        <p className="muted">Choose which sections the app shows. Today and Settings are always there.</p>
        <fieldset className="tab-picker">
          <legend className="visually-hidden">Sections to show</legend>
          {preferences.tabs.map((tab) => (
            <label key={tab.name} className="tab-option">
              <input
                type="checkbox"
                checked={tab.fixed || visible.includes(tab.name)}
                disabled={tab.fixed || busy}
                onChange={(event) => toggle(tab.name, event.target.checked)}
              />{' '}
              {tab.label}
              {tab.fixed ? <span className="muted small"> (always shown)</span> : null}
            </label>
          ))}
        </fieldset>
        <div className="actions">
          <button type="button" className="button primary" disabled={busy} onClick={() => void save()}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </div>
        {saved ? (
          <p className="ok" role="status">
            Saved. The tabs you chose are the ones shown, here and on your other devices.
          </p>
        ) : null}
        {failure ? (
          <p className="failure" role="alert">
            {failure.message}
          </p>
        ) : null}
      </section>

      <section className="card" aria-labelledby="model-settings-heading">
        <h2 id="model-settings-heading">Model and allowance</h2>
        {showModel ? (
          <Model />
        ) : (
          <p className="muted small">
            Here the model is the assistant you are talking to, in ChatGPT or Claude. Your Mac’s own connection, and its allowance, show in
            Settings on the Mac.
          </p>
        )}
      </section>

      <section className="card" aria-labelledby="literature-settings-heading">
        <h2 id="literature-settings-heading">Literature</h2>
        <LiteratureSettings onChecked={() => undefined} />
      </section>

      <section className="card" aria-labelledby="case-settings-heading">
        <h2 id="case-settings-heading">Case Series</h2>
        <p className="muted small">New cases from the series you follow appear on Today with their teaching points.</p>
        <HubSettings onChanged={() => undefined} />
        <details className="support-details">
          <summary>Browse every case</summary>
          <CaseSeries embedded />
        </details>
      </section>

      <details className="card toggle-card" aria-labelledby="privacy-settings-heading">
        <summary>
          <h2 id="privacy-settings-heading">Where your data lives</h2>
        </summary>
        <PrivacyNote />
      </details>
    </div>
  )
}
