"""Idempotent imports, cross-provider matching, database constraints and migrations. Synthetic data."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError, StatementError

from alembic import command
from app.db import Base
from app.ingest import canonical_team, import_records, run_import
from app.models import ImportRun, Match, MatchProviderRef, MatchStatistics, OddsSnapshot, Team, TeamAlias
from app.providers.base import NormalizedMatch, NormalizedOdds, ProviderError
from tests.conftest import synthetic_season

BACKEND = Path(__file__).parent.parent


def _counts(session):
    return {
        model.__name__: session.scalar(select(func.count()).select_from(model))
        for model in (Match, Team, TeamAlias, MatchProviderRef, MatchStatistics, OddsSnapshot)
    }


def test_import_is_idempotent(session):
    records = synthetic_season(2022)
    first = import_records(session, "synthetic", records)
    session.commit()
    before = _counts(session)
    assert first.created == 56 and before["Match"] == 56 and before["Team"] == 8
    assert before["MatchStatistics"] == 112 and before["OddsSnapshot"] == 168

    second = import_records(session, "synthetic", synthetic_season(2022))
    session.commit()
    assert (second.created, second.updated, second.unchanged, second.odds_added) == (0, 0, 56, 0)
    assert _counts(session) == before


def test_scheduled_match_is_updated_not_duplicated(session):
    kickoff = datetime(2030, 3, 1, 15, 0, tzinfo=UTC)
    scheduled = NormalizedMatch("r1", "PL", 2029, "Team A", "Team B", kickoff, "datetime", "scheduled",
                                odds=[NormalizedOdds("Avg", "1X2", "home", 2.0, "pre_match")])
    import_records(session, "synthetic", [scheduled], fetched_at=kickoff - timedelta(days=3))
    moved = replace(scheduled, kickoff_utc=kickoff + timedelta(hours=2),
                    odds=[NormalizedOdds("Avg", "1X2", "home", 1.9, "pre_match")])
    counts = import_records(session, "synthetic", [moved], fetched_at=kickoff - timedelta(days=1))
    assert (counts.created, counts.updated, counts.odds_added) == (0, 1, 1)
    finished = replace(moved, status="finished", home_goals=1, away_goals=0, odds=[])
    import_records(session, "synthetic", [finished])
    session.commit()

    match = session.scalars(select(Match)).one()
    assert match.status == "finished" and match.kickoff_utc == kickoff + timedelta(hours=2)
    # Both prices are kept, each with the time it was fetched: odds history is never overwritten.
    snapshots = session.scalars(select(OddsSnapshot).order_by(OddsSnapshot.fetched_at)).all()
    assert [(s.decimal_odds, s.fetched_at) for s in snapshots] == [
        (2.0, kickoff - timedelta(days=3)), (1.9, kickoff - timedelta(days=1))]


def test_finished_match_is_not_reverted_and_bad_records_are_skipped(session):
    kickoff = datetime(2030, 3, 1, 15, 0, tzinfo=UTC)
    done = NormalizedMatch("r1", "PL", 2029, "Team A", "Team B", kickoff, "datetime", "finished", 2, 2)
    import_records(session, "synthetic", [done])
    stale = replace(done, status="scheduled", home_goals=None, away_goals=None)
    same_team = NormalizedMatch("r2", "PL", 2029, "Team A", "Team A", kickoff, "datetime", "scheduled")
    counts = import_records(session, "synthetic", [stale, stale, same_team])
    assert session.scalars(select(Match)).one().status == "finished"
    assert len(counts.warnings) == 3  # reverted status, duplicate record, same team


def test_two_providers_resolve_to_one_match(session):
    kickoff = datetime(2030, 3, 1, 15, 0, tzinfo=UTC)
    a = NormalizedMatch("x", "PL", 2029, "Man United", "Nott'm Forest", kickoff, "date", "scheduled")
    b = NormalizedMatch("99", "PL", 2029, "Manchester United FC", "Nottingham Forest FC",
                        kickoff.replace(hour=17), "datetime", "scheduled", matchday=28)
    import_records(session, "provider-a", [a])
    counts = import_records(session, "provider-b", [b])
    session.commit()
    assert counts.created == 0 and counts.updated == 1
    match = session.scalars(select(Match)).one()
    assert match.kickoff_precision == "datetime" and match.matchday == 28
    assert session.scalar(select(func.count()).select_from(Team)) == 2
    assert session.scalar(select(func.count()).select_from(MatchProviderRef)) == 2  # provenance per provider
    # A later date-only record must not erase the precise kick-off time.
    import_records(session, "provider-a", [a])
    assert session.scalars(select(Match)).one().kickoff_utc.hour == 17


def test_canonical_team_names():
    assert canonical_team("Wolves") == canonical_team("Wolverhampton Wanderers FC")
    assert canonical_team("AFC Bournemouth")[0] == canonical_team("Bournemouth")[0]
    assert canonical_team("Brighton")[0] == canonical_team("Brighton & Hove Albion FC")[0]
    assert canonical_team("Arsenal FC") == ("arsenal", "Arsenal")
    assert canonical_team("Manchester City")[0] != canonical_team("Manchester United")[0]


class _FakeAdapter:
    name = "synthetic"
    warnings: list[str] = []

    def __init__(self, error=None):
        self.error = error

    def fetch_matches(self, competition_code, season_start_year):
        if self.error:
            raise self.error
        return synthetic_season(season_start_year)


def test_run_import_records_success_and_failure(session):
    ok = run_import(session, "synthetic", "PL", 2022, adapter=_FakeAdapter())
    assert ok.status == "success" and ok.matches_created == 56 and ok.finished_at is not None
    failed = run_import(session, "synthetic", "PL", 2022, adapter=_FakeAdapter(ProviderError("provider is down")))
    assert failed.status == "failed" and failed.error_message == "provider is down"
    crashed = run_import(session, "synthetic", "PL", 2022, adapter=_FakeAdapter(RuntimeError("secret detail")))
    assert crashed.status == "failed" and "secret detail" not in crashed.error_message
    assert session.scalar(select(func.count()).select_from(ImportRun)) == 3
    assert session.scalar(select(func.count()).select_from(Match)) == 56  # failures changed no data


def test_unknown_competition_fails_cleanly(session):
    record = replace(synthetic_season(2022)[0], competition_code="ZZ")
    with pytest.raises(ProviderError):
        import_records(session, "synthetic", [record])


# --- constraints ---------------------------------------------------------------------------------


@pytest.fixture
def one_match(session):
    import_records(session, "synthetic", synthetic_season(2022)[:1])
    session.commit()
    return session.scalars(select(Match)).one()


def _expect_integrity_error(session, obj):
    session.add(obj)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_match_constraints(session, one_match):
    m = one_match
    base = dict(competition_id=m.competition_id, season_id=m.season_id, kickoff_utc=m.kickoff_utc)
    _expect_integrity_error(session, Match(**base, home_team_id=m.home_team_id, away_team_id=m.away_team_id))
    _expect_integrity_error(session, Match(**base, home_team_id=m.home_team_id, away_team_id=m.home_team_id))
    _expect_integrity_error(
        session, Match(**base, home_team_id=m.away_team_id, away_team_id=m.home_team_id, status="finished"))
    _expect_integrity_error(
        session, Match(**base, home_team_id=m.away_team_id, away_team_id=m.home_team_id, status="bogus"))
    _expect_integrity_error(
        session, Match(**base, home_team_id=m.away_team_id, away_team_id=m.home_team_id, home_goals=-1))
    _expect_integrity_error(session, Match(**base, home_team_id=m.away_team_id, away_team_id=987654))


def test_odds_and_datetime_constraints(session, one_match):
    now = datetime.now(UTC)
    common = dict(match_id=one_match.id, provider="p", bookmaker="b", selection="home", timing="pre_match", fetched_at=now)
    _expect_integrity_error(session, OddsSnapshot(**common, market="1X2", decimal_odds=1.0))
    _expect_integrity_error(session, OddsSnapshot(**common, market="CORNERS", decimal_odds=2.0))
    session.add(OddsSnapshot(**{**common, "fetched_at": datetime(2030, 1, 1)}, market="1X2", decimal_odds=2.0))
    with pytest.raises(StatementError, match="naive datetime"):
        session.flush()
    session.rollback()


def test_migrations_build_the_same_schema_as_the_models(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migrated.db').as_posix()}"
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []
    command.downgrade(config, "base")
    with engine.connect() as connection:
        tables = connection.exec_driver_sql("select name from sqlite_master where type='table'").scalars().all()
    engine.dispose()
    assert set(tables) == {"alembic_version"}
