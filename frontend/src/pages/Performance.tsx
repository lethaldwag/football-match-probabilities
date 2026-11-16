import { useState } from 'react'
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api/client'
import type { BettingSimulation, CalibrationBin, Evaluation, Market } from '../api/types'
import { Async, Card, Disclaimer, TableWrap, Td, Th } from '../components/ui'
import { dateTime, MARKET_LABELS, MODEL_NAMES, percent } from '../format'
import { useApi } from '../hooks/useApi'

const MARKETS: Market[] = ['1X2', 'OU25', 'BTTS']
const BENCHMARK = 'bookmaker-implied'
const SERIES = { model: '#2a78d6', benchmark: '#eb6834' }
const INK_2 = '#52514e'

function CalibrationChart({ model, benchmark }: { model: CalibrationBin[]; benchmark?: CalibrationBin[] }) {
  const tick = (v: number) => `${Math.round(v * 100)}%`
  return (
    <div className="h-72 w-full" role="img" aria-label="Calibration chart: predicted probability against observed frequency. The same values are in the table below.">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid stroke="#e1e0d9" />
          <XAxis
            type="number"
            dataKey="mean_predicted"
            domain={[0, 1]}
            ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
            tickFormatter={tick}
            stroke="#c3c2b7"
            tick={{ fill: INK_2, fontSize: 12 }}
            label={{ value: 'Predicted probability', position: 'insideBottom', offset: -14, fill: INK_2, fontSize: 12 }}
          />
          <YAxis
            type="number"
            domain={[0, 1]}
            ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
            tickFormatter={tick}
            stroke="#c3c2b7"
            tick={{ fill: INK_2, fontSize: 12 }}
            label={{ value: 'How often it happened', angle: -90, position: 'insideLeft', fill: INK_2, fontSize: 12, dy: 60 }}
          />
          <ReferenceLine
            segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]}
            stroke="#898781"
            strokeDasharray="4 4"
            label={{ value: 'Perfect calibration', position: 'insideBottomRight', fill: INK_2, fontSize: 11 }}
          />
          <Tooltip
            formatter={(value, name) => [percent(Number(value), 1), name]}
            labelFormatter={(label) => `Predicted ${percent(Number(label), 1)}`}
          />
          <Legend verticalAlign="top" height={28} formatter={(value) => <span style={{ color: INK_2 }}>{value}</span>} />
          <Line
            data={model}
            dataKey="observed_frequency"
            name="Model"
            stroke={SERIES.model}
            strokeWidth={2}
            dot={{ r: 4, fill: SERIES.model, stroke: '#fcfcfb', strokeWidth: 2 }}
            isAnimationActive={false}
          />
          {benchmark && benchmark.length > 0 && (
            <Line
              data={benchmark}
              dataKey="observed_frequency"
              name="Bookmaker-implied"
              stroke={SERIES.benchmark}
              strokeWidth={2}
              dot={{ r: 4, fill: SERIES.benchmark, stroke: '#fcfcfb', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

function BettingSection({ sim }: { sim: BettingSimulation }) {
  if (!sim.available) {
    return (
      <Card title="Simulated betting returns">
        <p className="text-sm text-ink-2">Not available: {sim.reason}</p>
      </Card>
    )
  }
  const units = (v: number | undefined) => (v == null ? 'n/a' : `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} units`)
  return (
    <Card
      title="Simulated betting returns"
      note="A separate question from probability quality above. This is a simulation on past matches under fixed assumptions. Past results do not predict future results, and nothing here is advice to bet."
    >
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {[
          ['Bets placed', `${sim.n_bets} of ${sim.matches_with_odds} matches`],
          ['Profit / loss', units(sim.profit)],
          ['Return on stakes', sim.roi == null ? 'n/a' : `${sim.roi >= 0 ? '+' : '−'}${percent(Math.abs(sim.roi), 1)}`],
          ['Largest drawdown', sim.max_drawdown == null ? 'n/a' : `${sim.max_drawdown.toFixed(1)} units`],
        ].map(([label, value]) => (
          <div key={label}>
            <dt className="text-ink-2">{label}</dt>
            <dd className="text-lg font-semibold tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {sim.roi != null && sim.roi < 0 && (
        <p className="mt-3 text-sm">
          <strong>The simulated strategy lost money.</strong> The model shows no betting edge over the bookmakers in this
          period.
        </p>
      )}
      <h3 className="mt-4 text-sm font-semibold">Assumptions</h3>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-ink-2">
        {Object.values(sim.assumptions ?? {}).map((text) => (
          <li key={text}>{text}</li>
        ))}
        {sim.average_bookmaker_margin != null && (
          <li>Average bookmaker margin in the odds used: {percent(sim.average_bookmaker_margin, 1)}.</li>
        )}
      </ul>
    </Card>
  )
}

export function PerformanceView({ evaluation }: { evaluation: Evaluation }) {
  const [market, setMarket] = useState<Market>('1X2')
  const { results } = evaluation
  const names = Object.keys(results.models)
  const primary = results.models[results.primary_model]
  const primaryMarket = primary?.markets[market]
  const benchmarkMarket = results.models[BENCHMARK]?.markets[market]
  const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold">Model performance</h1>
      <Disclaimer text={evaluation.disclaimer} />
      <Card title="Evaluation period">
        <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
          <div>
            <dt className="inline text-ink-2">Matches evaluated: </dt>
            <dd className="inline font-semibold">{evaluation.n_matches}</dd>
          </div>
          <div>
            <dt className="inline text-ink-2">Period: </dt>
            <dd className="inline">
              {date(evaluation.period_start)} to {date(evaluation.period_end)} ({results.seasons.join(', ')})
            </dd>
          </div>
          <div>
            <dt className="inline text-ink-2">Evaluation run: </dt>
            <dd className="inline">{dateTime(evaluation.created_at)}</dd>
          </div>
          <div>
            <dt className="inline text-ink-2">Matches the model could not predict: </dt>
            <dd className="inline">{results.matches_without_primary_prediction}</dd>
          </div>
        </dl>
        <p className="mt-2 text-sm text-ink-2">{results.method}</p>
      </Card>

      <div role="group" aria-label="Market" className="flex w-fit flex-wrap rounded-lg border border-axis p-0.5 text-sm">
        {MARKETS.map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={market === m}
            onClick={() => setMarket(m)}
            className={`rounded-md px-3 py-1.5 font-medium ${market === m ? 'bg-ink text-white' : 'text-ink-2'}`}
          >
            {MARKET_LABELS[m]}
          </button>
        ))}
      </div>

      <Card
        title={`Probability quality: ${MARKET_LABELS[market]}`}
        note="Log loss and Brier score measure how good the probabilities are (lower is better). Accuracy, how often the most likely outcome happened, is secondary."
      >
        <TableWrap label="Probability quality by model">
          <thead>
            <tr>
              <Th>Model</Th>
              <Th>Role</Th>
              <Th numeric>Matches</Th>
              <Th numeric>Log loss</Th>
              <Th numeric>Brier score</Th>
              <Th numeric>Accuracy</Th>
            </tr>
          </thead>
          <tbody>
            {names.map((name) => {
              const overall = results.models[name].markets[market]?.overall
              return (
                <tr key={name}>
                  <Td>{MODEL_NAMES[name] ?? name}</Td>
                  <Td>{results.models[name].role}</Td>
                  {overall ? (
                    <>
                      <Td numeric>{overall.n}</Td>
                      <Td numeric>{overall.log_loss.toFixed(4)}</Td>
                      <Td numeric>{overall.brier.toFixed(4)}</Td>
                      <Td numeric>{percent(overall.accuracy, 1)}</Td>
                    </>
                  ) : (
                    <td colSpan={4} className="border-b border-line px-2 py-1.5 text-right text-sm text-ink-2">
                      Not available for this market
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
        </TableWrap>
        <p className="mt-2 text-xs text-ink-2">
          The bookmaker-implied row is a benchmark from market-average odds, not a model of this site. A model that does
          not beat it has no demonstrated edge.
        </p>
      </Card>

      <Card
        title="Calibration"
        note="When the model says 60%, does it happen about 60% of the time? Points near the dashed line are well calibrated. Bins with few predictions are noisy."
      >
        {primaryMarket && primaryMarket.calibration.length > 0 ? (
          <>
            <CalibrationChart model={primaryMarket.calibration} benchmark={benchmarkMarket?.calibration} />
            <TableWrap label="Calibration by probability range (model)">
              <thead>
                <tr>
                  <Th>Predicted range</Th>
                  <Th numeric>Predictions</Th>
                  <Th numeric>Average predicted</Th>
                  <Th numeric>Observed</Th>
                </tr>
              </thead>
              <tbody>
                {primaryMarket.calibration.map((bin) => (
                  <tr key={bin.bin_lower}>
                    <Td>
                      {percent(bin.bin_lower)}–{percent(bin.bin_upper)}
                    </Td>
                    <Td numeric>{bin.count}</Td>
                    <Td numeric>{percent(bin.mean_predicted, 1)}</Td>
                    <Td numeric>{percent(bin.observed_frequency, 1)}</Td>
                  </tr>
                ))}
              </tbody>
            </TableWrap>
          </>
        ) : (
          <p className="text-sm text-ink-2">No calibration data for this market.</p>
        )}
      </Card>

      <Card title="By season" note="Model only. Small samples (such as a season in progress) give unstable numbers.">
        {primaryMarket && primaryMarket.by_season.length > 0 ? (
          <TableWrap label="Model performance by season">
            <thead>
              <tr>
                <Th>Season</Th>
                <Th numeric>Matches</Th>
                <Th numeric>Log loss</Th>
                <Th numeric>Brier score</Th>
                <Th numeric>Accuracy</Th>
              </tr>
            </thead>
            <tbody>
              {primaryMarket.by_season.map((row) => (
                <tr key={row.season}>
                  <Td>{row.season}</Td>
                  <Td numeric>{row.n}</Td>
                  <Td numeric>{row.log_loss.toFixed(4)}</Td>
                  <Td numeric>{row.brier.toFixed(4)}</Td>
                  <Td numeric>{percent(row.accuracy, 1)}</Td>
                </tr>
              ))}
            </tbody>
          </TableWrap>
        ) : (
          <p className="text-sm text-ink-2">No per-season results for this market.</p>
        )}
      </Card>

      <BettingSection sim={results.betting_simulation} />
    </div>
  )
}

export default function Performance() {
  const evaluation = useApi(() => api.evaluation('PL'), 'evaluation')
  if (evaluation.status === 'error' && evaluation.error.status === 404) {
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">Model performance</h1>
        <div className="rounded-lg border border-dashed border-axis p-6 text-center text-sm text-ink-2">
          No evaluation has been run yet. Import historical results and run the evaluation (see the README).
        </div>
      </div>
    )
  }
  return (
    <Async state={evaluation} reload={evaluation.reload}>
      {(data) => <PerformanceView evaluation={data} />}
    </Async>
  )
}
