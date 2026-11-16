import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Form, Market, MatchDetail, Odds, Prediction, TeamFeature } from '../api/types'
import { ProbabilityBars, ProbabilityLine } from '../components/ProbabilityBars'
import { Async, Card, Disclaimer, StateBadge, TableWrap, Td, Th } from '../components/ui'
import { bookmakerName, dateTime, kickoff, MARKET_LABELS, MODEL_NAMES, percent, SELECTION_LABELS } from '../format'
import { useApi } from '../hooks/useApi'

const MARKETS: Market[] = ['1X2', 'OU25', 'BTTS']
const fixed = (value: number | null | undefined, digits = 2) => (value == null ? 'n/a' : value.toFixed(digits))

function Predictions({ match }: { match: MatchDetail }) {
  if (match.predictions.length === 0) {
    return (
      <Card title="Model probabilities">
        <p className="text-sm text-ink-2">
          No prediction has been generated for this match. Predictions exist for upcoming matches and for completed
          matches inside the evaluation period.
        </p>
      </Card>
    )
  }
  const first = match.predictions[0]
  return (
    <Card
      title="Model probabilities"
      note={
        first.kind === 'backtest'
          ? 'Backtest: produced after the match using only results known before the match day. It shows what the model would have estimated.'
          : 'Estimated before kick-off from past results. These are model estimates, not bookmaker odds.'
      }
    >
      <div className="grid gap-5 md:grid-cols-3">
        {match.predictions.map((p) => (
          <div key={p.market}>
            <h3 className="mb-2 text-sm font-semibold">{MARKET_LABELS[p.market]}</h3>
            <ProbabilityBars prediction={p} />
          </div>
        ))}
      </div>
      <dl className="mt-4 grid gap-x-6 gap-y-1 border-t border-line pt-3 text-sm text-ink-2 sm:grid-cols-2">
        <Fact label="Model">{`${MODEL_NAMES[first.model] ?? first.model} v${first.model_version}`}</Fact>
        <Fact label="Generated">{dateTime(first.generated_at)}</Fact>
        <Fact label="Uses results known before">{dateTime(first.data_cutoff)}</Fact>
        <Fact label="Past matches used">{String(first.training_matches)}</Fact>
      </dl>
    </Card>
  )
}

function Fact({ label, children }: { label: string; children: string }) {
  return (
    <div>
      <dt className="inline">{label}: </dt>
      <dd className="inline text-ink">{children}</dd>
    </div>
  )
}

function Inputs({ match }: { match: MatchDetail }) {
  const prediction = match.predictions.find((p) => p.state !== 'unavailable')
  if (!prediction || match.features.length === 0) return null
  const feature = (teamId: number): TeamFeature | undefined => match.features.find((f) => f.team_id === teamId)
  const rows = [
    { team: match.home_team, xg: prediction.expected_home_goals, f: feature(match.home_team.id) },
    { team: match.away_team, xg: prediction.expected_away_goals, f: feature(match.away_team.id) },
  ]
  return (
    <Card
      title="What the model used"
      note={`Team strengths are estimated from past results, with recent matches weighted more. 1.00 is an average team. Home advantage multiplies the home team's expected goals by ${fixed(prediction.explanation?.home_advantage)}. Injuries, line-ups and transfers are not known to the model.`}
    >
      <TableWrap label="Model inputs by team">
        <thead>
          <tr>
            <Th>Team</Th>
            <Th numeric>Expected goals</Th>
            <Th numeric>Attack strength</Th>
            <Th numeric>Goals conceded factor</Th>
            <Th numeric>Last {rows[0].f?.form_matches ?? 5}: points</Th>
            <Th numeric>Goals for–against</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ team, xg, f }) => (
            <tr key={team.id}>
              <Td>{team.name}</Td>
              <Td numeric>{fixed(xg)}</Td>
              <Td numeric>{fixed(f?.attack_strength)}</Td>
              <Td numeric>{fixed(f?.defence_weakness)}</Td>
              <Td numeric>{f ? f.form_points : 'n/a'}</Td>
              <Td numeric>{f ? `${f.form_goals_for}–${f.form_goals_against}` : 'n/a'}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
      <p className="mt-2 text-xs text-ink-2">
        Goals conceded factor: above 1.00 means the team concedes more than average. Recent form is shown for context;
        the model itself uses all results in its window.
      </p>
      {prediction.explanation?.notes?.map((note) => (
        <p key={note} className="mt-2 text-sm">
          Note: {note}
        </p>
      ))}
    </Card>
  )
}

function OtherModels({ predictions }: { predictions: Prediction[] }) {
  const others = predictions.filter((p) => p.model !== 'poisson-goals')
  if (others.length === 0) return null
  return (
    <Card title="Reference model" note="A deliberately simple baseline (the same probabilities for every match), kept for comparison.">
      <ul className="space-y-1 text-sm">
        {others.map((p) => (
          <li key={`${p.model}-${p.kind}-${p.market}`}>
            <span className="text-ink-2">
              {MARKET_LABELS[p.market]} ({p.kind}):
            </span>{' '}
            <ProbabilityLine prediction={p} />
          </li>
        ))}
      </ul>
    </Card>
  )
}

function OddsTable({ odds }: { odds: Odds[] }) {
  return (
    <Card
      title="Bookmaker odds"
      note="Prices from bookmakers as reported by the data provider. Implied probability is 1 ÷ odds and includes the bookmaker's margin, so the outcomes add up to more than 100%."
    >
      {MARKETS.map((market) => {
        const rows = odds.filter((o) => o.market === market)
        if (rows.length === 0) {
          return (
            <p key={market} className="mt-2 text-sm text-ink-2">
              {MARKET_LABELS[market]}: no odds available from the current data provider.
            </p>
          )
        }
        return (
          <div key={market} className="mt-3">
            <h3 className="text-sm font-semibold">{MARKET_LABELS[market]}</h3>
            <TableWrap label={`Odds: ${MARKET_LABELS[market]}`}>
              <thead>
                <tr>
                  <Th>Bookmaker</Th>
                  <Th>Price type</Th>
                  <Th>Outcome</Th>
                  <Th numeric>Decimal odds</Th>
                  <Th numeric>Implied</Th>
                  <Th>Source · downloaded</Th>
                </tr>
              </thead>
              <tbody>
                {rows.map((o) => (
                  <tr key={`${o.provider}-${o.bookmaker}-${o.timing}-${o.selection}`}>
                    <Td>{bookmakerName(o.bookmaker)}</Td>
                    <Td>{o.timing === 'closing' ? 'Closing' : 'Pre-match'}</Td>
                    <Td>{SELECTION_LABELS[o.selection] ?? o.selection}</Td>
                    <Td numeric>{o.decimal_odds.toFixed(2)}</Td>
                    <Td numeric>{percent(o.implied_probability, 1)}</Td>
                    <Td>
                      {o.provider} · {dateTime(o.fetched_at)}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
            <p className="mt-1 text-xs text-ink-2">
              {rows.some((o) => o.captured_at)
                ? `Quoted ${dateTime(rows.find((o) => o.captured_at)?.captured_at)}.`
                : 'The provider does not publish the exact time these prices were quoted.'}
            </p>
          </div>
        )
      })}
    </Card>
  )
}

function FormTable({ form }: { form: Form }) {
  const RESULT = { W: 'Won', D: 'Drew', L: 'Lost' }
  return (
    <div>
      <h3 className="text-sm font-semibold">{form.team.name}</h3>
      {form.matches.length === 0 ? (
        <p className="mt-1 text-sm text-ink-2">No earlier matches in the imported data.</p>
      ) : (
        <TableWrap label={`Recent matches: ${form.team.name}`}>
          <thead>
            <tr>
              <Th>Date</Th>
              <Th>Opponent</Th>
              <Th>Result</Th>
              <Th numeric>Score</Th>
            </tr>
          </thead>
          <tbody>
            {form.matches.map((m) => (
              <tr key={m.match_id}>
                <Td>{new Date(m.kickoff_utc).toLocaleDateString(undefined, { dateStyle: 'medium' })}</Td>
                <Td>
                  {m.opponent} ({m.venue === 'H' ? 'home' : 'away'})
                </Td>
                <Td>{RESULT[m.result]}</Td>
                <Td numeric>
                  {m.goals_for}–{m.goals_against}
                </Td>
              </tr>
            ))}
          </tbody>
        </TableWrap>
      )}
    </div>
  )
}

function Statistics({ match }: { match: MatchDetail }) {
  if (match.statistics.length === 0) {
    return (
      <Card title="Match statistics">
        <p className="text-sm text-ink-2">
          {match.status === 'finished'
            ? 'No match statistics are available from the data provider for this match.'
            : 'Match statistics become available after the match has been played.'}
        </p>
      </Card>
    )
  }
  const home = match.statistics.find((s) => s.team_id === match.home_team.id)
  const away = match.statistics.find((s) => s.team_id === match.away_team.id)
  const show = (value: number | null | undefined) => (value == null ? 'n/a' : value)
  const rows = [
    ['Shots', home?.shots, away?.shots],
    ['Shots on target', home?.shots_on_target, away?.shots_on_target],
    ['Corners', home?.corners, away?.corners],
    ['Fouls', home?.fouls, away?.fouls],
    ['Yellow cards', home?.yellow_cards, away?.yellow_cards],
    ['Red cards', home?.red_cards, away?.red_cards],
  ] as const
  const source = home ?? away
  return (
    <Card
      title="Match statistics"
      note={`Full-time statistics for this match only. Source: ${source?.provider}, downloaded ${dateTime(source?.fetched_at)}. Not used to predict this match.`}
    >
      <TableWrap label="Match statistics">
        <thead>
          <tr>
            <Th>Statistic</Th>
            <Th numeric>{match.home_team.name}</Th>
            <Th numeric>{match.away_team.name}</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, h, a]) => (
            <tr key={label}>
              <Td>{label}</Td>
              <Td numeric>{show(h)}</Td>
              <Td numeric>{show(a)}</Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Card>
  )
}

export function MatchDetailView({ match }: { match: MatchDetail }) {
  const finished = match.status === 'finished'
  return (
    <div className="space-y-4">
      <header>
        <p className="text-sm text-ink-2">
          {match.competition_code} · {match.season} · {kickoff(match.kickoff_utc, match.kickoff_precision)} ·{' '}
          {match.status.replace('_', ' ')}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-3">
          <h1 className="text-2xl font-semibold">
            {match.home_team.name} {finished ? `${match.home_goals}–${match.away_goals}` : 'v'} {match.away_team.name}
          </h1>
          <StateBadge state={match.prediction_state} />
        </div>
        {match.data_note && <p className="mt-2 text-sm">Note: {match.data_note}</p>}
      </header>
      <Disclaimer text={match.disclaimer} />
      <Predictions match={match} />
      <Inputs match={match} />
      <OddsTable odds={match.odds} />
      <Card
        title="Recent matches"
        note={`Last ${Math.max(match.home_form.matches.length, match.away_form.matches.length) || 5} league matches completed before this fixture.`}
      >
        <div className="grid gap-5 md:grid-cols-2">
          <FormTable form={match.home_form} />
          <FormTable form={match.away_form} />
        </div>
      </Card>
      <Statistics match={match} />
      <OtherModels predictions={match.all_predictions} />
      <Card title="Data sources">
        <ul className="space-y-1 text-sm text-ink-2">
          {match.provenance.map((p) => (
            <li key={`${p.provider}-${p.provider_match_ref}`}>
              <span className="text-ink">{p.provider}</span> · downloaded {dateTime(p.fetched_at)}
              {p.source_updated_at && ` · source updated ${dateTime(p.source_updated_at)}`}
              {p.transform_notes && ` · ${p.transform_notes}`}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

export default function MatchDetailPage() {
  const id = Number(useParams().id)
  const match = useApi(() => api.match(id), `match-${id}`)
  return (
    <div className="space-y-4">
      <Link to="/" className="text-sm">
        ← All matches
      </Link>
      <Async state={match} reload={match.reload}>
        {(data) => <MatchDetailView match={data} />}
      </Async>
    </div>
  )
}
