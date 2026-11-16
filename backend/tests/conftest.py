"""Shared test fixtures.

ALL DATA USED IN TESTS IS SYNTHETIC. Teams ("Team A"...), results and odds are generated from a
seeded random number generator or written by hand; none of it is real football data.
"""

import os

os.environ["DATABASE_URL"] = "sqlite://"  # never touch a real database from tests
os.environ["RATE_LIMIT_PER_MINUTE"] = "0"
os.environ["ADMIN_API_TOKEN"] = "test-admin-token"
os.environ["FOOTBALL_DATA_ORG_API_KEY"] = ""

from datetime import UTC, datetime, timedelta  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import Base  # noqa: E402
from app.providers.base import NormalizedMatch, NormalizedOdds, NormalizedTeamStats  # noqa: E402

TEAMS = [f"Team {c}" for c in "ABCDEFGH"]


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session(engine):
    with Session(engine, expire_on_commit=False) as session:
        yield session


def synthetic_season(start_year: int, seed: int = 0, finished: bool = True) -> list[NormalizedMatch]:
    """A double round-robin between 8 synthetic teams: 56 matches, 4 per match day, weekly."""
    rng = np.random.default_rng(seed + start_year)
    strength = {team: 0.6 + 0.2 * i for i, team in enumerate(TEAMS)}  # Team H strongest
    pairs = [(h, a) for h in TEAMS for a in TEAMS if h != a]
    rng.shuffle(pairs)
    first_day = datetime(start_year, 8, 10, 15, 0, tzinfo=UTC)
    records = []
    for i, (home, away) in enumerate(pairs):
        home_goals = int(rng.poisson(1.4 * strength[home] / strength[away]))
        away_goals = int(rng.poisson(1.1 * strength[away] / strength[home]))
        records.append(
            NormalizedMatch(
                provider_match_ref=f"SYN:{start_year}:{home}:{away}",
                competition_code="PL",
                season_start_year=start_year,
                home_team=home,
                away_team=away,
                kickoff_utc=first_day + timedelta(days=7 * (i // 4)),
                kickoff_precision="datetime",
                status="finished" if finished else "scheduled",
                home_goals=home_goals if finished else None,
                away_goals=away_goals if finished else None,
                raw={"synthetic": True},
                home_stats=NormalizedTeamStats(shots=10, shots_on_target=4) if finished else None,
                away_stats=NormalizedTeamStats(shots=8, shots_on_target=3) if finished else None,
                odds=[
                    NormalizedOdds("Avg", "1X2", "home", 2.1, "pre_match"),
                    NormalizedOdds("Avg", "1X2", "draw", 3.4, "pre_match"),
                    NormalizedOdds("Avg", "1X2", "away", 3.6, "pre_match"),
                ],
            )
        )
    return records


@pytest.fixture
def three_seasons(session):
    """Three synthetic finished seasons (168 matches) imported into the test database."""
    from app.ingest import import_records

    for year in (2020, 2021, 2022):
        import_records(session, "synthetic", synthetic_season(year))
    session.commit()
    return session
