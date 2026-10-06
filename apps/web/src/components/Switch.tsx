/**
 * An on/off switch with its words beside it (feedback of 5 October): a bare
 * checkbox floating above its label read as an unexplained blue mark. The
 * switch's accessible name is the label, and it says On or Off as well.
 */

import type { ReactNode } from 'react'

export function Switch({
  label,
  checked,
  onChange,
  disabled = false,
  hint
}: {
  label: string
  checked: boolean
  onChange: (on: boolean) => void
  disabled?: boolean
  hint?: ReactNode
}) {
  return (
    <label className={`switch-row${disabled ? ' disabled' : ''}`}>
      <span className="switch-label">
        {label}
        {hint ? <span className="muted small switch-hint">{hint}</span> : null}
      </span>
      <span className="switch">
        <input
          type="checkbox"
          role="switch"
          aria-label={label}
          checked={checked}
          disabled={disabled}
          onChange={(event) => onChange(event.target.checked)}
        />
        <span className="switch-track" aria-hidden="true" />
        <span className="switch-text" aria-hidden="true">
          {checked ? 'On' : 'Off'}
        </span>
      </span>
    </label>
  )
}
