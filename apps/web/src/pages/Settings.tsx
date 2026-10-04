/**
 * Settings (ADR 0024): which tabs the app shows. Today and Settings stay, so
 * there is always a way back. The choice is kept on the Mac and follows you
 * to the phone through sync.
 */

import { useEffect, useState } from 'react'
import { Unavailable } from '../components/Unavailable'
import { ApiError, api, asApiError } from '../lib/api'
import { useLoad } from '../lib/useLoad'

export function Settings({ onSaved }: { onSaved?: (visible: string[]) => void }) {
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
    </div>
  )
}
