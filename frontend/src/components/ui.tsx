import type { ReactNode } from 'react'
import type { PredictionState } from '../api/types'
import type { AsyncState } from '../hooks/useApi'

export function Card({ title, children, note }: { title?: string; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-4 sm:p-5">
      {title && <h2 className="text-base font-semibold">{title}</h2>}
      {note && <p className="mt-1 text-sm text-ink-2">{note}</p>}
      <div className={title ? 'mt-3' : ''}>{children}</div>
    </section>
  )
}

/** The one place loading, error and empty states are rendered. */
export function Async<T>({
  state,
  reload,
  isEmpty,
  empty,
  children,
}: {
  state: AsyncState<T>
  reload?: () => void
  isEmpty?: (data: T) => boolean
  empty?: ReactNode
  children: (data: T) => ReactNode
}) {
  if (state.status === 'loading') {
    return (
      <p role="status" className="py-8 text-center text-sm text-ink-2">
        Loading…
      </p>
    )
  }
  if (state.status === 'error') {
    return (
      <div role="alert" className="rounded-lg border border-line bg-surface p-4 text-sm">
        <p className="font-semibold">Something went wrong</p>
        <p className="mt-1 text-ink-2">{state.error.message}</p>
        {reload && (
          <button type="button" onClick={reload} className="mt-3 rounded border border-axis px-3 py-1.5 font-medium">
            Try again
          </button>
        )}
      </div>
    )
  }
  if (isEmpty?.(state.data)) {
    return <div className="rounded-lg border border-dashed border-axis p-6 text-center text-sm text-ink-2">{empty}</div>
  }
  return <>{children(state.data)}</>
}

const STATE_TEXT: Record<PredictionState, { icon: string; label: string; title: string }> = {
  ok: { icon: '✓', label: 'Up to date', title: 'Prediction generated from current data' },
  stale: { icon: '!', label: 'Stale', title: 'This prediction is old and may not reflect the latest results' },
  unavailable: { icon: '–', label: 'Unavailable', title: 'The model could not produce an estimate' },
  missing: { icon: '–', label: 'No prediction', title: 'No prediction has been generated for this match' },
}

/** State is always written out in words; the icon and border are secondary cues. */
export function StateBadge({ state }: { state: PredictionState }) {
  const text = STATE_TEXT[state]
  return (
    <span
      title={text.title}
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${
        state === 'ok' ? 'border-line text-ink-2' : 'border-ink text-ink'
      }`}
    >
      <span aria-hidden="true">{text.icon}</span>
      {text.label}
    </span>
  )
}

export function Disclaimer({ text }: { text?: string }) {
  return (
    <p role="note" className="rounded-lg border border-axis bg-surface px-4 py-3 text-sm text-ink-2">
      <strong className="text-ink">Estimates, not certainties.</strong>{' '}
      {text ??
        'Probabilities are statistical estimates from historical results. They are uncertain, can be wrong, and are not betting or financial advice.'}
    </p>
  )
}

export function Th({ children, numeric }: { children?: ReactNode; numeric?: boolean }) {
  return (
    <th scope="col" className={`border-b border-axis px-2 py-1.5 text-xs font-medium text-ink-2 ${numeric ? 'text-right' : 'text-left'}`}>
      {children}
    </th>
  )
}

export function Td({ children, numeric }: { children?: ReactNode; numeric?: boolean }) {
  return <td className={`border-b border-line px-2 py-1.5 text-sm ${numeric ? 'text-right' : 'text-left'}`}>{children}</td>
}

export function TableWrap({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div className="overflow-x-auto">
      <table aria-label={label} className="w-full border-collapse">
        {children}
      </table>
    </div>
  )
}
