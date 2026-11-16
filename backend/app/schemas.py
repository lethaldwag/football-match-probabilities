"""API response and request models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DISCLAIMER = (
    "Probabilities are statistical estimates from historical results. They are uncertain, can be "
    "wrong, and are not betting or financial advice."
)

PredictionState = Literal["ok", "stale", "unavailable", "missing"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SeasonOut(ORM):
    id: int
    start_year: int
    label: str


class CompetitionOut(ORM):
    id: int
    code: str
    name: str
    country: str | None
    kind: str
    seasons: list[SeasonOut]


class TeamOut(ORM):
    id: int
    name: str


class PredictionOut(BaseModel):
    market: str
    model: str
    model_version: str
    kind: Literal["live", "backtest"]
    state: Literal["ok", "stale", "unavailable"]
    reason: str | None
    probabilities: dict[str, float] | None
    expected_home_goals: float | None
    expected_away_goals: float | None
    training_matches: int
    explanation: dict | None
    generated_at: datetime
    data_cutoff: datetime


class MatchSummary(BaseModel):
    id: int
    competition_code: str
    season: str
    stage: str
    matchday: int | None
    kickoff_utc: datetime
    kickoff_precision: Literal["datetime", "date"]
    status: str
    home_team: TeamOut
    away_team: TeamOut
    home_goals: int | None
    away_goals: int | None
    data_note: str | None = Field(description="Data-quality warning for this match, if any")
    prediction_state: PredictionState
    predictions: list[PredictionOut] = Field(description="Primary model only; empty when missing")


class MatchPage(BaseModel):
    items: list[MatchSummary]
    total: int
    limit: int
    offset: int


class OddsOut(BaseModel):
    provider: str
    bookmaker: str
    market: str
    selection: str
    decimal_odds: float
    implied_probability: float = Field(description="1 / decimal odds; includes the bookmaker margin")
    timing: str
    captured_at: datetime | None = Field(description="Quote time if the provider states it, else null")
    fetched_at: datetime


class StatsOut(ORM):
    team_id: int
    provider: str
    fetched_at: datetime
    shots: int | None
    shots_on_target: int | None
    corners: int | None
    fouls: int | None
    yellow_cards: int | None
    red_cards: int | None


class FormMatch(BaseModel):
    match_id: int
    kickoff_utc: datetime
    opponent: str
    venue: Literal["H", "A"]
    goals_for: int
    goals_against: int
    result: Literal["W", "D", "L"]


class FormOut(BaseModel):
    team: TeamOut
    before: datetime
    matches: list[FormMatch]


class TeamFeatureOut(ORM):
    team_id: int
    kind: str
    data_cutoff: datetime
    matches_in_window: int
    form_matches: int
    form_points: int
    form_goals_for: int
    form_goals_against: int
    attack_strength: float | None
    defence_weakness: float | None


class ProvenanceOut(ORM):
    provider: str
    provider_match_ref: str
    fetched_at: datetime
    source_updated_at: datetime | None
    transform_notes: str | None


class MatchDetail(MatchSummary):
    all_predictions: list[PredictionOut]
    features: list[TeamFeatureOut]
    odds: list[OddsOut]
    statistics: list[StatsOut]
    home_form: FormOut
    away_form: FormOut
    provenance: list[ProvenanceOut]
    disclaimer: str = DISCLAIMER


class ImportRunOut(ORM):
    id: int
    provider: str
    competition_code: str
    season_start_year: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    records_seen: int
    matches_created: int
    matches_updated: int
    matches_unchanged: int
    warnings: list[str] | None
    error_message: str | None


class DataStatus(BaseModel):
    competition_code: str
    data_state: Literal["fresh", "stale", "never_imported"]
    data_stale_after_hours: int
    last_successful_import: ImportRunOut | None
    last_import_attempt: ImportRunOut | None
    latest_prediction_generated_at: datetime | None
    upcoming_matches: int
    latest_evaluation_at: datetime | None
    disclaimer: str = DISCLAIMER


class EvaluationOut(BaseModel):
    id: int
    competition_code: str
    created_at: datetime
    period_start: datetime
    period_end: datetime
    n_matches: int
    results: dict
    disclaimer: str = DISCLAIMER


class ImportRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    competition: str = Field(min_length=1, max_length=16)
    season: int = Field(ge=1990, le=2100, description="Season start year, e.g. 2025 for 2025-26")
