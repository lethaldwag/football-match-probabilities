import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { DataStatus, MatchSummary } from '../api/types'
import { ProbabilityLine } from '../components/ProbabilityBars'
import { Async, Card, Disclaimer, StateBadge } from '../components/ui'
import { dateTime, kickoff, MARKET_LABELS } from '../format'
import { useApi } from '../hooks/useApi'

const COMPETITION = 'PL'
const PAGE_SIZE = 20
const today = () => new Date().toISOString().slice(0, 10)

const DATA_STATE_TEXT: Record<DataStatus['data_state'], string> = {
  fresh: 'Data is up to date',
  stale: 'Data is stale',
  never_imported: 'No data has been imported yet',
}

export function DataStatusPanel({ status }: { status: DataStatus }) {
  const failed = status.last_import_attempt?.status === 'failed'
  return (
    <Card>
      <p className="text-sm">
        <strong>{DATA_STATE_TEXT[status.data_state]}.</strong>{' '}
        {status.data_state === 'stale' &&
          `The last successful import is more than ${status.data_stale_after_hours} hours old, so fixtures, results and predictions may be out of date. `}
        {status.data_state === 'never_imported' && 'Run an import to load fixtures and results (see the README). '}
      </p>
      <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm text-ink-2 sm:grid-cols-2">
        <div>
          <dt className="inline">Last successful import: </dt>
          <dd className="inline text-ink">
            {status.last_successful_import
              ? `${dateTime(status.last_successful_import.finished_at)} (${status.last_successful_import.provider})`
              : 'never'}
          </dd>
        </div>
        <div>
          <dt className="inline">Predictions last generated: </dt>
          <dd className="inline text-ink">{dateTime(status.latest_prediction_generated_at)}</dd>
        </div>
      </dl>
      {failed && (
        <p className="mt-2 text-sm">
          <strong>Latest import attempt failed</strong> ({dateTime(status.last_import_attempt?.started_at)}):{' '}
          {status.last_import_attempt?.error_message ?? 'unknown error'}
        </p>
      )}
    </Card>
  )
}

function MatchCard({ match }: { match: MatchSummary }) {
  const finished = match.status === 'finished'
  const backtest = match.predictions.some((p) => p.kind === 'backtest')
  return (
    <li className="rounded-lg border border-line bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-xs text-ink-2">
            {kickoff(match.kickoff_utc, match.kickoff_precision)} · {match.status.replace('_', ' ')}
          </p>
          <h3 className="mt-0.5 text-base font-semibold">
            <Link to={`/matches/${match.id}`}>
              {match.home_team.name} {finished ? `${match.home_goals}–${match.away_goals}` : 'v'} {match.away_team.name}
            </Link>
          </h3>
        </div>
        <StateBadge state={match.prediction_state} />
      </div>
      {match.data_note && <p className="mt-2 text-sm">Note: {match.data_note}</p>}
      {match.predictions.length > 0 ? (
        <>
          <dl className="mt-3 space-y-1 text-sm">
            {match.predictions.map((p) => (
              <div key={p.market} className="sm:flex sm:gap-2">
                <dt className="text-ink-2 sm:w-56 sm:shrink-0">{MARKET_LABELS[p.market]}</dt>
                <dd>
                  <ProbabilityLine prediction={p} />
                </dd>
              </div>
            ))}
          </dl>
          <p className="mt-2 text-xs text-ink-2">
            {backtest
              ? 'Backtest: produced after the match, using only results known before the match day.'
              : `Model estimate generated ${dateTime(match.predictions[0].generated_at)}.`}
          </p>
        </>
      ) : (
        <p className="mt-3 text-sm text-ink-2">No prediction is available for this match.</p>
      )}
    </li>
  )
}

export default function Dashboard() {
  const [view, setView] = useState<'upcoming' | 'results'>('upcoming')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [offset, setOffset] = useState(0)

  const upcoming = view === 'upcoming'
  const query = {
    competition: COMPETITION,
    status: upcoming ? 'scheduled' : 'finished',
    date_from: dateFrom || (upcoming ? today() : undefined),
    date_to: dateTo || undefined,
    order: upcoming ? ('asc' as const) : ('desc' as const),
    limit: PAGE_SIZE,
    offset,
  }
  const status = useApi(() => api.status(COMPETITION), 'status')
  const matches = useApi(() => api.matches(query), JSON.stringify(query))
  const noData = status.status === 'ready' && status.data.data_state === 'never_imported'

  const change = (apply: () => void) => {
    apply()
    setOffset(0)
  }

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Premier League</h1>
      <Disclaimer />
      <Async state={status} reload={status.reload}>
        {(data) => <DataStatusPanel status={data} />}
      </Async>

      <div className="flex flex-wrap items-end gap-3">
        <div role="group" aria-label="Match list" className="flex rounded-lg border border-axis p-0.5 text-sm">
          {(['upcoming', 'results'] as const).map((v) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              onClick={() => change(() => setView(v))}
              className={`rounded-md px-3 py-1.5 font-medium ${view === v ? 'bg-ink text-white' : 'text-ink-2'}`}
            >
              {v === 'upcoming' ? 'Upcoming' : 'Results'}
            </button>
          ))}
        </div>
        <label className="text-sm text-ink-2">
          From
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => change(() => setDateFrom(e.target.value))}
            className="ml-2 rounded border border-axis bg-surface px-2 py-1 text-ink"
          />
        </label>
        <label className="text-sm text-ink-2">
          To
          <input
            type="date"
            value={dateTo}
            onChange={(e) => change(() => setDateTo(e.target.value))}
            className="ml-2 rounded border border-axis bg-surface px-2 py-1 text-ink"
          />
        </label>
      </div>

      {noData ? (
        <div className="rounded-lg border border-dashed border-axis p-6 text-center text-sm text-ink-2">
          No matches to show because no data has been imported yet.
        </div>
      ) : (
        <Async
          state={matches}
          reload={matches.reload}
          isEmpty={(page) => page.items.length === 0}
          empty={
            upcoming
              ? 'No upcoming fixtures have been imported for these dates. Nothing is shown rather than guessing.'
              : 'No completed matches found for these dates.'
          }
        >
          {(page) => (
            <>
              <ul className="space-y-3">
                {page.items.map((match) => (
                  <MatchCard key={match.id} match={match} />
                ))}
              </ul>
              <nav aria-label="Pagination" className="flex items-center justify-between pt-2 text-sm">
                <button
                  type="button"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                  className="rounded border border-axis px-3 py-1.5 disabled:opacity-40"
                >
                  Previous
                </button>
                <span className="text-ink-2">
                  {offset + 1}–{offset + page.items.length} of {page.total}
                </span>
                <button
                  type="button"
                  disabled={offset + PAGE_SIZE >= page.total}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                  className="rounded border border-axis px-3 py-1.5 disabled:opacity-40"
                >
                  Next
                </button>
              </nav>
            </>
          )}
        </Async>
      )}
    </div>
  )
}
