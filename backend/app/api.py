"""Versioned public API (/api/v1) and protected admin routes."""

import secrets
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings, get_settings
from app.db import get_session, utcnow
from app.ingest import run_import
from app.modeling.predict import PRIMARY_MODEL, generate_live_predictions
from app.models import (
    MARKETS,
    MATCH_STATUSES,
    Competition,
    EvaluationRun,
    ImportRun,
    Match,
    MatchProviderRef,
    MatchStatistics,
    OddsSnapshot,
    Prediction,
    Season,
    Team,
    TeamFeature,
)
from app.providers import PROVIDER_NAMES
from app.schemas import (
    CompetitionOut,
    DataStatus,
    EvaluationOut,
    FormMatch,
    FormOut,
    ImportRequest,
    ImportRunOut,
    MatchDetail,
    MatchPage,
    MatchSummary,
    OddsOut,
    PredictionOut,
    ProvenanceOut,
    StatsOut,
    TeamFeatureOut,
    TeamOut,
)

router = APIRouter(prefix="/api/v1")
Db = Annotated[Session, Depends(get_session)]
Config = Annotated[Settings, Depends(get_settings)]
CompetitionCode = Annotated[str, Query(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9]+$")]


def _competition(db: Session, code: str) -> Competition:
    row = db.scalar(select(Competition).where(Competition.code == code.upper()))
    if row is None:
        raise HTTPException(404, f"Competition '{code}' not found (no data imported for it yet)")
    return row


def _prediction_out(p: Prediction, match: Match, now: datetime, stale_hours: int) -> PredictionOut:
    state = "ok"
    if p.status == "unavailable":
        state = "unavailable"
    elif (
        p.kind == "live"
        and match.status == "scheduled"
        and match.kickoff_utc > now
        and p.generated_at < now - timedelta(hours=stale_hours)
    ):
        state = "stale"
    return PredictionOut(
        market=p.market, model=p.model_version.name, model_version=p.model_version.version, kind=p.kind,
        state=state, reason=p.reason, probabilities=p.probabilities,
        expected_home_goals=p.expected_home_goals, expected_away_goals=p.expected_away_goals,
        training_matches=p.training_matches, explanation=p.explanation,
        generated_at=p.generated_at, data_cutoff=p.data_cutoff,
    )


def _primary(predictions: list[Prediction]) -> list[Prediction]:
    """Primary model's predictions; a genuine pre-match (live) one is preferred over a backtest."""
    mine = [p for p in predictions if p.model_version.name == PRIMARY_MODEL]
    live = [p for p in mine if p.kind == "live"]
    chosen = live or mine
    order = list(MARKETS)
    return sorted(chosen, key=lambda p: order.index(p.market))


def _summary(match: Match, predictions: list[Prediction], now: datetime, stale_hours: int) -> dict:
    outs = [_prediction_out(p, match, now, stale_hours) for p in _primary(predictions)]
    states = {o.state for o in outs}
    state = (
        "missing" if not outs
        else "unavailable" if states == {"unavailable"}
        else "stale" if "stale" in states
        else "ok"
    )
    note = None
    if match.status == "scheduled" and match.result_known_at < now:
        note = "Kick-off has passed but no result has been imported yet; data for this match may be out of date."
    elif match.kickoff_precision == "date":
        note = "The data provider supplied only a date for this match; kick-off time is unknown."
    return dict(
        id=match.id, competition_code=match.competition.code, season=match.season.label, stage=match.stage,
        matchday=match.matchday, kickoff_utc=match.kickoff_utc, kickoff_precision=match.kickoff_precision,
        status=match.status, home_team=TeamOut.model_validate(match.home_team),
        away_team=TeamOut.model_validate(match.away_team), home_goals=match.home_goals,
        away_goals=match.away_goals, data_note=note, prediction_state=state, predictions=outs,
    )


_MATCH_LOAD = (
    selectinload(Match.competition), selectinload(Match.season),
    selectinload(Match.home_team), selectinload(Match.away_team),
)


def _predictions_for(db: Session, match_ids: list[int]) -> dict[int, list[Prediction]]:
    out: dict[int, list[Prediction]] = {}
    if match_ids:
        rows = db.scalars(
            select(Prediction).where(Prediction.match_id.in_(match_ids)).options(selectinload(Prediction.model_version))
        )
        for p in rows:
            out.setdefault(p.match_id, []).append(p)
    return out


@router.get("/competitions", response_model=list[CompetitionOut])
def list_competitions(db: Db):
    """Competitions that have imported data, with their seasons."""
    return db.scalars(select(Competition).options(selectinload(Competition.seasons)).order_by(Competition.name)).all()


@router.get("/matches", response_model=MatchPage)
def list_matches(
    db: Db,
    config: Config,
    competition: CompetitionCode = "PL",
    season: Annotated[int | None, Query(ge=1990, le=2100, description="Season start year")] = None,
    status: Annotated[Literal[*MATCH_STATUSES] | None, Query()] = None,
    date_from: Annotated[date | None, Query(description="Inclusive, UTC")] = None,
    date_to: Annotated[date | None, Query(description="Inclusive, UTC")] = None,
    order: Literal["asc", "desc"] = "asc",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Matches filtered by competition, season, status and date, with the primary model's predictions."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    comp = _competition(db, competition)
    conditions = [Match.competition_id == comp.id]
    if season is not None:
        conditions.append(Match.season_id.in_(select(Season.id).where(Season.competition_id == comp.id,
                                                                     Season.start_year == season)))
    if status:
        conditions.append(Match.status == status)
    if date_from:
        conditions.append(Match.kickoff_utc >= datetime.combine(date_from, time.min, UTC))
    if date_to:
        conditions.append(Match.kickoff_utc < datetime.combine(date_to + timedelta(days=1), time.min, UTC))
    total = db.scalar(select(func.count()).select_from(Match).where(*conditions))
    direction = Match.kickoff_utc.asc() if order == "asc" else Match.kickoff_utc.desc()
    matches = db.scalars(
        select(Match).where(*conditions).order_by(direction, Match.id).limit(limit).offset(offset).options(*_MATCH_LOAD)
    ).all()
    predictions = _predictions_for(db, [m.id for m in matches])
    now = utcnow()
    items = [MatchSummary(**_summary(m, predictions.get(m.id, []), now, config.prediction_stale_hours)) for m in matches]
    return MatchPage(items=items, total=total, limit=limit, offset=offset)


def _team_form(db: Session, team: Team, competition_id: int, before: datetime, limit: int) -> FormOut:
    candidates = db.scalars(
        select(Match)
        .where(
            Match.competition_id == competition_id, Match.status == "finished", Match.kickoff_utc < before,
            or_(Match.home_team_id == team.id, Match.away_team_id == team.id),
        )
        .order_by(Match.kickoff_utc.desc())
        .limit(limit + 5)
        .options(selectinload(Match.home_team), selectinload(Match.away_team))
    ).all()
    rows = []
    for m in candidates:
        if m.result_known_at > before or len(rows) >= limit:  # same "known before" rule as the models
            continue
        home = m.home_team_id == team.id
        gf, ga = (m.home_goals, m.away_goals) if home else (m.away_goals, m.home_goals)
        rows.append(FormMatch(
            match_id=m.id, kickoff_utc=m.kickoff_utc, opponent=(m.away_team if home else m.home_team).name,
            venue="H" if home else "A", goals_for=gf, goals_against=ga,
            result="W" if gf > ga else "D" if gf == ga else "L",
        ))
    return FormOut(team=TeamOut.model_validate(team), before=before, matches=rows)


@router.get("/matches/{match_id}", response_model=MatchDetail)
def get_match(match_id: int, db: Db, config: Config):
    """Match detail: predictions, odds, statistics, recent form and data provenance."""
    match = db.scalar(select(Match).where(Match.id == match_id).options(*_MATCH_LOAD))
    if match is None:
        raise HTTPException(404, f"Match {match_id} not found")
    now = utcnow()
    predictions = _predictions_for(db, [match.id]).get(match.id, [])
    order = list(MARKETS)
    all_out = [
        _prediction_out(p, match, now, config.prediction_stale_hours)
        for p in sorted(predictions, key=lambda p: (p.model_version.name != PRIMARY_MODEL, p.kind, order.index(p.market)))
    ]
    shown_kinds = {p.kind for p in _primary(predictions)}
    features = [
        f for f in db.scalars(select(TeamFeature).where(TeamFeature.match_id == match.id)) if f.kind in shown_kinds
    ]

    latest: dict[tuple, OddsSnapshot] = {}
    for o in db.scalars(select(OddsSnapshot).where(OddsSnapshot.match_id == match.id).order_by(OddsSnapshot.fetched_at)):
        latest[(o.provider, o.bookmaker, o.market, o.selection, o.timing)] = o
    odds = [
        OddsOut(provider=o.provider, bookmaker=o.bookmaker, market=o.market, selection=o.selection,
                decimal_odds=o.decimal_odds, implied_probability=1.0 / o.decimal_odds, timing=o.timing,
                captured_at=o.captured_at, fetched_at=o.fetched_at)
        for o in latest.values()
    ]
    stats = db.scalars(select(MatchStatistics).where(MatchStatistics.match_id == match.id)).all()
    refs = db.scalars(select(MatchProviderRef).where(MatchProviderRef.match_id == match.id)).all()
    return MatchDetail(
        **_summary(match, predictions, now, config.prediction_stale_hours),
        all_predictions=all_out,
        features=[TeamFeatureOut.model_validate(f) for f in features],
        odds=odds,
        statistics=[StatsOut.model_validate(s) for s in stats],
        home_form=_team_form(db, match.home_team, match.competition_id, match.kickoff_utc, 5),
        away_form=_team_form(db, match.away_team, match.competition_id, match.kickoff_utc, 5),
        provenance=[ProvenanceOut.model_validate(r) for r in refs],
    )


@router.get("/matches/{match_id}/predictions", response_model=list[PredictionOut])
def get_match_predictions(
    match_id: int, db: Db, config: Config,
    market: Annotated[Literal["1X2", "OU25", "BTTS"] | None, Query()] = None,
):
    """All stored predictions for a match (every model and kind), optionally for one market."""
    match = db.get(Match, match_id)
    if match is None:
        raise HTTPException(404, f"Match {match_id} not found")
    now = utcnow()
    return [
        _prediction_out(p, match, now, config.prediction_stale_hours)
        for p in _predictions_for(db, [match_id]).get(match_id, [])
        if market is None or p.market == market
    ]


@router.get("/teams/{team_id}/form", response_model=FormOut)
def get_team_form(
    team_id: int, db: Db,
    competition: CompetitionCode = "PL",
    before: Annotated[datetime | None, Query(description="Only matches known before this time (default: now)")] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
):
    """A team's most recent finished matches in a competition."""
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(404, f"Team {team_id} not found")
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    return _team_form(db, team, _competition(db, competition).id, before or utcnow(), limit)


@router.get("/evaluation/latest", response_model=EvaluationOut)
def latest_evaluation(db: Db, competition: CompetitionCode = "PL"):
    """Most recent chronological evaluation summary for a competition."""
    comp = _competition(db, competition)
    run = db.scalar(
        select(EvaluationRun).where(EvaluationRun.competition_id == comp.id).order_by(EvaluationRun.created_at.desc())
    )
    if run is None:
        raise HTTPException(404, "No evaluation has been run for this competition yet")
    return EvaluationOut(
        id=run.id, competition_code=comp.code, created_at=run.created_at, period_start=run.period_start,
        period_end=run.period_end, n_matches=run.n_matches, results=run.results,
    )


@router.get("/status", response_model=DataStatus)
def data_status(db: Db, config: Config, competition: CompetitionCode = "PL"):
    """When data was last imported and predictions last generated. Works before any import."""
    code = competition.upper()
    runs = select(ImportRun).where(ImportRun.competition_code == code).order_by(ImportRun.started_at.desc())
    last_attempt = db.scalar(runs)
    last_success = db.scalar(runs.where(ImportRun.status == "success"))
    comp = db.scalar(select(Competition).where(Competition.code == code))
    now = utcnow()
    latest_prediction = upcoming = latest_evaluation = None
    if comp is not None:
        match_ids = select(Match.id).where(Match.competition_id == comp.id)
        latest_prediction = db.scalar(
            select(func.max(Prediction.generated_at)).where(Prediction.kind == "live", Prediction.match_id.in_(match_ids))
        )
        upcoming = db.scalar(
            select(func.count()).select_from(Match).where(
                Match.competition_id == comp.id, Match.status == "scheduled", Match.kickoff_utc > now)
        )
        latest_evaluation = db.scalar(
            select(func.max(EvaluationRun.created_at)).where(EvaluationRun.competition_id == comp.id)
        )
    if last_success is None:
        state = "never_imported"
    elif last_success.finished_at and last_success.finished_at < now - timedelta(hours=config.data_stale_hours):
        state = "stale"
    else:
        state = "fresh"
    return DataStatus(
        competition_code=code, data_state=state, data_stale_after_hours=config.data_stale_hours,
        last_successful_import=last_success, last_import_attempt=last_attempt,
        latest_prediction_generated_at=latest_prediction, upcoming_matches=upcoming or 0,
        latest_evaluation_at=latest_evaluation,
    )


# --- Admin (token protected) -------------------------------------------------------------------


def require_admin(config: Config, x_admin_token: Annotated[str | None, Header()] = None) -> None:
    if not config.admin_api_token:
        raise HTTPException(503, "Admin API is disabled: ADMIN_API_TOKEN is not configured")
    if not x_admin_token or not secrets.compare_digest(x_admin_token, config.admin_api_token):
        raise HTTPException(401, "Missing or invalid admin token")


admin = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(require_admin)], tags=["admin"])


@admin.get("/imports", response_model=list[ImportRunOut])
def list_imports(db: Db, limit: Annotated[int, Query(ge=1, le=100)] = 20):
    return db.scalars(select(ImportRun).order_by(ImportRun.started_at.desc()).limit(limit)).all()


@admin.post("/imports", response_model=ImportRunOut, status_code=201)
def trigger_import(body: ImportRequest, db: Db):
    """Import one season from a provider, then regenerate predictions for upcoming matches."""
    if body.provider not in PROVIDER_NAMES:
        raise HTTPException(422, f"Unknown provider; known providers: {', '.join(PROVIDER_NAMES)}")
    run = run_import(db, body.provider, body.competition.upper(), body.season)
    if run.status == "success":
        generate_live_predictions(db, body.competition.upper())
    return run
