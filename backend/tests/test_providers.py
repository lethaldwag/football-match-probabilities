"""Provider normalisation. Fixture files and payloads are synthetic (see conftest docstring)."""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.providers import get_adapter
from app.providers.base import ProviderError, RateLimitError
from app.providers.football_data_org import FootballDataOrgAdapter, parse_matches
from app.providers.football_data_uk import FootballDataUkAdapter, parse_csv

CSV = (Path(__file__).parent / "fixtures" / "SYNTHETIC_fduk_sample.csv").read_text(encoding="utf-8")


def test_fduk_normalises_rows_and_reports_bad_ones():
    matches, warnings = parse_csv(chr(0xFEFF) + CSV, "PL", 2025)
    assert [m.home_team for m in matches] == ["Synthetic Rovers", "Fictional City", "Made Up United", "Fictional City"]
    assert len(warnings) == 1 and "not-a-date" in warnings[0]  # bad row reported, not silently dropped

    first = matches[0]
    assert first.status == "finished" and (first.home_goals, first.away_goals) == (2, 1)
    # 15:00 UK time in August (BST, UTC+1) is 14:00 UTC; in January (GMT) it is unchanged.
    assert first.kickoff_utc == datetime(2025, 8, 16, 14, 0, tzinfo=UTC)
    assert matches[1].kickoff_utc == datetime(2026, 1, 10, 17, 30, tzinfo=UTC)
    assert first.kickoff_precision == "datetime"
    assert first.provider_match_ref == "E0:2025:Synthetic Rovers:Fictional City"
    assert first.home_stats.shots == 12 and first.away_stats.red_cards == 0
    assert first.raw["FTHG"] == "2"  # raw row retained for provenance


def test_fduk_odds_are_labelled_by_timing_and_never_asian_handicap():
    first = parse_csv(CSV, "PL", 2025)[0][0]
    odds = {(o.bookmaker, o.market, o.selection, o.timing): o.decimal_odds for o in first.odds}
    assert odds[("B365", "1X2", "home", "pre_match")] == 2.10
    assert odds[("Avg", "OU25", "over", "pre_match")] == 1.90
    assert odds[("Avg", "1X2", "away", "closing")] == 3.80
    assert ("B365", "1X2", "home", "closing") not in odds  # column absent -> no invented price
    assert all(o.captured_at is None for o in first.odds)  # provider gives no quote time
    assert len(odds) == 11


def test_fduk_scheduled_rows_and_invalid_prices():
    matches, _ = parse_csv(CSV, "PL", 2025)
    upcoming = matches[2]
    assert upcoming.status == "scheduled" and upcoming.home_goals is None and upcoming.home_stats is None
    overrun = matches[3]  # a July match in the 2025-26 season file stays in 2025-26
    assert overrun.season_start_year == 2025
    assert [o.decimal_odds for o in overrun.odds] == [3.0]  # 0.99 and "abc" rejected


def test_fduk_fixture_file_derives_season_from_dates():
    matches, _ = parse_csv(CSV, "PL", 2025, season_from_dates=True)
    assert len(matches) == 3  # the July 2026 row belongs to 2026-27 and is filtered out
    assert parse_csv(CSV, "PL", 2026, season_from_dates=True)[0][0].season_start_year == 2026


def test_fduk_date_only_rows_get_date_precision():
    text = "Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\nE0,08/08/15,Synthetic Rovers,Fictional City,0,1\n"
    match = parse_csv(text, "PL", 2015)[0][0]
    assert match.kickoff_precision == "date" and match.kickoff_utc == datetime(2015, 8, 8, tzinfo=UTC)


def _fake_get(status=200, text="", json_body=None):
    def get(url, **kwargs):
        request = httpx.Request("GET", url)
        if json_body is not None:
            return httpx.Response(status, json=json_body, request=request)
        return httpx.Response(status, text=text, request=request)
    return get


def test_fduk_adapter_handles_provider_errors(monkeypatch):
    adapter = FootballDataUkAdapter()
    monkeypatch.setattr(httpx, "get", _fake_get(429))
    with pytest.raises(RateLimitError):
        adapter.fetch_matches("PL", 2025)
    monkeypatch.setattr(httpx, "get", _fake_get(500))
    with pytest.raises(ProviderError):
        adapter.fetch_matches("PL", 2025)

    def boom(url, **kwargs):
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(httpx, "get", boom)
    with pytest.raises(ProviderError):
        adapter.fetch_matches("PL", 2025)
    with pytest.raises(ProviderError):
        adapter.fetch_matches("CL", 2025)  # competition not offered by this provider


def test_fduk_adapter_merges_fixture_file(monkeypatch):
    monkeypatch.setattr(httpx, "get", _fake_get(200, text=CSV))
    matches = FootballDataUkAdapter().fetch_matches("PL", 2025)
    refs = [m.provider_match_ref for m in matches]
    assert len(refs) == len(set(refs)) == 4  # same file served twice -> no duplicates


# Hand-written payload in the shape documented for football-data.org v4. Synthetic content.
ORG_PAYLOAD = {
    "matches": [
        {"id": 1001, "utcDate": "2025-08-16T14:00:00Z", "status": "FINISHED", "matchday": 1,
         "stage": "REGULAR_SEASON", "lastUpdated": "2025-08-16T17:00:00Z",
         "homeTeam": {"id": 1, "name": "Synthetic Rovers FC"}, "awayTeam": {"id": 2, "name": "Fictional City FC"},
         "score": {"fullTime": {"home": 2, "away": 1}}},
        {"id": 1002, "utcDate": "2026-02-21T12:30:00Z", "status": "TIMED", "matchday": 26,
         "stage": "REGULAR_SEASON", "lastUpdated": "2026-01-01T00:00:00Z",
         "homeTeam": {"id": 3, "name": "Made Up United FC"}, "awayTeam": {"id": 1, "name": "Synthetic Rovers FC"},
         "score": {"fullTime": {"home": None, "away": None}}},
        {"id": 1003, "utcDate": "2025-09-01T14:00:00Z", "status": "FINISHED",
         "homeTeam": {"id": 2, "name": "Fictional City FC"}, "awayTeam": {"id": 3, "name": "Made Up United FC"},
         "score": {"fullTime": {"home": None, "away": None}}},
        {"id": 1004, "status": "TIMED", "homeTeam": {"name": None}, "awayTeam": {"name": "X"}},
    ]
}


def test_org_normalises_payload():
    matches, warnings = parse_matches(ORG_PAYLOAD, "PL", 2025)
    assert len(matches) == 3 and len(warnings) == 2
    done, upcoming, no_score = matches
    assert done.status == "finished" and (done.home_goals, done.away_goals) == (2, 1)
    assert done.provider_match_ref == "1001" and done.stage == "regular" and done.matchday == 1
    assert done.source_updated_at == datetime(2025, 8, 16, 17, 0, tzinfo=UTC)
    assert upcoming.status == "scheduled" and upcoming.home_goals is None
    assert no_score.status == "scheduled"  # "finished" without a score is not trusted


def test_org_adapter_requires_key_and_maps_errors(monkeypatch):
    with pytest.raises(ProviderError, match="not configured"):
        FootballDataOrgAdapter(api_key="").fetch_matches("PL", 2025)
    adapter = FootballDataOrgAdapter(api_key="dummy-test-key")
    monkeypatch.setattr(httpx, "get", _fake_get(429))
    with pytest.raises(RateLimitError):
        adapter.fetch_matches("PL", 2025)
    monkeypatch.setattr(httpx, "get", _fake_get(403))
    with pytest.raises(ProviderError) as info:
        adapter.fetch_matches("PL", 2025)
    assert "dummy-test-key" not in str(info.value)  # credentials never appear in error messages
    monkeypatch.setattr(httpx, "get", _fake_get(200, json_body=ORG_PAYLOAD))
    assert len(adapter.fetch_matches("PL", 2025)) == 3


def test_registry():
    assert get_adapter("football-data-uk").name == "football-data-uk"
    with pytest.raises(ProviderError):
        get_adapter("nope")
