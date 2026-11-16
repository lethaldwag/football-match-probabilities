"""API validation, error shape, pagination, data-state reporting and admin protection. Synthetic data."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.ingest as ingest
from app.db import get_session, utcnow
from app.ingest import import_records
from app.main import RateLimiter, create_app
from app.modeling.evaluate import run_evaluation
from app.modeling.predict import generate_live_predictions
from app.models import Match, Prediction
from app.providers.base import NormalizedMatch, NormalizedOdds, ProviderError
from tests.conftest import synthetic_season

ADMIN = {"X-Admin-Token": "test-admin-token"}


@pytest.fixture
def client(session):
    app = create_app()
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def populated(three_seasons, client, monkeypatch):
    """Three finished synthetic seasons, an evaluation, and two upcoming matches with predictions."""
    session = three_seasons
    # The synthetic seasons are dated 2020-2023 but the upcoming matches must be in the real future,
    # so the models' 4-year lookback window is lifted for this fixture only.
    import app.modeling.predict as predict
    from app.modeling.features import known_before
    monkeypatch.setattr(predict, "known_before", lambda frame, cutoff, lookback_days=None: known_before(frame, cutoff))
    run_evaluation(session, "PL", n_seasons=1)
    soon = utcnow() + timedelta(days=2)
    upcoming = [
        NormalizedMatch("up1", "PL", 2023, "Team A", "Team B", soon, "datetime", "scheduled",
                        odds=[NormalizedOdds("Avg", "1X2", s, o, "pre_match")
                              for s, o in (("home", 2.5), ("draw", 3.2), ("away", 2.9))]),
        NormalizedMatch("up2", "PL", 2023, "Team C", "Team D", soon + timedelta(days=1), "datetime", "scheduled"),
    ]
    import_records(session, "synthetic", upcoming)
    session.commit()
    generate_live_predictions(session, "PL")
    return client


def test_health_and_openapi(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/matches" in paths and "/api/v1/evaluation/latest" in paths


def test_empty_database_is_reported_honestly(client):
    status = client.get("/api/v1/status").json()
    assert status["data_state"] == "never_imported" and status["last_successful_import"] is None
    assert status["upcoming_matches"] == 0 and "not betting" in status["disclaimer"]
    assert client.get("/api/v1/competitions").json() == []
    response = client.get("/api/v1/matches")
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=101", "offset=-1", "status=won", "date_from=yesterday", "season=12", "order=up",
     "competition=PL;drop", "date_from=2024-02-01&date_to=2024-01-01"],
)
def test_invalid_query_parameters_get_a_consistent_error(populated, query):
    response = populated.get(f"/api/v1/matches?{query}")
    assert response.status_code == 422
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details"} and error["code"] == "validation_error"


def test_not_found_and_unknown_routes(populated):
    for path in ("/api/v1/matches/999999", "/api/v1/teams/999999/form", "/api/v1/nothing",
                 "/api/v1/evaluation/latest?competition=SA", "/api/v1/matches/999999/predictions"):
        response = populated.get(path)
        assert response.status_code == 404 and response.json()["error"]["code"] == "not_found", path
    assert populated.get("/api/v1/matches/abc").status_code == 422


def test_match_listing_filters_and_pagination(populated):
    page = populated.get("/api/v1/matches?limit=10").json()
    assert page["total"] == 170 and len(page["items"]) == 10 and page["limit"] == 10
    kickoffs = [m["kickoff_utc"] for m in page["items"]]
    assert kickoffs == sorted(kickoffs)
    second = populated.get("/api/v1/matches?limit=10&offset=10").json()
    assert {m["id"] for m in page["items"]}.isdisjoint(m["id"] for m in second["items"])

    assert populated.get("/api/v1/matches?season=2021").json()["total"] == 56
    assert populated.get("/api/v1/matches?status=scheduled").json()["total"] == 2
    assert populated.get("/api/v1/matches?date_from=2020-08-10&date_to=2020-08-10").json()["total"] == 4
    assert populated.get("/api/v1/matches?competition=pl&status=postponed").json()["total"] == 0


def test_prediction_states_missing_ok_and_stale(populated, session):
    upcoming = populated.get("/api/v1/matches?status=scheduled").json()["items"]
    assert [m["prediction_state"] for m in upcoming] == ["ok", "ok"]
    prediction = upcoming[0]["predictions"][0]
    assert prediction["kind"] == "live" and prediction["model"] == "poisson-goals"
    assert abs(sum(prediction["probabilities"].values()) - 1) < 1e-9
    assert prediction["generated_at"] and prediction["data_cutoff"] and prediction["model_version"]

    # Finished match outside the evaluated season: no prediction exists, and the API says so.
    old = populated.get("/api/v1/matches?season=2020&limit=1").json()["items"][0]
    assert old["prediction_state"] == "missing" and old["predictions"] == []
    # Finished match inside the evaluated season: backtest, labelled as such.
    tested = populated.get("/api/v1/matches?season=2022&order=desc&limit=1").json()["items"][0]
    assert tested["prediction_state"] == "ok" and {p["kind"] for p in tested["predictions"]} == {"backtest"}

    # Age the live predictions beyond the staleness threshold.
    for p in session.scalars(select(Prediction).where(Prediction.kind == "live")):
        p.generated_at = utcnow() - timedelta(hours=100)
    session.commit()
    upcoming = populated.get("/api/v1/matches?status=scheduled").json()["items"]
    assert [m["prediction_state"] for m in upcoming] == ["stale", "stale"]


def test_overdue_scheduled_match_gets_a_data_note(populated, session):
    match = session.scalars(select(Match).where(Match.status == "scheduled")).first()
    match.kickoff_utc = utcnow() - timedelta(days=1)
    session.commit()
    item = populated.get(f"/api/v1/matches/{match.id}").json()
    assert "no result has been imported" in item["data_note"]


def test_match_detail(populated):
    upcoming = populated.get("/api/v1/matches?status=scheduled").json()["items"]
    detail = populated.get(f"/api/v1/matches/{upcoming[0]['id']}").json()
    assert len(detail["predictions"]) == 3 and len(detail["all_predictions"]) == 6
    assert {o["selection"]: o["decimal_odds"] for o in detail["odds"]} == {"home": 2.5, "draw": 3.2, "away": 2.9}
    odd = detail["odds"][0]
    assert odd["provider"] == "synthetic" and odd["timing"] == "pre_match" and odd["captured_at"] is None
    assert odd["fetched_at"] and odd["implied_probability"] == pytest.approx(1 / odd["decimal_odds"])
    assert detail["statistics"] == []  # no statistics exist before a match; nothing is invented
    assert len(detail["home_form"]["matches"]) == 5 and len(detail["features"]) == 2
    assert all(m["kickoff_utc"] < detail["kickoff_utc"] for m in detail["home_form"]["matches"])
    assert detail["provenance"][0]["provider"] == "synthetic" and "uncertain" in detail["disclaimer"]

    without_odds = populated.get(f"/api/v1/matches/{upcoming[1]['id']}").json()
    assert without_odds["odds"] == []

    finished = populated.get("/api/v1/matches?season=2022&order=desc&limit=1").json()["items"][0]
    detail = populated.get(f"/api/v1/matches/{finished['id']}").json()
    assert len(detail["statistics"]) == 2 and detail["statistics"][0]["provider"] == "synthetic"
    only_btts = populated.get(f"/api/v1/matches/{finished['id']}/predictions?market=BTTS").json()
    assert {p["market"] for p in only_btts} == {"BTTS"} and len(only_btts) == 2
    assert populated.get(f"/api/v1/matches/{finished['id']}/predictions?market=XYZ").status_code == 422


def test_team_form_respects_before(populated):
    team_id = populated.get("/api/v1/matches?limit=1").json()["items"][0]["home_team"]["id"]
    form = populated.get(f"/api/v1/teams/{team_id}/form?before=2021-01-01T00:00:00Z&limit=3").json()
    assert len(form["matches"]) == 3 and all(m["kickoff_utc"] < "2021-01-01" for m in form["matches"])
    assert populated.get(f"/api/v1/teams/{team_id}/form?before=2000-01-01T00:00:00Z").json()["matches"] == []
    assert populated.get(f"/api/v1/teams/{team_id}/form?limit=50").status_code == 422


def test_evaluation_and_competitions(populated):
    evaluation = populated.get("/api/v1/evaluation/latest").json()
    assert evaluation["n_matches"] == 56 and "Walk-forward" in evaluation["results"]["method"]
    competitions = populated.get("/api/v1/competitions").json()
    assert competitions[0]["code"] == "PL" and len(competitions[0]["seasons"]) == 4


def test_admin_requires_token_and_reports_import_outcome(populated, monkeypatch):
    body = {"provider": "football-data-uk", "competition": "PL", "season": 2024}
    assert populated.get("/api/v1/admin/imports").status_code == 401
    assert populated.post("/api/v1/admin/imports", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert populated.post("/api/v1/admin/imports", json={**body, "provider": "x"}, headers=ADMIN).status_code == 422
    assert populated.post("/api/v1/admin/imports", json={**body, "season": 5}, headers=ADMIN).status_code == 422

    class Fake:
        name, warnings = "football-data-uk", []

        def fetch_matches(self, competition_code, season_start_year):
            if season_start_year == 2025:
                raise ProviderError("provider unreachable")
            return synthetic_season(season_start_year)

    monkeypatch.setattr(ingest, "get_adapter", lambda name: Fake())
    created = populated.post("/api/v1/admin/imports", json=body, headers=ADMIN)
    assert created.status_code == 201 and created.json()["status"] == "success"
    assert created.json()["matches_created"] == 56
    failed = populated.post("/api/v1/admin/imports", json={**body, "season": 2025}, headers=ADMIN).json()
    assert failed["status"] == "failed" and failed["error_message"] == "provider unreachable"
    runs = populated.get("/api/v1/admin/imports", headers=ADMIN).json()
    assert [r["status"] for r in runs] == ["failed", "success"]

    status = populated.get("/api/v1/status").json()
    assert status["data_state"] == "fresh" and status["last_import_attempt"]["status"] == "failed"
    assert status["last_successful_import"]["season_start_year"] == 2024


def test_unhandled_errors_do_not_leak_details(client, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("secret internal detail")
    monkeypatch.setattr("app.api.utcnow", explode)
    response = client.get("/api/v1/status")
    assert response.status_code == 500
    assert response.json() == {"error": {"code": "error", "message": "Internal server error", "details": []}}


def test_rate_limiter():
    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("a") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("b")
