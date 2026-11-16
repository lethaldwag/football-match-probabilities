"""Adapter for the CSV files published by Football-Data.co.uk.

Files used (official downloads, no scraping):
  - https://www.football-data.co.uk/mmz4281/<season>/<division>.csv  (results, stats, odds)
  - https://www.football-data.co.uk/fixtures.csv                      (upcoming fixtures with odds)

See docs/data-providers.md for fields, limits and licensing notes.
"""

import csv
import io
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from app.providers.base import (
    NormalizedMatch,
    NormalizedOdds,
    NormalizedTeamStats,
    ProviderError,
    RateLimitError,
)
from app.providers.http import tls_context

BASE_URL = "https://www.football-data.co.uk"
DIVISIONS = {"PL": "E0", "PD": "SP1", "SA": "I1", "BL1": "D1", "FL1": "F1"}
# The provider's notes describe kick-off times as UK time; treated as an assumption (see docs).
SOURCE_TZ = ZoneInfo("Europe/London")
DEFAULT_BOOKMAKERS = ("B365", "PS", "Avg", "Max", "BbAv", "BbMx")

_STAT_COLUMNS = {
    "shots": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "corners": ("HC", "AC"),
    "fouls": ("HF", "AF"),
    "yellow_cards": ("HY", "AY"),
    "red_cards": ("HR", "AR"),
}


def _int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def _odds_value(value: str | None) -> float | None:
    try:
        number = float(value) if value not in (None, "") else None
    except ValueError:
        return None
    return number if number is not None and number > 1 else None


def season_of(date: datetime) -> int:
    """Season start year for a European league match date (seasons start in July or later)."""
    return date.year if date.month >= 7 else date.year - 1


def _parse_kickoff(date_text: str, time_text: str | None) -> tuple[datetime, str]:
    date_text = date_text.strip()
    day = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            day = datetime.strptime(date_text, fmt)
            break
        except ValueError:
            continue
    if day is None:
        raise ValueError(f"unrecognised date {date_text!r}")
    if time_text and time_text.strip():
        hours, minutes = time_text.strip().split(":")[:2]
        local = day.replace(hour=int(hours), minute=int(minutes), tzinfo=SOURCE_TZ)
        return local.astimezone(UTC), "datetime"
    return day.replace(tzinfo=UTC), "date"


def _row_odds(row: dict, bookmakers: tuple[str, ...]) -> list[NormalizedOdds]:
    out: list[NormalizedOdds] = []
    for bk in bookmakers:
        for timing, mid in (("pre_match", ""), ("closing", "C")):
            columns = {
                ("1X2", "home"): f"{bk}{mid}H",
                ("1X2", "draw"): f"{bk}{mid}D",
                ("1X2", "away"): f"{bk}{mid}A",
                ("OU25", "over"): f"{bk}{mid}>2.5",
                ("OU25", "under"): f"{bk}{mid}<2.5",
            }
            for (market, selection), column in columns.items():
                price = _odds_value(row.get(column))
                if price is not None:
                    out.append(NormalizedOdds(bk, market, selection, price, timing))
    return out


def parse_csv(
    text: str,
    competition_code: str,
    season_start_year: int | None = None,
    bookmakers: tuple[str, ...] = DEFAULT_BOOKMAKERS,
    season_from_dates: bool = False,
) -> tuple[list[NormalizedMatch], list[str]]:
    """Parse a results or fixtures CSV into normalised matches.

    Rows for other divisions are ignored. A season file belongs entirely to `season_start_year`
    (seasons can overrun into July, e.g. 2019-20). For the fixtures file, which is not
    season-specific, pass `season_from_dates=True`: the season is derived from each date and rows
    from other seasons are ignored. Returns (matches, warnings).
    """
    division = DIVISIONS[competition_code]
    matches: list[NormalizedMatch] = []
    warnings: list[str] = []
    reader = csv.DictReader(io.StringIO(text.lstrip(chr(0xFEFF))))
    for line_no, row in enumerate(reader, start=2):
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        if row.get("Div") != division:
            continue
        home, away = row.get("HomeTeam", ""), row.get("AwayTeam", "")
        if not home or not away or not row.get("Date"):
            warnings.append(f"line {line_no}: missing team or date; row skipped")
            continue
        try:
            kickoff, precision = _parse_kickoff(row["Date"], row.get("Time"))
        except ValueError as exc:
            warnings.append(f"line {line_no}: {exc}; row skipped")
            continue
        if season_from_dates or season_start_year is None:
            season = season_of(kickoff.astimezone(SOURCE_TZ))
            if season_start_year is not None and season != season_start_year:
                continue
        else:
            season = season_start_year

        home_goals, away_goals = _int(row.get("FTHG")), _int(row.get("FTAG"))
        finished = home_goals is not None and away_goals is not None
        stats = [
            NormalizedTeamStats(**{name: _int(row.get(cols[side])) for name, cols in _STAT_COLUMNS.items()})
            for side in (0, 1)
        ]
        notes = "Match id synthesised from division, season and team names (the file has no ids). "
        notes += (
            "Kick-off converted from UK local time to UTC."
            if precision == "datetime"
            else "File has no kick-off time; stored as midnight UTC with date precision."
        )
        matches.append(
            NormalizedMatch(
                provider_match_ref=f"{division}:{season}:{home}:{away}",
                competition_code=competition_code,
                season_start_year=season,
                home_team=home,
                away_team=away,
                kickoff_utc=kickoff,
                kickoff_precision=precision,
                status="finished" if finished else "scheduled",
                home_goals=home_goals if finished else None,
                away_goals=away_goals if finished else None,
                transform_notes=notes,
                raw=row,
                home_stats=None if stats[0].is_empty() else stats[0],
                away_stats=None if stats[1].is_empty() else stats[1],
                odds=_row_odds(row, bookmakers),
            )
        )
    return matches, warnings


class FootballDataUkAdapter:
    name = "football-data-uk"

    def __init__(self, bookmakers: tuple[str, ...] = DEFAULT_BOOKMAKERS, timeout: float = 30.0):
        self.bookmakers = bookmakers
        self.timeout = timeout
        self.warnings: list[str] = []

    def supports(self, competition_code: str) -> bool:
        return competition_code in DIVISIONS

    def _download(self, url: str, allow_missing: bool = False) -> str | None:
        try:
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True, verify=tls_context())
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not download {url}: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            raise RateLimitError(f"rate limited by provider for {url}")
        if response.status_code == 404 and allow_missing:
            return None
        if response.status_code != 200:
            raise ProviderError(f"unexpected HTTP {response.status_code} for {url}")
        return response.content.decode("utf-8-sig", errors="replace")

    def fetch_matches(self, competition_code: str, season_start_year: int) -> list[NormalizedMatch]:
        if not self.supports(competition_code):
            raise ProviderError(f"{self.name} has no file for competition {competition_code}")
        self.warnings = []
        yy = f"{season_start_year % 100:02d}{(season_start_year + 1) % 100:02d}"
        text = self._download(f"{BASE_URL}/mmz4281/{yy}/{DIVISIONS[competition_code]}.csv")
        matches, warnings = parse_csv(text, competition_code, season_start_year, self.bookmakers)
        self.warnings += warnings
        if not matches:
            raise ProviderError("season file contained no rows for this competition")

        # Upcoming fixtures live in a separate file that covers only the next few days.
        fixtures_text = self._download(f"{BASE_URL}/fixtures.csv", allow_missing=True)
        if fixtures_text:
            fixtures, warnings = parse_csv(
                fixtures_text, competition_code, season_start_year, self.bookmakers, season_from_dates=True
            )
            self.warnings += warnings
            known = {m.provider_match_ref for m in matches}
            matches += [m for m in fixtures if m.provider_match_ref not in known]
        return matches
