/**
 * Loading state with three honest outcomes: loading, loaded, or a named
 * failure. There is no fourth state in which something plausible is shown
 * instead (ADR 0001).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
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

  // A reload of the same thing keeps what is on screen until the fresh value arrives:
  // blanking it to "loading" every few seconds unmounted whole pages, and stopped a
  // podcast mid-play while another episode was being written. Loading something
  // different (new dependencies) still says it is loading.
  const lastDeps = useRef<unknown[] | null>(null)
  useEffect(() => {
    let live = true
    const sameThing =
      lastDeps.current !== null && lastDeps.current.length === deps.length && lastDeps.current.every((value, index) => Object.is(value, deps[index]))
    lastDeps.current = deps
    if (!sameThing) setResult({ state: 'loading' })
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
