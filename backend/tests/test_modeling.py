"""Probability validity, cutoff/leakage behaviour and chronological evaluation. Synthetic data."""

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.ingest import import_records
from app.modeling.evaluate import calibration, implied_probabilities, run_evaluation, score, simulate_bets, walk_forward
from app.modeling.features import known_before, load_match_frame, team_form
from app.modeling.models import (
    BaseRateModel,
    ModelUnavailable,
    PoissonGoalsModel,
    market_probabilities,
    outcomes,
    validate_probabilities,
)
from app.modeling.predict import fit_models, generate_live_predictions
from app.models import (
    MARKETS,
    Competition,
    EvaluationRun,
    Match,
    ModelVersion,
    Prediction,
    TeamFeature,
    result_known_at,
)
from tests.conftest import synthetic_season


def _frame(session) -> pd.DataFrame:
    return load_match_frame(session, session.scalars(select(Competition)).one().id)


# --- probabilities -------------------------------------------------------------------------------


@pytest.mark.parametrize("home_rate", [0.05, 0.7, 1.5, 3.0, 6.0])
@pytest.mark.parametrize("away_rate", [0.05, 1.1, 4.5])
def test_market_probabilities_are_valid_distributions(home_rate, away_rate):
    for market, probs in market_probabilities(home_rate, away_rate).items():
        assert tuple(probs) == MARKETS[market]
        assert all(0.0 <= p <= 1.0 for p in probs.values())
        assert math.isclose(sum(probs.values()), 1.0, abs_tol=1e-9)


def test_market_probabilities_match_hand_calculation():
    probs = market_probabilities(1.0, 1.0, max_goals=25)
    # P(total <= 2) for Poisson(2) = e^-2 * (1 + 2 + 2) ; P(both score) = (1 - e^-1)^2
    assert math.isclose(probs["OU25"]["under"], 5 * math.exp(-2), abs_tol=1e-9)
    assert math.isclose(probs["BTTS"]["yes"], (1 - math.exp(-1)) ** 2, abs_tol=1e-9)
    assert math.isclose(probs["1X2"]["home"], probs["1X2"]["away"], abs_tol=1e-12)
    assert market_probabilities(2.5, 0.8)["1X2"]["home"] > market_probabilities(0.8, 2.5)["1X2"]["home"]


@pytest.mark.parametrize("rates", [(0, 1), (-1, 1), (float("nan"), 1), (1, float("inf"))])
def test_invalid_goal_rates_are_rejected(rates):
    with pytest.raises(ValueError):
        market_probabilities(*rates)


@pytest.mark.parametrize(
    "probs",
    [{"home": 0.5, "draw": 0.3, "away": 0.3}, {"home": 1.2, "draw": -0.1, "away": -0.1},
     {"home": float("nan"), "draw": 0.5, "away": 0.5}, {"home": 0.5, "away": 0.5}],
)
def test_validate_probabilities_rejects_bad_input(probs):
    with pytest.raises(ValueError):
        validate_probabilities("1X2", probs)


def test_models_refuse_to_predict_with_too_little_data(three_seasons):
    frame = _frame(three_seasons)
    early = frame["kickoff"].min().to_pydatetime() + timedelta(days=30)
    fitted = fit_models(frame, early)
    assert fitted.models == {} and "past matches available" in fitted.reasons[PoissonGoalsModel.name]
    with pytest.raises(ModelUnavailable):
        BaseRateModel().fit(known_before(frame, early), early)


def test_fitted_models_give_valid_probabilities_and_sensible_ordering(three_seasons):
    frame = _frame(three_seasons)
    fitted = fit_models(frame, datetime(2024, 1, 1, tzinfo=UTC))
    poisson, base = fitted.models[PoissonGoalsModel.name], fitted.models[BaseRateModel.name]
    teams = {t: i for i, t in zip(sorted(set(frame["home_id"])), "ABCDEFGH", strict=True)}
    weak, strong = teams["A"], teams["H"]  # Team H is generated as the strongest
    for model in (poisson, base):
        for market, probs in model.predict(strong, weak).items():
            validate_probabilities(market, probs)
    assert poisson.predict(strong, weak)["1X2"]["home"] > poisson.predict(weak, strong)["1X2"]["home"]
    assert poisson.strengths(strong)[0] > poisson.strengths(weak)[0]
    assert poisson.home_advantage > 1.0
    # A team the model has never seen gets average strength rather than an error.
    assert poisson.strengths(999) == (None, None)
    validate_probabilities("1X2", poisson.predict(999, weak)["1X2"])


# --- cutoff / leakage ----------------------------------------------------------------------------


def test_result_known_at_is_conservative():
    kickoff = datetime(2025, 1, 4, 15, 0, tzinfo=UTC)
    assert result_known_at(kickoff, "datetime") == kickoff + timedelta(hours=3)
    assert result_known_at(datetime(2025, 1, 4, tzinfo=UTC), "date") == datetime(2025, 1, 5, tzinfo=UTC)


def test_known_before_excludes_everything_not_known_at_cutoff(three_seasons):
    frame = _frame(three_seasons)
    cutoff = frame["kickoff"].iloc[60].to_pydatetime()  # kick-off of some match day
    known = known_before(frame, cutoff)
    assert len(known) > 0
    assert (known["known_at"] <= pd.Timestamp(cutoff)).all()
    assert (known["kickoff"] < pd.Timestamp(cutoff)).all()  # no same-day or later match
    # A match still in play at the cutoff (kicked off 1h earlier) is not known yet.
    assert len(known_before(frame, cutoff + timedelta(hours=1))) == len(known)
    assert len(known_before(frame, cutoff + timedelta(hours=3))) == len(known) + 4


def test_scheduled_matches_never_enter_training(session):
    import_records(session, "synthetic", synthetic_season(2022, finished=False))
    assert known_before(_frame(session), datetime(2040, 1, 1, tzinfo=UTC)).empty


def test_future_results_cannot_change_a_prediction(three_seasons):
    """The leakage test: rewrite every result on/after the cutoff; predictions must be identical."""
    session = three_seasons
    import_records(session, "synthetic", synthetic_season(2023))
    session.commit()
    cutoff = datetime(2023, 10, 1, tzinfo=UTC)
    home, away = session.execute(select(Match.home_team_id, Match.away_team_id).limit(1)).one()

    def predict():
        fitted = fit_models(_frame(session), cutoff)
        return {name: model.predict(home, away) for name, model in fitted.models.items()}, len(fitted.known)

    before, n_before = predict()
    changed = 0
    for match in session.scalars(select(Match)):
        if match.result_known_at > cutoff:
            match.home_goals, match.away_goals = 9, 0
            changed += 1
    session.commit()
    after, n_after = predict()
    assert changed > 0 and n_before == n_after
    assert before == after and PoissonGoalsModel.name in before


def test_team_form_uses_only_the_filtered_frame(three_seasons):
    frame = _frame(three_seasons)
    team = int(frame["home_id"].iloc[0])
    cutoff = datetime(2021, 1, 1, tzinfo=UTC)
    form = team_form(known_before(frame, cutoff), team)
    mine = frame[((frame["home_id"] == team) | (frame["away_id"] == team)) & (frame["kickoff"] < pd.Timestamp(cutoff))]
    assert form["matches_in_window"] == len(mine) and form["form_matches"] == 5
    assert 0 <= form["form_points"] <= 15
    assert team_form(known_before(frame, datetime(2000, 1, 1, tzinfo=UTC)), team)["form_matches"] == 0


# --- stored predictions --------------------------------------------------------------------------


def test_live_predictions_are_stored_with_timestamps_and_frozen_at_kickoff(three_seasons):
    session = three_seasons
    now = datetime(2023, 8, 1, 12, 0, tzinfo=UTC)
    import_records(session, "synthetic", synthetic_season(2023, finished=False))
    session.commit()
    assert generate_live_predictions(session, "PL", now=now) == 56
    assert generate_live_predictions(session, "NOPE", now=now) == 0

    rows = session.scalars(select(Prediction)).all()
    assert len(rows) == 56 * 3 * 2  # markets x models
    assert all(p.kind == "live" and p.generated_at == now and p.data_cutoff == now for p in rows)
    assert all(p.status == "ok" and p.training_matches == 168 for p in rows)
    for p in rows:
        validate_probabilities(p.market, p.probabilities)
    assert session.scalar(select(func.count()).select_from(TeamFeature)) == 112
    assert session.scalar(select(func.count()).select_from(ModelVersion)) == 2

    # Regenerating replaces rather than duplicates, and never touches matches that have kicked off.
    snapshot = {(p.match_id, p.model_version_id, p.market): p.probabilities for p in rows}
    later = now + timedelta(days=17)  # two match days have kicked off by then
    assert generate_live_predictions(session, "PL", now=later) == 48
    session.expire_all()
    rows = session.scalars(select(Prediction)).all()
    assert len(rows) == 56 * 6 and session.scalar(select(func.count()).select_from(ModelVersion)) == 2
    frozen = [p for p in rows if p.generated_at == now]
    assert len(frozen) == 8 * 6
    assert all(snapshot[(p.match_id, p.model_version_id, p.market)] == p.probabilities for p in frozen)

    # Same data and same cutoff -> identical output (reproducible).
    regenerated = {(p.match_id, p.model_version_id, p.market): p.probabilities for p in rows}
    generate_live_predictions(session, "PL", now=later)
    session.expire_all()
    again = {(p.match_id, p.model_version_id, p.market): p.probabilities for p in session.scalars(select(Prediction))}
    assert again == regenerated


def test_unavailable_predictions_are_stored_as_such(session):
    import_records(session, "synthetic", synthetic_season(2022, finished=False))
    session.commit()
    generate_live_predictions(session, "PL", now=datetime(2022, 8, 1, tzinfo=UTC))
    rows = session.scalars(select(Prediction)).all()
    assert rows and all(p.status == "unavailable" and p.probabilities is None and p.reason for p in rows)


# --- evaluation ----------------------------------------------------------------------------------


def test_score_matches_hand_calculation():
    pairs = [({"home": 0.5, "draw": 0.3, "away": 0.2}, "home"), ({"home": 0.2, "draw": 0.2, "away": 0.6}, "draw")]
    result = score(pairs, "1X2")
    assert result["n"] == 2 and result["accuracy"] == 0.5
    assert math.isclose(result["log_loss"], -(math.log(0.5) + math.log(0.2)) / 2)
    assert math.isclose(result["brier"], ((0.25 + 0.09 + 0.04) + (0.04 + 0.64 + 0.36)) / 2)
    assert score([], "1X2") is None
    # A market where one outcome never occurs in the sample must still be scored.
    assert score([({"over": 0.9, "under": 0.1}, "over")], "OU25")["log_loss"] == pytest.approx(-math.log(0.9))


def test_calibration_bins_count_every_prediction():
    pairs = [({"over": 0.65, "under": 0.35}, "over")] * 3 + [({"over": 0.62, "under": 0.38}, "under")]
    bins = calibration(pairs, "OU25")
    assert len(bins) == 1 and bins[0]["count"] == 4
    assert bins[0]["observed_frequency"] == 0.75 and bins[0]["bin_lower"] == pytest.approx(0.6)
    three_way = calibration([({"home": 0.5, "draw": 0.3, "away": 0.2}, "home")], "1X2")
    assert sum(b["count"] for b in three_way) == 3  # each outcome pooled one-vs-rest


def test_implied_probabilities_remove_the_margin():
    implied = implied_probabilities({"home": 2.0, "draw": 4.0, "away": 4.0}, "1X2")
    assert implied == {"home": 0.5, "draw": 0.25, "away": 0.25}
    with_margin = implied_probabilities({"over": 1.9, "under": 1.9}, "OU25")
    assert with_margin == {"over": 0.5, "under": 0.5}
    assert implied_probabilities({"home": 2.0, "draw": 4.0}, "1X2") is None


def test_bet_simulation_arithmetic():
    odds = {"home": 2.0, "draw": 3.5, "away": 4.0}
    bets = [
        {"probs": {"home": 0.6, "draw": 0.2, "away": 0.2}, "odds": odds, "actual": "home"},  # bet, +1.0
        {"probs": {"home": 0.6, "draw": 0.2, "away": 0.2}, "odds": odds, "actual": "away"},  # bet, -1.0
        {"probs": {"home": 0.6, "draw": 0.2, "away": 0.2}, "odds": odds, "actual": "draw"},  # bet, -1.0
        {"probs": {"home": 0.5, "draw": 0.28, "away": 0.22}, "odds": odds, "actual": "home"},  # no edge
    ]
    result = simulate_bets(bets)
    assert result["n_bets"] == 3 and result["matches_with_odds"] == 4
    assert result["profit"] == pytest.approx(-1.0) and result["roi"] == pytest.approx(-1 / 3)
    assert result["max_drawdown"] == pytest.approx(2.0)
    assert result["average_bookmaker_margin"] == pytest.approx(1 / 2 + 1 / 3.5 + 1 / 4 - 1)
    assert simulate_bets([])["roi"] is None


def test_walk_forward_only_trains_on_the_past(three_seasons):
    frame = _frame(three_seasons)
    last_season = set(frame["season_id"].unique()[-1:])
    records = walk_forward(frame, last_season)
    assert len(records) == 56
    previous_cutoff = None
    for record in records:
        row, fitted = record["row"], record["fitted"]
        assert fitted.cutoff <= row.kickoff  # model was fitted before this match kicked off
        assert fitted.cutoff.hour == 0 and fitted.cutoff.date() == row.kickoff.date()
        assert (fitted.known["known_at"] <= pd.Timestamp(fitted.cutoff)).all()
        assert row.match_id not in set(fitted.known["match_id"])
        assert previous_cutoff is None or fitted.cutoff >= previous_cutoff  # chronological order
        previous_cutoff = fitted.cutoff
    assert len(records[-1]["fitted"].known) > len(records[0]["fitted"].known)  # training set grows


def test_run_evaluation_end_to_end(three_seasons):
    session = three_seasons
    run = run_evaluation(session, "PL", n_seasons=1)
    assert run.n_matches == 56 and run.period_start < run.period_end
    results = run.results
    # The Poisson model needs 150 past matches: unavailable for the first 10 match days (40 matches).
    assert results["seasons"] == ["2022-23"] and results["matches_without_primary_prediction"] == 40
    assert set(results["models"]) == {"poisson-goals", "base-rate", "bookmaker-implied"}
    for market in MARKETS:
        overall = results["models"]["poisson-goals"]["markets"][market]["overall"]
        assert overall["n"] == 16 and overall["log_loss"] > 0 and 0 <= overall["accuracy"] <= 1
        assert results["models"]["base-rate"]["markets"][market]["overall"]["n"] == 56
    assert "BTTS" not in results["models"]["bookmaker-implied"]["markets"]  # no BTTS odds -> not invented
    assert results["betting_simulation"]["available"] and results["betting_simulation"]["matches_with_odds"] == 16

    backtests = session.scalars(select(Prediction).where(Prediction.kind == "backtest")).all()
    assert len(backtests) == 56 * 6
    kickoffs = {m.id: m.kickoff_utc for m in session.scalars(select(Match))}
    assert all(p.data_cutoff <= kickoffs[p.match_id] for p in backtests)

    # Re-running replaces backtest rows instead of duplicating them, and gives identical numbers.
    again = run_evaluation(session, "PL", n_seasons=1)
    assert again.results["models"] == results["models"]
    assert session.scalar(select(func.count()).select_from(Prediction)) == 56 * 6
    assert session.scalar(select(func.count()).select_from(EvaluationRun)) == 2
    assert run_evaluation(session, "NOPE") is None


def test_outcomes():
    assert outcomes(2, 1) == {"1X2": "home", "OU25": "over", "BTTS": "yes"}
    assert outcomes(0, 0) == {"1X2": "draw", "OU25": "under", "BTTS": "no"}
    assert outcomes(0, 2) == {"1X2": "away", "OU25": "under", "BTTS": "no"}
