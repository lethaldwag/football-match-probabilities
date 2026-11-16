"""Provider adapter interface and the normalised records every adapter must produce."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


class ProviderError(Exception):
    """The provider could not supply data (network error, bad response, missing credentials...)."""


class RateLimitError(ProviderError):
    """The provider refused the request because of its rate limit. Retry later; never bypass."""


@dataclass
class NormalizedOdds:
    bookmaker: str
    market: str  # 1X2 | OU25 | BTTS
    selection: str
    decimal_odds: float
    timing: str  # pre_match | closing
    captured_at: datetime | None = None  # only when the provider states it


@dataclass
class NormalizedTeamStats:
    shots: int | None = None
    shots_on_target: int | None = None
    corners: int | None = None
    fouls: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None

    def is_empty(self) -> bool:
        return all(v is None for v in vars(self).values())


@dataclass
class NormalizedMatch:
    provider_match_ref: str
    competition_code: str
    season_start_year: int
    home_team: str
    away_team: str
    kickoff_utc: datetime
    kickoff_precision: str  # datetime | date
    status: str
    home_goals: int | None = None
    away_goals: int | None = None
    stage: str = "regular"
    matchday: int | None = None
    source_updated_at: datetime | None = None
    transform_notes: str | None = None
    raw: dict | None = None
    home_stats: NormalizedTeamStats | None = None
    away_stats: NormalizedTeamStats | None = None
    odds: list[NormalizedOdds] = field(default_factory=list)


class ProviderAdapter(Protocol):
    name: str

    def supports(self, competition_code: str) -> bool: ...

    def fetch_matches(self, competition_code: str, season_start_year: int) -> list[NormalizedMatch]:
        """Return all matches the provider has for one season, normalised. Raises ProviderError."""
        ...
