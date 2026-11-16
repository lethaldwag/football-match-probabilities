"""Chronological (walk-forward) evaluation.

For every match day in the evaluation seasons, models are refitted using only results known before
that day (cutoff = 00:00 UTC of the match day), then scored on that day's matches. No random splits.
"""

import logging
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import log_loss
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.modeling.features import load_match_frame
from app.modeling.models import outcomes, validate_probabilities
from app.modeling.predict import PRIMARY_MODEL, ensure_model_versions, fit_models, prediction_rows
from app.models import MARKETS, Competition, EvaluationRun, Match, OddsSnapshot, Prediction, Season, TeamFeature

log = logging.getLogger(__name__)

BENCHMARK = "bookmaker-implied"
BENCHMARK_BOOKMAKERS = ("Avg", "BbAv")  # market-average columns in football-data.co.uk files
CALIBRATION_BINS = 10
BET_EDGE_THRESHOLD = 0.05  # fixed in advance; not optimised on the evaluation period
METHOD = (
    "Walk-forward: for each match day, models are refitted on results known before 00:00 UTC of that "
    "day and scored on that day's matches. Brier score is the multi-class form (sum of squared errors "
    "over outcomes, averaged over matches; 0 is perfect, lower is better)."
)


def score(pairs: list[tuple[dict[str, float], str]], market: str) -> dict | None:
    """Metrics for a list of (probabilities, actual outcome)."""
    if not pairs:
        return None
    selections = MARKETS[market]
    p = np.array([[probs[s] for s in selections] for probs, _ in pairs])
    y = np.array([selections.index(actual) for _, actual in pairs])
    onehot = np.eye(len(selections))[y]
    return {
        "n": len(pairs),
        "log_loss": float(log_loss(y, p, labels=list(range(len(selections))))),
        "brier": float(((p - onehot) ** 2).sum(axis=1).mean()),
        "accuracy": float((p.argmax(axis=1) == y).mean()),
    }


def calibration(pairs: list[tuple[dict[str, float], str]], market: str) -> list[dict]:
    """Predicted vs observed frequency by probability bin (all outcomes pooled one-vs-rest)."""
    if not pairs:
        return []
    selections = MARKETS[market]
    # For two-way markets the second outcome mirrors the first, so only the first is used.
    used = selections if len(selections) > 2 else selections[:1]
    prob = np.array([probs[s] for probs, _ in pairs for s in used])
    hit = np.array([int(actual == s) for _, actual in pairs for s in used])
    observed, predicted = calibration_curve(hit, prob, n_bins=CALIBRATION_BINS, strategy="uniform")
    edges = np.linspace(0.0, 1.0, CALIBRATION_BINS + 1)
    bin_ids = np.searchsorted(edges[1:-1], prob)  # same binning as calibration_curve
    counts = np.bincount(bin_ids, minlength=CALIBRATION_BINS)
    nonempty = np.flatnonzero(counts)
    return [
        {"bin_lower": float(edges[b]), "bin_upper": float(edges[b + 1]), "mean_predicted": float(predicted[i]),
         "observed_frequency": float(observed[i]), "count": int(counts[b])}
        for i, b in enumerate(nonempty)
    ]


def walk_forward(frame: pd.DataFrame, eval_season_ids: set[int]) -> list[dict]:
    """Return one record per evaluated match: fitted-at-cutoff objects needed to build predictions."""
    targets = frame[(frame["status"] == "finished") & frame["season_id"].isin(eval_season_ids)]
    records = []
    for day, day_matches in targets.groupby(targets["kickoff"].dt.floor("D"), sort=True):
        cutoff = day.to_pydatetime().astimezone(UTC)
        fitted = fit_models(frame, cutoff)
        for row in day_matches.itertuples():
            records.append({"row": row, "fitted": fitted})
    return records


def _benchmark_odds(session: Session, match_ids: list[int]) -> dict[int, dict[str, dict[str, float]]]:
    """Latest pre-match market-average odds per match: {match_id: {market: {selection: odds}}}."""
    out: dict[int, dict[str, dict[str, float]]] = {}
    for bookmaker in reversed(BENCHMARK_BOOKMAKERS):  # preferred bookmaker applied last, overwriting
        rows = session.execute(
            select(OddsSnapshot.match_id, OddsSnapshot.market, OddsSnapshot.selection, OddsSnapshot.decimal_odds)
            .where(OddsSnapshot.timing == "pre_match", OddsSnapshot.bookmaker == bookmaker)
            .order_by(OddsSnapshot.fetched_at)
        )
        wanted = set(match_ids)
        staged: dict[int, dict[str, dict[str, float]]] = {}
        for match_id, market, selection, odds in rows:
            if match_id in wanted:
                staged.setdefault(match_id, {}).setdefault(market, {})[selection] = odds
        out.update({m: {**out.get(m, {}), **markets} for m, markets in staged.items()})
    return out


def implied_probabilities(odds: dict[str, float], market: str) -> dict[str, float] | None:
    """Margin-free implied probabilities (proportional normalisation), or None if a price is missing."""
    selections = MARKETS[market]
    if any(s not in odds for s in selections):
        return None
    inverse = {s: 1.0 / odds[s] for s in selections}
    total = sum(inverse.values())
    return validate_probabilities(market, {s: v / total for s, v in inverse.items()})


def simulate_bets(bets_input: list[dict]) -> dict:
    """Flat-stake simulation on 1X2. `bets_input` is chronological: {probs, odds, actual}."""
    assumptions = {
        "market": "Bets on the match result market (home win / draw / away win) only.",
        "odds": "Pre-match market-average odds from football-data.co.uk (collected by the provider "
                "before the match; exact quote time not published). Odds include the bookmaker margin.",
        "selection_rule": f"At most one bet per match: the outcome with the highest model expected "
                          f"value, only if model probability x odds - 1 >= {BET_EDGE_THRESHOLD}.",
        "stake": "1 unit per bet (flat)",
        "threshold_note": "The threshold was fixed in advance, not optimised on this period.",
    }
    equity, peak, drawdown, n_bets, overrounds = 0.0, 0.0, 0.0, 0, []
    for item in bets_input:
        odds, probs = item["odds"], item["probs"]
        overrounds.append(sum(1.0 / o for o in odds.values()) - 1.0)
        selection = max(odds, key=lambda s: probs[s] * odds[s])
        if probs[selection] * odds[selection] - 1.0 < BET_EDGE_THRESHOLD:
            continue
        n_bets += 1
        equity += (odds[selection] - 1.0) if item["actual"] == selection else -1.0
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "available": True,
        "assumptions": assumptions,
        "matches_with_odds": len(bets_input),
        "n_bets": n_bets,
        "total_staked": float(n_bets),
        "profit": equity,
        "roi": equity / n_bets if n_bets else None,
        "max_drawdown": drawdown,
        "average_bookmaker_margin": float(np.mean(overrounds)) if overrounds else None,
    }


def run_evaluation(session: Session, competition_code: str, n_seasons: int = 3) -> EvaluationRun | None:
    """Run the walk-forward backtest, store backtest predictions and an evaluation summary."""
    competition = session.scalar(select(Competition).where(Competition.code == competition_code))
    if competition is None:
        return None
    frame = load_match_frame(session, competition.id)
    finished = frame[frame["status"] == "finished"]
    if finished.empty:
        return None
    seasons = {
        s.id: s for s in session.scalars(select(Season).where(Season.competition_id == competition.id))
    }
    with_results = sorted({seasons[i].start_year: i for i in finished["season_id"].unique()}.items())
    eval_ids = {season_id for _, season_id in with_results[-n_seasons:]}

    versions = ensure_model_versions(session)
    generated_at = utcnow()
    records = walk_forward(frame, eval_ids)

    scored: dict[str, dict[str, list[tuple]]] = {}  # model -> market -> [(probs, actual, season_id)]
    prediction_dicts, feature_dicts, unavailable = [], [], 0
    primary_1x2: dict[int, dict[str, float]] = {}
    actuals: dict[int, dict[str, str]] = {}
    for record in records:
        row, fitted = record["row"], record["fitted"]
        predictions, features = prediction_rows(
            fitted, row.match_id, row.home_id, row.away_id, "backtest", generated_at, versions
        )
        prediction_dicts += predictions
        feature_dicts += features
        actual = outcomes(int(row.home_goals), int(row.away_goals))
        actuals[row.match_id] = actual
        if PRIMARY_MODEL not in fitted.models:
            unavailable += 1
        for name, model in fitted.models.items():
            probs = model.predict(row.home_id, row.away_id)
            for market in MARKETS:
                scored.setdefault(name, {}).setdefault(market, []).append(
                    (probs[market], actual[market], row.season_id)
                )
            if name == PRIMARY_MODEL:
                primary_1x2[row.match_id] = probs["1X2"]

    # Bookmaker benchmark and betting simulation use time-labelled pre-match odds only.
    ordered = [r["row"] for r in records]
    odds = _benchmark_odds(session, [r.match_id for r in ordered])
    bets_input = []
    for row in ordered:
        for market in ("1X2", "OU25"):
            implied = implied_probabilities(odds.get(row.match_id, {}).get(market, {}), market)
            if implied:
                scored.setdefault(BENCHMARK, {}).setdefault(market, []).append(
                    (implied, actuals[row.match_id][market], row.season_id)
                )
        match_odds = odds.get(row.match_id, {}).get("1X2", {})
        if row.match_id in primary_1x2 and len(match_odds) == 3:
            bets_input.append({"probs": primary_1x2[row.match_id], "odds": match_odds,
                               "actual": actuals[row.match_id]["1X2"]})

    def summarise(name: str) -> dict:
        markets = {}
        for market, triples in scored.get(name, {}).items():
            pairs = [(p, a) for p, a, _ in triples]
            by_season = []
            for start_year, season_id in with_results[-n_seasons:]:
                metrics = score([(p, a) for p, a, s in triples if s == season_id], market)
                if metrics:
                    by_season.append({"season": seasons[season_id].label, "start_year": start_year, **metrics})
            markets[market] = {"overall": score(pairs, market), "by_season": by_season,
                               "calibration": calibration(pairs, market)}
        return markets

    models = {}
    for name, version in versions.items():
        models[name] = {
            "version": version.version,
            "role": "primary" if name == PRIMARY_MODEL else "reference baseline",
            "description": version.description,
            "markets": summarise(name),
        }
    if BENCHMARK in scored:
        models[BENCHMARK] = {
            "version": None,
            "role": "market benchmark",
            "description": "Probabilities implied by pre-match market-average bookmaker odds with the "
                           "margin removed proportionally. Not a model of this application; shown for "
                           "context. No BTTS odds are available from the current provider.",
            "markets": summarise(BENCHMARK),
        }

    results = {
        "method": METHOD,
        "primary_model": PRIMARY_MODEL,
        "seasons": [seasons[i].label for _, i in with_results[-n_seasons:]],
        "matches_without_primary_prediction": unavailable,
        "models": models,
        "betting_simulation": simulate_bets(bets_input) if bets_input else {
            "available": False, "reason": "No pre-match bookmaker odds are stored for the evaluated matches."},
    }

    match_ids = select(Match.id).where(Match.competition_id == competition.id)
    session.execute(delete(Prediction).where(Prediction.kind == "backtest", Prediction.match_id.in_(match_ids)))
    session.execute(delete(TeamFeature).where(TeamFeature.kind == "backtest", TeamFeature.match_id.in_(match_ids)))
    if prediction_dicts:
        session.execute(Prediction.__table__.insert(), prediction_dicts)
        session.execute(TeamFeature.__table__.insert(), feature_dicts)
    kickoffs = [r.kickoff.to_pydatetime() for r in ordered] or [datetime.now(UTC)]
    run = EvaluationRun(
        competition_id=competition.id, created_at=generated_at, period_start=min(kickoffs),
        period_end=max(kickoffs), n_matches=len(ordered), results=results,
    )
    session.add(run)
    session.commit()
    log.info("evaluation finished", extra={"competition": competition_code, "matches": len(ordered)})
    return run
