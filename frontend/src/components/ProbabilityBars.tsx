import type { Prediction } from '../api/types'
import { percent, SELECTION_LABELS } from '../format'

/** One row per outcome: label, percentage in text, and a thin bar for magnitude. */
export function ProbabilityBars({ prediction }: { prediction: Prediction }) {
  if (prediction.state === 'unavailable' || !prediction.probabilities) {
    return (
      <p className="text-sm text-ink-2">
        Not available{prediction.reason ? `: ${prediction.reason}` : '.'}
      </p>
    )
  }
  return (
    <dl className="grid grid-cols-[auto_3.5rem_1fr] items-center gap-x-3 gap-y-1.5 text-sm">
      {Object.entries(prediction.probabilities).map(([selection, p]) => (
        <div key={selection} className="contents">
          <dt>{SELECTION_LABELS[selection] ?? selection}</dt>
          <dd className="text-right font-semibold tabular-nums">{percent(p)}</dd>
          <dd aria-hidden="true" className="h-2 rounded-sm bg-line">
            <div className="h-2 rounded-sm bg-series-1" style={{ width: percent(p, 1) }} />
          </dd>
        </div>
      ))}
    </dl>
  )
}

/** Compact one-line summary for lists. */
export function ProbabilityLine({ prediction }: { prediction: Prediction }) {
  if (prediction.state === 'unavailable' || !prediction.probabilities) return <span className="text-ink-2">Not available</span>
  return (
    <span className="tabular-nums">
      {Object.entries(prediction.probabilities)
        .map(([selection, p]) => `${SELECTION_LABELS[selection] ?? selection} ${percent(p)}`)
        .join(' · ')}
    </span>
  )
}
