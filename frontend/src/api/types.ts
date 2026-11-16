// Types mirror the FastAPI response models (backend/app/schemas.py).

export type Market = '1X2' | 'OU25' | 'BTTS'
export type PredictionState = 'ok' | 'stale' | 'unavailable' | 'missing'

export interface Team {
  id: number
  name: string
}

export interface Prediction {
  market: Market
  model: string
  model_version: string
  kind: 'live' | 'backtest'
  state: 'ok' | 'stale' | 'unavailable'
  reason: string | null
  probabilities: Record<string, number> | null
  expected_home_goals: number | null
  expected_away_goals: number | null
  training_matches: number
  explanation: { home_advantage?: number; notes?: string[] } | null
  generated_at: string
  data_cutoff: string
}

export interface MatchSummary {
  id: number
  competition_code: string
  season: string
  stage: string
  matchday: number | null
  kickoff_utc: string
  kickoff_precision: 'datetime' | 'date'
  status: string
  home_team: Team
  away_team: Team
  home_goals: number | null
  away_goals: number | null
  data_note: string | null
  prediction_state: PredictionState
  predictions: Prediction[]
}

export interface MatchPage {
  items: MatchSummary[]
  total: number
  limit: number
  offset: number
}

export interface Odds {
  provider: string
  bookmaker: string
  market: Market
  selection: string
  decimal_odds: number
  implied_probability: number
  timing: 'pre_match' | 'closing'
  captured_at: string | null
  fetched_at: string
}

export interface TeamStats {
  team_id: number
  provider: string
  fetched_at: string
  shots: number | null
  shots_on_target: number | null
  corners: number | null
  fouls: number | null
  yellow_cards: number | null
  red_cards: number | null
}

export interface FormMatch {
  match_id: number
  kickoff_utc: string
  opponent: string
  venue: 'H' | 'A'
  goals_for: number
  goals_against: number
  result: 'W' | 'D' | 'L'
}

export interface Form {
  team: Team
  before: string
  matches: FormMatch[]
}

export interface TeamFeature {
  team_id: number
  kind: string
  data_cutoff: string
  matches_in_window: number
  form_matches: number
  form_points: number
  form_goals_for: number
  form_goals_against: number
  attack_strength: number | null
  defence_weakness: number | null
}

export interface Provenance {
  provider: string
  provider_match_ref: string
  fetched_at: string
  source_updated_at: string | null
  transform_notes: string | null
}

export interface MatchDetail extends MatchSummary {
  all_predictions: Prediction[]
  features: TeamFeature[]
  odds: Odds[]
  statistics: TeamStats[]
  home_form: Form
  away_form: Form
  provenance: Provenance[]
  disclaimer: string
}

export interface ImportRun {
  id: number
  provider: string
  status: string
  started_at: string
  finished_at: string | null
  error_message: string | null
}

export interface DataStatus {
  competition_code: string
  data_state: 'fresh' | 'stale' | 'never_imported'
  data_stale_after_hours: number
  last_successful_import: ImportRun | null
  last_import_attempt: ImportRun | null
  latest_prediction_generated_at: string | null
  upcoming_matches: number
  latest_evaluation_at: string | null
  disclaimer: string
}

export interface Metrics {
  n: number
  log_loss: number
  brier: number
  accuracy: number
}

export interface CalibrationBin {
  bin_lower: number
  bin_upper: number
  mean_predicted: number
  observed_frequency: number
  count: number
}

export interface MarketEvaluation {
  overall: Metrics | null
  by_season: (Metrics & { season: string })[]
  calibration: CalibrationBin[]
}

export interface ModelEvaluation {
  version: string | null
  role: string
  description: string
  markets: Partial<Record<Market, MarketEvaluation>>
}

export interface BettingSimulation {
  available: boolean
  reason?: string
  assumptions?: Record<string, string>
  matches_with_odds?: number
  n_bets?: number
  total_staked?: number
  profit?: number
  roi?: number | null
  max_drawdown?: number
  average_bookmaker_margin?: number | null
}

export interface Evaluation {
  id: number
  competition_code: string
  created_at: string
  period_start: string
  period_end: string
  n_matches: number
  disclaimer: string
  results: {
    method: string
    primary_model: string
    seasons: string[]
    matches_without_primary_prediction: number
    models: Record<string, ModelEvaluation>
    betting_simulation: BettingSimulation
  }
}
