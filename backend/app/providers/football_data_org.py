"""Adapter for the football-data.org v4 REST API (fixtures and results; requires an API key).

See docs/data-providers.md for fields, limits and licensing notes.
"""

from datetime import UTC, datetime

import httpx

from app.providers.base import NormalizedMatch, ProviderError, RateLimitError
from app.providers.http import tls_context

BASE_URL = "https://api.football-data.org/v4"
# Our competition codes follow football-data.org's codes.
COMPETITIONS = {"PL", "PD", "SA", "BL1", "FL1", "CL"}

_STATUS = {
    "SCHEDULED": "scheduled",
    "TIMED": "scheduled",
    "IN_PLAY": "in_play",
    "PAUSED": "in_play",
    "FINISHED": "finished",
    "AWARDED": "finished",
    "POSTPONED": "postponed",
    "SUSPENDED": "postponed",
    "CANCELLED": "cancelled",
}


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def parse_matches(payload: dict, competition_code: str, season_start_year: int) -> tuple[list[NormalizedMatch], list[str]]:
    matches: list[NormalizedMatch] = []
    warnings: list[str] = []
    for item in payload.get("matches", []):
        try:
            home, away = item["homeTeam"]["name"], item["awayTeam"]["name"]
            kickoff = _parse_time(item["utcDate"])
            status = _STATUS.get(item.get("status", ""))
            if not home or not away or kickoff is None or status is None:
                raise KeyError("team, utcDate or status")
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"match {item.get('id')}: missing or invalid field ({exc}); skipped")
            continue
        full_time = (item.get("score") or {}).get("fullTime") or {}
        home_goals, away_goals = full_time.get("home"), full_time.get("away")
        if status == "finished" and (home_goals is None or away_goals is None):
            warnings.append(f"match {item.get('id')}: finished without a score; kept as scheduled")
            status = "scheduled"
        stage = (item.get("stage") or "REGULAR_SEASON").lower()
        matches.append(
            NormalizedMatch(
                provider_match_ref=str(item["id"]),
                competition_code=competition_code,
                season_start_year=season_start_year,
                home_team=home,
                away_team=away,
                kickoff_utc=kickoff,
                kickoff_precision="datetime",
                status=status,
                home_goals=home_goals if status == "finished" else None,
                away_goals=away_goals if status == "finished" else None,
                stage="regular" if stage == "regular_season" else stage,
                matchday=item.get("matchday"),
                source_updated_at=_parse_time(item.get("lastUpdated")),
                transform_notes="Status mapped to internal values; full-time score used.",
                raw=item,
            )
        )
    return matches, warnings


class FootballDataOrgAdapter:
    name = "football-data-org"

    def __init__(self, api_key: str, timeout: float = 30.0):
        self.api_key = api_key
        self.timeout = timeout
        self.warnings: list[str] = []

    def supports(self, competition_code: str) -> bool:
        return competition_code in COMPETITIONS

    def fetch_matches(self, competition_code: str, season_start_year: int) -> list[NormalizedMatch]:
        if not self.api_key:
            raise ProviderError("FOOTBALL_DATA_ORG_API_KEY is not configured")
        if not self.supports(competition_code):
            raise ProviderError(f"{self.name} does not cover competition {competition_code}")
        url = f"{BASE_URL}/competitions/{competition_code}/matches"
        try:
            response = httpx.get(
                url,
                params={"season": season_start_year},
                headers={"X-Auth-Token": self.api_key},
                timeout=self.timeout,
                verify=tls_context(),
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {exc.__class__.__name__}") from exc
        if response.status_code == 429:
            raise RateLimitError("football-data.org rate limit reached; try again later")
        if response.status_code in (401, 403):
            raise ProviderError("football-data.org rejected the API key or the plan lacks access")
        if response.status_code != 200:
            raise ProviderError(f"unexpected HTTP {response.status_code} from football-data.org")
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("football-data.org returned invalid JSON") from exc
        matches, self.warnings = parse_matches(payload, competition_code, season_start_year)
        return matches
