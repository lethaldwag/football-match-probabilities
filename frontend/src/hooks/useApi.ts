import { useCallback, useEffect, useState } from 'react'
import { ApiError } from '../api/client'

export type AsyncState<T> =
  | { status: 'loading' }
  | { status: 'error'; error: ApiError }
  | { status: 'ready'; data: T }

/** Run an API call and track loading / error / ready. `key` re-runs the call when it changes. */
export function useApi<T>(load: () => Promise<T>, key: string): AsyncState<T> & { reload: () => void } {
  const [state, setState] = useState<AsyncState<T>>({ status: 'loading' })
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })
    load().then(
      (data) => !cancelled && setState({ status: 'ready', data }),
      (error: unknown) => {
        if (cancelled) return
        const apiError = error instanceof ApiError ? error : new ApiError(0, 'error', 'Unexpected error')
        setState({ status: 'error', error: apiError })
      },
    )
    return () => {
      cancelled = true
    }
    // `load` is intentionally not a dependency: callers describe the request with `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  return { ...state, reload }
}
