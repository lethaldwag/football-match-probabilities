"""Adapter for the openfootball/football.json public dataset (CC0, no key).

Used for the full-season fixture list. Results in this dataset can lag behind other providers,
so a record without a score is only ever treated as "scheduled", never as proof a match is unplayed.

See docs/data-providers.md for fields, limits and licensing notes.
"""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from app.providers.base import NormalizedMatch, ProviderError, RateLimitError
from app.providers.http import tls_context

BASE_URL = "https://raw.githubusercontent.com/openfootball/football.json/master"
# competition code -> (file name, timezone the file's kick-off times are assumed to be in)
FILES = {
    "PL": ("en.1", "Europe/London"),
    "PD": ("es.1", "Europe/Madrid"),
    "SA": ("it.1", "Europe/Rome"),
    "BL1": ("de.1", "Europe/Berlin"),
    "FL1": ("fr.1", "Europe/Paris"),
}


def _full_time(score) -> tuple[int, int] | None:
    """The dataset writes scores as {"ft": [h, a]} or as a bare [h, a] list."""
    value = score.get("ft") if isinstance(score, dict) else score
    if isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) and v >= 0 for v in value):
        return value[0], value[1]
    return None


def parse_matches(payload: dict, competition_code: str, season_start_year: int) -> tuple[list[NormalizedMatch], list[str]]:
    file_name, tz_name = FILES[competition_code]
    tz = ZoneInfo(tz_name)
    matches: list[NormalizedMatch] = []
    warnings: list[str] = []
    for index, item in enumerate(payload.get("matches", [])):
        try:
            home, away = item["team1"], item["team2"]
            day = datetime.strptime(item["date"], "%Y-%m-%d")
            if not isinstance(home, str) or not isinstance(away, str) or not home or not away:
                raise ValueError("team name")
            if item.get("time"):
                hours, minutes = str(item["time"]).split(":")[:2]
                kickoff = day.replace(hour=int(hours), minute=int(minutes), tzinfo=tz).astimezone(UTC)
                precision = "datetime"
            else:
                kickoff, precision = day.replace(tzinfo=UTC), "date"
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"record {index}: missing or invalid field ({exc}); skipped")
            continue
        score = _full_time(item.get("score"))
        matchday = re.search(r"(\d+)", str(item.get("round", "")))
        notes = "Match id synthesised from file, season and team names (the dataset has no ids). "
        notes += (
            f"Kick-off converted from {tz_name} local time to UTC."
            if precision == "datetime"
            else "No kick-off time given; stored as midnight UTC with date precision."
        )
        matches.append(
            NormalizedMatch(
                provider_match_ref=f"{file_name}:{season_start_year}:{home}:{away}",
                competition_code=competition_code,
                season_start_year=season_start_year,
                home_team=home,
                away_team=away,
                kickoff_utc=kickoff,
                kickoff_precision=precision,
                status="finished" if score else "scheduled",
                home_goals=score[0] if score else None,
                away_goals=score[1] if score else None,
                matchday=int(matchday.group(1)) if matchday else None,
                transform_notes=notes,
                raw=item,
            )
        )
    return matches, warnings


class OpenFootballAdapter:
    name = "openfootball"

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
        self.warnings: list[str] = []

    def supports(self, competition_code: str) -> bool:
        return competition_code in FILES

    def fetch_matches(self, competition_code: str, season_start_year: int) -> list[NormalizedMatch]:
        if not self.supports(competition_code):
            raise ProviderError(f"{self.name} has no file for competition {competition_code}")
        season = f"{season_start_year}-{(season_start_year + 1) % 100:02d}"
        url = f"{BASE_URL}/{season}/{FILES[competition_code][0]}.json"
        try:
            response = httpx.get(url, timeout=self.timeout, follow_redirects=True, verify=tls_context())
        except httpx.HTTPError as exc:
            raise ProviderError(f"could not download {url}: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            raise RateLimitError(f"rate limited by provider for {url}")
        if response.status_code == 404:
            raise ProviderError(f"{self.name} has no data for season {season}")
        if response.status_code != 200:
            raise ProviderError(f"unexpected HTTP {response.status_code} for {url}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("openfootball returned invalid JSON") from exc
        matches, self.warnings = parse_matches(payload, competition_code, season_start_year)
        if not matches:
            raise ProviderError("season file contained no usable matches")
        return matches
