import type { ApiError } from '../lib/api'

/**
 * An honest failure. It names what could not be reached and offers to try
 * again. It never substitutes invented content for the owner's own.
 */
export function Unavailable({ error, onRetry }: { error: ApiError; onRetry?: () => void }) {
  return (
    <div className="unavailable" role="alert">
      <h2>Not available</h2>
      <p>{error.message}</p>
      {error.kind === 'unreachable' ? (
        <p className="muted">
          This page is showing from the offline shell. Your saved work is on this Mac and is
          untouched.
        </p>
      ) : null}
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  )
}
