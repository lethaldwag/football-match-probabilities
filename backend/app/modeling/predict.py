"""Fit models at a data cutoff and store predictions with their timestamps."""

import logging
from dataclasses import dataclass
from datetime import datetime

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.modeling.features import known_before, load_match_frame, team_form
from app.modeling.models import BaseRateModel, ModelUnavailable, PoissonGoalsModel
from app.models import MARKETS, Competition, Match, ModelVersion, Prediction, TeamFeature

log = logging.getLogger(__name__)

PRIMARY_MODEL = PoissonGoalsModel.name
MODEL_CLASSES = (PoissonGoalsModel, BaseRateModel)


def ensure_model_versions(session: Session) -> dict[str, ModelVersion]:
    """Make sure a model_versions row exists for each model (new row when parameters change)."""
    out = {}
    for cls in MODEL_CLASSES:
        model = cls()
        params = model.params_dict()
        rows = session.scalars(select(ModelVersion).where(ModelVersion.name == cls.name)).all()
        row = next((r for r in rows if r.params == params and r.version.startswith(cls.version)), None)
        if row is None:
            # Same code version but different parameters -> distinct, traceable version label.
            version = cls.version if not rows else f"{cls.version}+p{len(rows) + 1}"
            row = ModelVersion(name=cls.name, version=version, description=cls.description, params=params)
            session.add(row)
            session.flush()
        out[cls.name] = row
    return out


@dataclass
class Fitted:
    cutoff: datetime
    known: pd.DataFrame
    models: dict[str, object]  # name -> fitted model
    reasons: dict[str, str]  # name -> why unavailable


def fit_models(frame: pd.DataFrame, cutoff: datetime) -> Fitted:
    """Fit every model using only matches whose results were known at `cutoff`."""
    lookback = PoissonGoalsModel().params.lookback_days
    known = known_before(frame, cutoff, lookback_days=lookback)
    fitted = Fitted(cutoff=cutoff, known=known, models={}, reasons={})
    for cls in MODEL_CLASSES:
        try:
            fitted.models[cls.name] = cls().fit(known, cutoff)
        except ModelUnavailable as exc:
            fitted.reasons[cls.name] = str(exc)
    return fitted


def prediction_rows(
    fitted: Fitted, match_id: int, home_id: int, away_id: int, kind: str, generated_at: datetime,
    versions: dict[str, ModelVersion],
) -> tuple[list[dict], list[dict]]:
    """Build prediction and team-feature rows (as dicts) for one match."""
    predictions: list[dict] = []
    base = {"match_id": match_id, "kind": kind, "generated_at": generated_at, "data_cutoff": fitted.cutoff}
    for name, version in versions.items():
        model = fitted.models.get(name)
        common = {**base, "model_version_id": version.id}
        if model is None:
            predictions += [
                {**common, "market": market, "status": "unavailable", "reason": fitted.reasons[name],
                 "probabilities": None, "expected_home_goals": None, "expected_away_goals": None,
                 "training_matches": len(fitted.known), "explanation": None}
                for market in MARKETS
            ]
            continue
        probs = model.predict(home_id, away_id)
        home_xg = away_xg = explanation = None
        if isinstance(model, PoissonGoalsModel):
            home_xg, away_xg = model.expected_goals(home_id, away_id)
            notes = [
                f"{side} team has no matches in the training window; average strength assumed"
                for side, team in (("Home", home_id), ("Away", away_id))
                if not model.has_team(team)
            ]
            explanation = {"home_advantage": model.home_advantage, "notes": notes}
        predictions += [
            {**common, "market": market, "status": "ok", "reason": None, "probabilities": probs[market],
             "expected_home_goals": home_xg, "expected_away_goals": away_xg,
             "training_matches": model.n_train, "explanation": explanation}
            for market in MARKETS
        ]

    poisson = fitted.models.get(PoissonGoalsModel.name)
    features = []
    for team in (home_id, away_id):
        attack, defence = poisson.strengths(team) if poisson else (None, None)
        features.append(
            {"match_id": match_id, "team_id": team, "kind": kind, "data_cutoff": fitted.cutoff,
             **team_form(fitted.known, team), "attack_strength": attack, "defence_weakness": defence}
        )
    return predictions, features


def generate_live_predictions(session: Session, competition_code: str, now: datetime | None = None) -> int:
    """(Re)generate predictions for matches that have not kicked off. Returns matches predicted.

    Predictions for matches that have already kicked off are never touched.
    """
    now = now or utcnow()
    competition = session.scalar(select(Competition).where(Competition.code == competition_code))
    if competition is None:
        return 0
    upcoming = session.scalars(
        select(Match).where(
            Match.competition_id == competition.id, Match.status == "scheduled", Match.kickoff_utc > now
        )
    ).all()
    if not upcoming:
        return 0
    versions = ensure_model_versions(session)
    fitted = fit_models(load_match_frame(session, competition.id), now)
    ids = [m.id for m in upcoming]
    session.execute(delete(Prediction).where(Prediction.kind == "live", Prediction.match_id.in_(ids)))
    session.execute(delete(TeamFeature).where(TeamFeature.kind == "live", TeamFeature.match_id.in_(ids)))
    all_predictions, all_features = [], []
    for match in upcoming:
        try:
            predictions, features = prediction_rows(
                fitted, match.id, match.home_team_id, match.away_team_id, "live", now, versions
            )
        except Exception:
            log.exception("prediction generation failed", extra={"match_id": match.id})
            continue
        all_predictions += predictions
        all_features += features
    session.execute(Prediction.__table__.insert(), all_predictions)
    session.execute(TeamFeature.__table__.insert(), all_features)
    session.commit()
    log.info("live predictions generated", extra={"competition": competition_code, "matches": len(upcoming),
                                                  "data_cutoff": now.isoformat()})
    return len(upcoming)
