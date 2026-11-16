// All data in these tests is synthetic ("Team A" etc.); none of it is real football data.
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import App from '../App'
import type { DataStatus, Evaluation, MatchDetail, MatchSummary, Prediction } from '../api/types'

const prediction = (market: Prediction['market'], probabilities: Record<string, number>, extra: Partial<Prediction> = {}): Prediction => ({
  market,
  model: 'poisson-goals',
  model_version: '1.0',
  kind: 'live',
  state: 'ok',
  reason: null,
  probabilities,
  expected_home_goals: 1.62,
  expected_away_goals: 1.05,
  training_matches: 1480,
  explanation: { home_advantage: 1.21, notes: [] },
  generated_at: '2030-03-01T08:00:00Z',
  data_cutoff: '2030-03-01T08:00:00Z',
  ...extra,
})

const match: MatchSummary = {
  id: 7,
  competition_code: 'PL',
  season: '2029-30',
  stage: 'regular',
  matchday: null,
  kickoff_utc: '2030-03-02T15:00:00Z',
  kickoff_precision: 'datetime',
  status: 'scheduled',
  home_team: { id: 1, name: 'Team A' },
  away_team: { id: 2, name: 'Team B' },
  home_goals: null,
  away_goals: null,
  data_note: null,
  prediction_state: 'ok',
  predictions: [
    prediction('1X2', { home: 0.48, draw: 0.27, away: 0.25 }),
    prediction('OU25', { over: 0.55, under: 0.45 }),
    prediction('BTTS', { yes: 0.52, no: 0.48 }),
  ],
}

const status: DataStatus = {
  competition_code: 'PL',
  data_state: 'fresh',
  data_stale_after_hours: 48,
  last_successful_import: { id: 1, provider: 'football-data-uk', status: 'success', started_at: '2030-03-01T07:59:00Z', finished_at: '2030-03-01T08:00:00Z', error_message: null },
  last_import_attempt: null,
  latest_prediction_generated_at: '2030-03-01T08:00:00Z',
  upcoming_matches: 1,
  latest_evaluation_at: null,
  disclaimer: 'x',
}

const detail: MatchDetail = {
  ...match,
  all_predictions: match.predictions,
  features: [
    { team_id: 1, kind: 'live', data_cutoff: '2030-03-01T08:00:00Z', matches_in_window: 140, form_matches: 5, form_points: 10, form_goals_for: 9, form_goals_against: 4, attack_strength: 1.31, defence_weakness: 0.84 },
    { team_id: 2, kind: 'live', data_cutoff: '2030-03-01T08:00:00Z', matches_in_window: 140, form_matches: 5, form_points: 4, form_goals_for: 3, form_goals_against: 8, attack_strength: 0.9, defence_weakness: 1.12 },
  ],
  odds: [
    { provider: 'football-data-uk', bookmaker: 'Avg', market: '1X2', selection: 'home', decimal_odds: 2.1, implied_probability: 0.476, timing: 'pre_match', captured_at: null, fetched_at: '2030-03-01T08:00:00Z' },
  ],
  statistics: [],
  home_form: { team: match.home_team, before: match.kickoff_utc, matches: [{ match_id: 3, kickoff_utc: '2030-02-20T15:00:00Z', opponent: 'Team C', venue: 'H', goals_for: 2, goals_against: 0, result: 'W' }] },
  away_form: { team: match.away_team, before: match.kickoff_utc, matches: [] },
  provenance: [{ provider: 'football-data-uk', provider_match_ref: 'E0:2029:Team A:Team B', fetched_at: '2030-03-01T08:00:00Z', source_updated_at: null, transform_notes: null }],
  disclaimer: 'Probabilities are uncertain estimates and are not betting advice.',
}

const metrics = (n: number, log_loss: number) => ({ n, log_loss, brier: 0.6, accuracy: 0.5 })
const evaluation: Evaluation = {
  id: 1,
  competition_code: 'PL',
  created_at: '2030-03-01T08:00:00Z',
  period_start: '2027-08-14T14:00:00Z',
  period_end: '2030-02-25T20:00:00Z',
  n_matches: 860,
  disclaimer: 'Probabilities are uncertain estimates and are not betting advice.',
  results: {
    method: 'Walk-forward evaluation.',
    primary_model: 'poisson-goals',
    seasons: ['2027-28', '2028-29', '2029-30'],
    matches_without_primary_prediction: 0,
    models: {
      'poisson-goals': {
        version: '1.0', role: 'primary', description: '',
        markets: { '1X2': { overall: metrics(860, 1.0089), by_season: [{ season: '2027-28', ...metrics(380, 1.01) }], calibration: [{ bin_lower: 0.4, bin_upper: 0.5, mean_predicted: 0.45, observed_frequency: 0.47, count: 300 }] } },
      },
      'bookmaker-implied': { version: null, role: 'market benchmark', description: '', markets: { '1X2': { overall: metrics(860, 0.9951), by_season: [], calibration: [] } } },
    },
    betting_simulation: { available: true, assumptions: { stake: '1 unit per bet (flat)' }, matches_with_odds: 860, n_bets: 582, total_staked: 582, profit: -93.3, roi: -0.16, max_drawdown: 99.2, average_bookmaker_margin: 0.053 },
  },
}

type Routes = Record<string, { status?: number; body: unknown } | 'pending' | 'network-error'>

function mockApi(routes: Routes) {
  const fetchMock = vi.fn((input: string | URL | Request) => {
    const url = String(input)
    const key = Object.keys(routes).find((prefix) => url.includes(prefix))
    const route = key ? routes[key] : { status: 404, body: { error: { code: 'not_found', message: 'Not found', details: [] } } }
    if (route === 'pending') return new Promise<Response>(() => {})
    if (route === 'network-error') return Promise.reject(new TypeError('failed to fetch'))
    return Promise.resolve(new Response(JSON.stringify(route.body), { status: route.status ?? 200 }))
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const page = (items: MatchSummary[]) => ({ body: { items, total: items.length, limit: 20, offset: 0 } })
const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  )

describe('Dashboard', () => {
  it('shows a loading state while requests are pending', () => {
    mockApi({ '/status': 'pending', '/matches': 'pending' })
    renderAt('/')
    expect(screen.getAllByRole('status').length).toBeGreaterThan(0)
    expect(screen.getByRole('note')).toHaveTextContent(/not betting or financial advice/i)
  })

  it('renders upcoming matches with predictions, timestamps and data state', async () => {
    const fetchMock = mockApi({ '/status': { body: status }, '/matches': page([match]) })
    renderAt('/')
    expect(await screen.findByRole('link', { name: 'Team A v Team B' })).toHaveAttribute('href', '/matches/7')
    expect(screen.getByText('Home win 48% · Draw 27% · Away win 25%')).toBeInTheDocument()
    expect(screen.getByText('Over 2.5 55% · Under 2.5 45%')).toBeInTheDocument()
    expect(screen.getByText('Up to date')).toBeInTheDocument()
    expect(screen.getByText(/Data is up to date/)).toBeInTheDocument()
    expect(screen.getByText(/Model estimate generated/)).toBeInTheDocument()
    const matchesCall = fetchMock.mock.calls.map((c) => String(c[0])).find((u) => u.includes('/matches'))
    expect(matchesCall).toContain('status=scheduled')
  })

  it('says so when there are no upcoming fixtures instead of inventing any', async () => {
    mockApi({ '/status': { body: { ...status, upcoming_matches: 0 } }, '/matches': page([]) })
    renderAt('/')
    expect(await screen.findByText(/No upcoming fixtures have been imported/)).toBeInTheDocument()
    expect(screen.queryByRole('listitem')).not.toBeInTheDocument()
  })

  it('flags stale data, failed imports, stale and missing predictions in words', async () => {
    const stale = { ...match, id: 8, prediction_state: 'stale' as const }
    const missing = { ...match, id: 9, prediction_state: 'missing' as const, predictions: [], data_note: 'Kick-off has passed but no result has been imported yet.' }
    mockApi({
      '/status': { body: { ...status, data_state: 'stale', last_import_attempt: { ...status.last_successful_import!, status: 'failed', error_message: 'provider unreachable' } } },
      '/matches': page([stale, missing]),
    })
    renderAt('/')
    expect(await screen.findByText('Stale')).toBeInTheDocument()
    expect(screen.getByText('No prediction')).toBeInTheDocument()
    expect(screen.getByText('No prediction is available for this match.')).toBeInTheDocument()
    expect(screen.getByText(/Data is stale/)).toBeInTheDocument()
    expect(screen.getByText(/provider unreachable/)).toBeInTheDocument()
    expect(screen.getByText(/no result has been imported yet/)).toBeInTheDocument()
  })

  it('reports an empty database honestly', async () => {
    mockApi({ '/status': { body: { ...status, data_state: 'never_imported', last_successful_import: null, latest_prediction_generated_at: null } } })
    renderAt('/')
    expect(await screen.findByText(/No data has been imported yet\./)).toBeInTheDocument()
    expect(screen.getByText(/No matches to show/)).toBeInTheDocument()
  })

  it('shows an error with a retry button, and the API error message', async () => {
    mockApi({ '/status': 'network-error', '/matches': { status: 500, body: { error: { code: 'error', message: 'Internal server error', details: [] } } } })
    renderAt('/')
    const alerts = await screen.findAllByRole('alert')
    expect(alerts).toHaveLength(2)
    expect(screen.getByText('Internal server error')).toBeInTheDocument()
    expect(screen.getByText(/Could not reach the server/)).toBeInTheDocument()

    mockApi({ '/status': { body: status }, '/matches': page([match]) })
    await userEvent.click(within(alerts[1]).getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('link', { name: 'Team A v Team B' })).toBeInTheDocument()
  })

  it('switches to results and requests finished matches', async () => {
    const fetchMock = mockApi({ '/status': { body: status }, '/matches': page([{ ...match, status: 'finished', home_goals: 2, away_goals: 1, predictions: [{ ...match.predictions[0], kind: 'backtest' }] }]) })
    renderAt('/')
    await userEvent.click(await screen.findByRole('button', { name: 'Results' }))
    expect(await screen.findByRole('link', { name: 'Team A 2–1 Team B' })).toBeInTheDocument()
    expect(screen.getByText(/Backtest: produced after the match/)).toBeInTheDocument()
    expect(fetchMock.mock.calls.some((c) => String(c[0]).includes('status=finished'))).toBe(true)
  })
})

describe('Match detail', () => {
  it('shows probabilities, model inputs, odds with source, and the uncertainty notice', async () => {
    mockApi({ '/matches/7': { body: detail } })
    renderAt('/matches/7')
    expect(await screen.findByRole('heading', { name: 'Team A v Team B' })).toBeInTheDocument()
    expect(screen.getByRole('note')).toHaveTextContent(/uncertain estimates/)
    expect(screen.getByText('27%')).toBeInTheDocument()
    expect(screen.getByText(/Poisson goals model v1\.0/)).toBeInTheDocument()
    expect(screen.getByText('1480')).toBeInTheDocument()
    const inputs = screen.getByRole('table', { name: 'Model inputs by team' })
    expect(within(inputs).getByText('1.62')).toBeInTheDocument()
    expect(within(inputs).getByText('1.31')).toBeInTheDocument()
    const odds = screen.getByRole('table', { name: 'Odds: Match result' })
    expect(within(odds).getByText('Market average')).toBeInTheDocument()
    expect(within(odds).getByText('2.10')).toBeInTheDocument()
    expect(within(odds).getByText(/football-data-uk/)).toBeInTheDocument()
    expect(screen.getByText(/does not publish the exact time/)).toBeInTheDocument()
    // Missing data is stated, never filled in.
    expect(screen.getByText(/Both teams to score: no odds available/)).toBeInTheDocument()
    expect(screen.getByText(/Match statistics become available after/)).toBeInTheDocument()
    expect(screen.getByText('No earlier matches in the imported data.')).toBeInTheDocument()
    expect(screen.getByText('Won')).toBeInTheDocument()
  })

  it('explains an unavailable prediction and a missing one', async () => {
    const unavailable = match.predictions.map((p) => ({ ...p, state: 'unavailable' as const, probabilities: null, reason: 'only 40 past matches available' }))
    mockApi({ '/matches/7': { body: { ...detail, prediction_state: 'unavailable', predictions: unavailable, all_predictions: unavailable } } })
    renderAt('/matches/7')
    expect((await screen.findAllByText(/only 40 past matches available/)).length).toBe(3)
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    cleanupAndRender({ ...detail, prediction_state: 'missing', predictions: [], all_predictions: [], features: [] })
    expect(await screen.findByText(/No prediction has been generated for this match/)).toBeInTheDocument()
  })

  it('shows the API error for an unknown match', async () => {
    mockApi({ '/matches/999': { status: 404, body: { error: { code: 'not_found', message: 'Match 999 not found', details: [] } } } })
    renderAt('/matches/999')
    expect(await screen.findByRole('alert')).toHaveTextContent('Match 999 not found')
  })
})

function cleanupAndRender(body: MatchDetail) {
  document.body.innerHTML = ''
  mockApi({ '/matches/7': { body } })
  renderAt('/matches/7')
}

describe('Model performance', () => {
  it('shows period, sample size, metrics, calibration table and a separate betting section', async () => {
    mockApi({ '/evaluation/latest': { body: evaluation } })
    renderAt('/performance')
    expect(await screen.findByRole('heading', { name: 'Model performance' })).toBeInTheDocument()
    expect(screen.getByText('Matches evaluated:').nextSibling).toHaveTextContent('860')
    expect(screen.getByText(/2027-28, 2028-29, 2029-30/)).toBeInTheDocument()
    const quality = screen.getByRole('table', { name: 'Probability quality by model' })
    expect(within(quality).getAllByText('860')).toHaveLength(2)
    expect(within(quality).getByText('1.0089')).toBeInTheDocument()
    expect(within(quality).getByText('0.9951')).toBeInTheDocument()
    expect(within(quality).getByText('Bookmaker-implied (benchmark)')).toBeInTheDocument()
    const calibration = screen.getByRole('table', { name: 'Calibration by probability range (model)' })
    expect(within(calibration).getByText('300')).toBeInTheDocument()
    expect(within(calibration).getByText('47.0%')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Calibration chart/ })).toBeInTheDocument()

    expect(screen.getByRole('heading', { name: 'Simulated betting returns' })).toBeInTheDocument()
    expect(screen.getByText('−93.3 units')).toBeInTheDocument()
    expect(screen.getByText('−16.0%')).toBeInTheDocument()
    expect(screen.getByText(/The simulated strategy lost money/)).toBeInTheDocument()
    expect(screen.getByText(/Past results do not predict future results/)).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Both teams to score' }))
    expect(within(quality).getAllByText('Not available for this market')).toHaveLength(2)
    expect(screen.getByText('No calibration data for this market.')).toBeInTheDocument()
  })

  it('explains when no evaluation exists yet', async () => {
    mockApi({})
    renderAt('/performance')
    expect(await screen.findByText(/No evaluation has been run yet/)).toBeInTheDocument()
  })
})

describe('Wording', () => {
  it('never uses certainty language', async () => {
    mockApi({ '/status': { body: status }, '/matches': page([match]), '/evaluation/latest': { body: evaluation } })
    for (const path of ['/', '/performance']) {
      const view = renderAt(path)
      await screen.findAllByRole('heading')
      expect(view.container.textContent).not.toMatch(/guarantee|sure bet|\block\b|banker|certain win/i)
      view.unmount()
    }
  })
})
