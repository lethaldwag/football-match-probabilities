"""Time-aware feature inputs.

Everything a model sees goes through `known_before(frame, cutoff)`, which keeps only matches whose
result we treat as known at the cutoff. That one function is the leakage guard for both live
predictions and historical backtests.
"""

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, result_known_at

FORM_MATCHES = 5


def load_match_frame(session: Session, competition_id: int) -> pd.DataFrame:
    """All matches of a competition as a DataFrame (one row per match)."""
    rows = session.execute(
        select(
            Match.id, Match.season_id, Match.kickoff_utc, Match.kickoff_precision, Match.status,
            Match.home_team_id, Match.away_team_id, Match.home_goals, Match.away_goals,
        ).where(Match.competition_id == competition_id)
    ).all()
    frame = pd.DataFrame(
        rows,
        columns=["match_id", "season_id", "kickoff", "precision", "status", "home_id", "away_id",
                 "home_goals", "away_goals"],
    )
    if frame.empty:
        frame["known_at"] = pd.Series(dtype="datetime64[ns, UTC]")
        return frame
    frame["kickoff"] = pd.to_datetime(frame["kickoff"], utc=True)
    frame["known_at"] = pd.to_datetime(
        [result_known_at(k.to_pydatetime(), p) for k, p in zip(frame["kickoff"], frame["precision"], strict=True)],
        utc=True,
    )
    return frame.sort_values(["kickoff", "match_id"]).reset_index(drop=True)


def known_before(frame: pd.DataFrame, cutoff: datetime, lookback_days: int | None = None) -> pd.DataFrame:
    """Finished matches whose result was known at `cutoff` (optionally within a lookback window)."""
    cutoff = pd.Timestamp(cutoff)
    mask = (frame["status"] == "finished") & (frame["known_at"] <= cutoff)
    if lookback_days is not None:
        mask &= frame["kickoff"] >= cutoff - pd.Timedelta(days=lookback_days)
    out = frame[mask].copy()
    out["home_goals"] = out["home_goals"].astype(int)
    out["away_goals"] = out["away_goals"].astype(int)
    return out


def team_form(known: pd.DataFrame, team_id: int, n: int = FORM_MATCHES) -> dict:
    """Points and goals over the team's last `n` matches within an already cutoff-filtered frame."""
    games = known[(known["home_id"] == team_id) | (known["away_id"] == team_id)]
    recent = games.tail(n)
    points = goals_for = goals_against = 0
    for row in recent.itertuples():
        mine, theirs = (
            (row.home_goals, row.away_goals) if row.home_id == team_id else (row.away_goals, row.home_goals)
        )
        goals_for += int(mine)
        goals_against += int(theirs)
        points += 3 if mine > theirs else 1 if mine == theirs else 0
    return {
        "matches_in_window": int(len(games)),
        "form_matches": int(len(recent)),
        "form_points": points,
        "form_goals_for": goals_for,
        "form_goals_against": goals_against,
    }
