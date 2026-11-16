"""openfootball adapter and the two-provider merge rules. All payloads here are synthetic."""

from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.ingest import import_records
from app.models import Match
from app.providers import get_adapter
from app.providers.base import NormalizedMatch, ProviderError, RateLimitError
from app.providers.openfootball import OpenFootballAdapter, parse_matches

PAYLOAD = {
    "name": "Synthetic League 2025/26",
    "matches": [
        {"round": "Matchday 1", "date": "2025-08-16", "time": "15:00", "team1": "Synthetic Rovers FC",
         "team2": "Fictional City FC", "score": {"ht": [1, 0], "ft": [2, 1]}},
        {"round": "Matchday 2", "date": "2025-08-23", "time": "17:30", "team1": "Fictional City FC",
         "team2": "Made Up United FC", "score": [0, 0]},
        {"round": "Matchday 20", "date": "2026-01-10", "time": "20:00", "team1": "Made Up United FC",
         "team2": "Synthetic Rovers FC"},
        {"round": "Final", "date": "2026-05-24", "team1": "Synthetic Rovers FC", "team2": "Made Up United FC",
         "score": {"ft": [1]}},
        {"round": "Matchday 3", "date": "soon", "team1": "Synthetic Rovers FC", "team2": "Fictional City FC"},
        {"round": "Matchday 4", "date": "2025-09-01", "team1": "", "team2": "Fictional City FC"},
    ],
}


def test_parse_matches():
    matches, warnings = parse_matches(PAYLOAD, "PL", 2025)
    assert len(matches) == 4 and len(warnings) == 2  # bad rows reported, not silently dropped
    done, bare_list, upcoming, no_time = matches
    assert done.status == "finished" and (done.home_goals, done.away_goals) == (2, 1) and done.matchday == 1
    assert done.kickoff_utc == datetime(2025, 8, 16, 14, 0, tzinfo=UTC)  # 15:00 BST
    assert done.provider_match_ref == "en.1:2025:Synthetic Rovers FC:Fictional City FC"
    assert bare_list.status == "finished" and (bare_list.home_goals, bare_list.away_goals) == (0, 0)
    assert upcoming.status == "scheduled" and upcoming.home_goals is None
    assert upcoming.kickoff_utc == datetime(2026, 1, 10, 20, 0, tzinfo=UTC)  # GMT in January
    # No time -> date precision; malformed score -> not treated as a result; "Final" -> no matchday.
    assert no_time.kickoff_precision == "date" and no_time.status == "scheduled" and no_time.matchday is None
    assert done.raw["round"] == "Matchday 1"


def _fake_get(status=200, body=None):
    def get(url, **kwargs):
        return httpx.Response(status, json=body if body is not None else {}, request=httpx.Request("GET", url))
    return get


def test_adapter_fetch_and_errors(monkeypatch):
    adapter = get_adapter("openfootball")
    assert isinstance(adapter, OpenFootballAdapter)
    seen = {}

    def capture(url, **kwargs):
        seen["url"] = url
        return _fake_get(200, PAYLOAD)(url)

    monkeypatch.setattr(httpx, "get", capture)
    assert len(adapter.fetch_matches("PL", 2025)) == 4 and len(adapter.warnings) == 2
    assert seen["url"].endswith("/2025-26/en.1.json")

    for status, error in ((404, ProviderError), (429, RateLimitError), (500, ProviderError)):
        monkeypatch.setattr(httpx, "get", _fake_get(status))
        with pytest.raises(error):
            adapter.fetch_matches("PL", 2025)
    monkeypatch.setattr(httpx, "get", _fake_get(200, {"matches": []}))
    with pytest.raises(ProviderError, match="no usable matches"):
        adapter.fetch_matches("PL", 2025)
    with pytest.raises(ProviderError):
        adapter.fetch_matches("CL", 2025)


def test_lagging_provider_cannot_move_or_reopen_a_finished_match(session):
    """A fixture source that has not caught up with a result must not undo it, in any import order."""
    played = datetime(2026, 2, 3, 20, 0, tzinfo=UTC)  # the match was rearranged and played here
    original = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)  # stale time still held by the fixture source
    result = NormalizedMatch("a", "PL", 2025, "Team A", "Team B", played, "datetime", "finished", 3, 1)
    stale = NormalizedMatch("b", "PL", 2025, "Team A FC", "Team B FC", original, "datetime", "scheduled", matchday=20)

    import_records(session, "results-source", [result])
    for _ in range(2):  # repeated cycles must settle, not flip back and forth
        fixtures = import_records(session, "fixture-source", [stale])
        results = import_records(session, "results-source", [result])
    session.commit()
    match = session.scalars(select(Match)).one()
    assert (match.status, match.home_goals, match.away_goals, match.kickoff_utc) == ("finished", 3, 1, played)
    assert match.matchday == 20  # harmless extra detail from the fixture source is still merged
    assert (fixtures.updated, results.updated) == (0, 0)


def test_fixture_source_then_result_source(session):
    kickoff = datetime(2026, 1, 10, 15, 0, tzinfo=UTC)
    fixture = NormalizedMatch("b", "PL", 2025, "Team A FC", "Team B FC", kickoff, "datetime", "scheduled")
    result = NormalizedMatch("a", "PL", 2025, "Team A", "Team B", kickoff, "datetime", "finished", 0, 2)
    import_records(session, "fixture-source", [fixture])
    counts = import_records(session, "results-source", [result])
    assert (counts.created, counts.updated) == (0, 1)
    assert session.scalars(select(Match)).one().status == "finished"
