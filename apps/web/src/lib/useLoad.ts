/**
 * Loading state with three honest outcomes: loading, loaded, or a named
 * failure. There is no fourth state in which something plausible is shown
 * instead (ADR 0001).
 */

import { useCallback, useEffect, useState } from 'react'
import { ApiError } from './api'

export type Loaded<T> =
  | { state: 'loading' }
  | { state: 'ready'; value: T }
  | { state: 'failed'; error: ApiError }

export function useLoad<T>(load: () => Promise<T>, deps: unknown[] = []): {
  result: Loaded<T>
  reload: () => void
} {
  const [result, setResult] = useState<Loaded<T>>({ state: 'loading' })
  const [nonce, setNonce] = useState(0)

  const reload = useCallback(() => setNonce((value) => value + 1), [])

  useEffect(() => {
    let live = true
    setResult({ state: 'loading' })
    load()
      .then((value) => {
        if (live) setResult({ state: 'ready', value })
      })
      .catch((error: unknown) => {
        if (!live) return
        setResult({
          state: 'failed',
          error:
            error instanceof ApiError
              ? error
              : new ApiError('server', 'Something went wrong on this machine.')
        })
      })
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce, ...deps])

  return { result, reload }
}
