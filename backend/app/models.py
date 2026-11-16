"""Relational schema. Nothing here is specific to one competition."""

from datetime import datetime, timedelta

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow

MATCH_STATUSES = ("scheduled", "in_play", "finished", "postponed", "cancelled")
MARKETS = {"1X2": ("home", "draw", "away"), "OU25": ("over", "under"), "BTTS": ("yes", "no")}
ODDS_TIMINGS = ("pre_match", "closing")
PREDICTION_KINDS = ("live", "backtest")


def _in(column: str, values) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16), default="league")  # league | cup

    seasons: Mapped[list["Season"]] = relationship(back_populates="competition", order_by="Season.start_year")

    __table_args__ = (CheckConstraint("kind IN ('league', 'cup')", name="kind_valid"),)


class Season(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    start_year: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(16))  # e.g. "2025-26"

    competition: Mapped[Competition] = relationship(back_populates="seasons")

    __table_args__ = (UniqueConstraint("competition_id", "start_year"),)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    # Normalised key used to recognise the same club across providers.
    match_key: Mapped[str] = mapped_column(String(100), unique=True)


class TeamAlias(Base):
    """The name a provider uses for a team."""

    __tablename__ = "team_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    provider: Mapped[str] = mapped_column(String(40))
    provider_name: Mapped[str] = mapped_column(String(100))

    __table_args__ = (UniqueConstraint("provider", "provider_name"),)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"))
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    stage: Mapped[str] = mapped_column(String(40), default="regular")
    matchday: Mapped[int | None] = mapped_column(Integer)
    kickoff_utc: Mapped[datetime] = mapped_column(UTCDateTime)
    # "date" when the provider gave only a calendar date (kickoff_utc is then midnight UTC).
    kickoff_precision: Mapped[str] = mapped_column(String(8), default="datetime")
    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    competition: Mapped[Competition] = relationship()
    season: Mapped[Season] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])

    __table_args__ = (
        UniqueConstraint("season_id", "home_team_id", "away_team_id", "stage", name="uq_matches_fixture"),
        CheckConstraint("home_team_id <> away_team_id", name="distinct_teams"),
        CheckConstraint(_in("status", MATCH_STATUSES), name="status_valid"),
        CheckConstraint("kickoff_precision IN ('datetime', 'date')", name="precision_valid"),
        CheckConstraint("home_goals IS NULL OR home_goals >= 0", name="home_goals_nonneg"),
        CheckConstraint("away_goals IS NULL OR away_goals >= 0", name="away_goals_nonneg"),
        CheckConstraint(
            "status <> 'finished' OR (home_goals IS NOT NULL AND away_goals IS NOT NULL)",
            name="finished_has_score",
        ),
        Index("ix_matches_competition_kickoff", "competition_id", "kickoff_utc"),
        Index("ix_matches_status_kickoff", "status", "kickoff_utc"),
    )

    @property
    def result_known_at(self) -> datetime:
        return result_known_at(self.kickoff_utc, self.kickoff_precision)


def result_known_at(kickoff: datetime, precision: str) -> datetime:
    """Earliest time at which we treat a match result as known.

    Conservative on purpose: 3 hours after kickoff, or the end of the day when only a date is known.
    """
    return kickoff + (timedelta(hours=3) if precision == "datetime" else timedelta(days=1))


class MatchProviderRef(Base):
    """Provenance: which provider record a match came from, and the raw payload."""

    __tablename__ = "match_provider_refs"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_match_ref: Mapped[str] = mapped_column(String(200))
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    source_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    transform_notes: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (UniqueConstraint("provider", "provider_match_ref"),)


class MatchStatistics(Base):
    """Post-match statistics for one team in one match. Never an input for that match's own prediction."""

    __tablename__ = "match_statistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    provider: Mapped[str] = mapped_column(String(40))
    shots: Mapped[int | None] = mapped_column(Integer)
    shots_on_target: Mapped[int | None] = mapped_column(Integer)
    corners: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int | None] = mapped_column(Integer)
    yellow_cards: Mapped[int | None] = mapped_column(Integer)
    red_cards: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (UniqueConstraint("match_id", "team_id", "provider"),)


class OddsSnapshot(Base):
    """One bookmaker price as reported by a data provider.

    `captured_at` is the time the price was quoted, when the provider states it (often it does not).
    `fetched_at` is when we downloaded it. `timing` says whether the provider labels the price as
    pre-match or closing. A new row is added only when the price changes.
    """

    __tablename__ = "odds_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(40))
    bookmaker: Mapped[str] = mapped_column(String(40))
    market: Mapped[str] = mapped_column(String(8))
    selection: Mapped[str] = mapped_column(String(8))
    decimal_odds: Mapped[float] = mapped_column(Float)
    timing: Mapped[str] = mapped_column(String(12))
    captured_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint(
            "match_id", "provider", "bookmaker", "market", "selection", "timing", "fetched_at",
            name="uq_odds_snapshot",
        ),
        CheckConstraint("decimal_odds > 1", name="odds_gt_one"),
        CheckConstraint(_in("market", MARKETS), name="market_valid"),
        CheckConstraint(_in("timing", ODDS_TIMINGS), name="timing_valid"),
        Index("ix_odds_match_market", "match_id", "market"),
    )


class ModelVersion(Base):
    __tablename__ = "model_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("name", "version"),)


class TeamFeature(Base):
    """Derived pre-match features for one team in one match, computed at `data_cutoff`."""

    __tablename__ = "team_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
    kind: Mapped[str] = mapped_column(String(10))
    data_cutoff: Mapped[datetime] = mapped_column(UTCDateTime)
    matches_in_window: Mapped[int] = mapped_column(Integer)  # team's matches in the training window
    form_matches: Mapped[int] = mapped_column(Integer)  # how many recent matches the form covers (<= 5)
    form_points: Mapped[int] = mapped_column(Integer)
    form_goals_for: Mapped[int] = mapped_column(Integer)
    form_goals_against: Mapped[int] = mapped_column(Integer)
    # Multipliers relative to an average team (1.0); NULL when the model could not be fitted.
    attack_strength: Mapped[float | None] = mapped_column(Float)
    defence_weakness: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint("match_id", "team_id", "kind"),
        CheckConstraint(_in("kind", PREDICTION_KINDS), name="kind_valid"),
    )


class Prediction(Base):
    """A stored probability estimate for one market of one match.

    kind = "live": generated before kickoff with the data available then; frozen at kickoff.
    kind = "backtest": generated retrospectively, using only data known before the match day.
    """

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id", ondelete="CASCADE"), index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    market: Mapped[str] = mapped_column(String(8))
    kind: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))  # ok | unavailable
    reason: Mapped[str | None] = mapped_column(Text)  # why unavailable
    probabilities: Mapped[dict | None] = mapped_column(JSON)  # {selection: probability}
    expected_home_goals: Mapped[float | None] = mapped_column(Float)
    expected_away_goals: Mapped[float | None] = mapped_column(Float)
    training_matches: Mapped[int] = mapped_column(Integer, default=0)
    explanation: Mapped[dict | None] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    data_cutoff: Mapped[datetime] = mapped_column(UTCDateTime)

    model_version: Mapped[ModelVersion] = relationship()

    __table_args__ = (
        UniqueConstraint("match_id", "model_version_id", "market", "kind", name="uq_predictions_slot"),
        CheckConstraint(_in("market", MARKETS), name="market_valid"),
        CheckConstraint(_in("kind", PREDICTION_KINDS), name="kind_valid"),
        CheckConstraint("status IN ('ok', 'unavailable')", name="status_valid"),
        CheckConstraint("status <> 'ok' OR probabilities IS NOT NULL", name="ok_has_probabilities"),
    )


class ImportRun(Base):
    __tablename__ = "import_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    competition_code: Mapped[str] = mapped_column(String(16))
    season_start_year: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(10), default="running")
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    matches_created: Mapped[int] = mapped_column(Integer, default=0)
    matches_updated: Mapped[int] = mapped_column(Integer, default=0)
    matches_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="status_valid"),
        Index("ix_import_runs_started", "started_at"),
    )


class EvaluationRun(Base):
    """Result of one chronological (walk-forward) evaluation."""

    __tablename__ = "evaluation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competitions.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    period_start: Mapped[datetime] = mapped_column(UTCDateTime)
    period_end: Mapped[datetime] = mapped_column(UTCDateTime)
    n_matches: Mapped[int] = mapped_column(Integer)
    results: Mapped[dict] = mapped_column(JSON)
