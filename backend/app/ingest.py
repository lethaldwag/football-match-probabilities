"""Import pipeline: normalised provider records -> database, idempotently."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.competitions import COMPETITIONS
from app.db import utcnow
from app.models import (
    Competition,
    ImportRun,
    Match,
    MatchProviderRef,
    MatchStatistics,
    OddsSnapshot,
    Season,
    Team,
    TeamAlias,
)
from app.providers import get_adapter
from app.providers.base import NormalizedMatch, ProviderAdapter, ProviderError

log = logging.getLogger(__name__)

# Short names used by some providers -> full club name. Extend when adding leagues/providers.
_SHORT_NAMES = {
    "Man United": "Manchester United",
    "Man City": "Manchester City",
    "Nott'm Forest": "Nottingham Forest",
    "Wolves": "Wolverhampton Wanderers",
    "Tottenham": "Tottenham Hotspur",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United",
    "Brighton": "Brighton and Hove Albion",
    "Leicester": "Leicester City",
    "Leeds": "Leeds United",
    "Ipswich": "Ipswich Town",
    "Luton": "Luton Town",
    "Norwich": "Norwich City",
    "West Brom": "West Bromwich Albion",
    "Coventry": "Coventry City",
    "Hull": "Hull City",
    "Stoke": "Stoke City",
    "Swansea": "Swansea City",
    "Cardiff": "Cardiff City",
    "Huddersfield": "Huddersfield Town",
    "Birmingham": "Birmingham City",
    "Derby": "Derby County",
    "QPR": "Queens Park Rangers",
    "Wigan": "Wigan Athletic",
    "Blackburn": "Blackburn Rovers",
    "Bolton": "Bolton Wanderers",
    "Charlton": "Charlton Athletic",
}


def _squash(name: str) -> str:
    text = re.sub(r"[^a-z0-9 ]", "", name.lower().replace("&", " and "))
    return "".join(t for t in text.split() if t not in {"fc", "afc", "cf"})


_SHORT_KEYS = {_squash(short): full for short, full in _SHORT_NAMES.items()}


def canonical_team(name: str) -> tuple[str, str]:
    """Return (match_key, display_name) so the same club is recognised across providers."""
    key = _squash(name)
    if key in _SHORT_KEYS:
        full = _SHORT_KEYS[key]
        return _squash(full), full
    display = re.sub(r"\s+", " ", re.sub(r"\b(AFC|FC)\b", "", name)).strip() or name
    return key, display


def season_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


@dataclass
class ImportCounts:
    seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    odds_added: int = 0
    warnings: list[str] = field(default_factory=list)


class _Resolver:
    """Get-or-create helpers with per-import caches."""

    def __init__(self, session: Session, provider: str):
        self.session, self.provider = session, provider
        self.competitions: dict[str, Competition] = {}
        self.seasons: dict[tuple[int, int], Season] = {}
        self.teams: dict[str, Team] = {}

    def competition(self, code: str) -> Competition:
        if code not in self.competitions:
            row = self.session.scalar(select(Competition).where(Competition.code == code))
            if row is None:
                if code not in COMPETITIONS:
                    raise ProviderError(f"unknown competition code {code!r}; add it to app/competitions.py")
                row = Competition(code=code, **COMPETITIONS[code])
                self.session.add(row)
                self.session.flush()
            self.competitions[code] = row
        return self.competitions[code]

    def season(self, competition: Competition, start_year: int) -> Season:
        key = (competition.id, start_year)
        if key not in self.seasons:
            row = self.session.scalar(
                select(Season).where(Season.competition_id == competition.id, Season.start_year == start_year)
            )
            if row is None:
                row = Season(competition_id=competition.id, start_year=start_year, label=season_label(start_year))
                self.session.add(row)
                self.session.flush()
            self.seasons[key] = row
        return self.seasons[key]

    def team(self, provider_name: str) -> Team:
        if provider_name not in self.teams:
            alias = self.session.scalar(
                select(TeamAlias).where(
                    TeamAlias.provider == self.provider, TeamAlias.provider_name == provider_name
                )
            )
            if alias is not None:
                team = self.session.get(Team, alias.team_id)
            else:
                key, display = canonical_team(provider_name)
                team = self.session.scalar(select(Team).where(Team.match_key == key))
                if team is None:
                    team = Team(name=display, match_key=key)
                    self.session.add(team)
                    self.session.flush()
                self.session.add(TeamAlias(team_id=team.id, provider=self.provider, provider_name=provider_name))
                self.session.flush()
            self.teams[provider_name] = team
        return self.teams[provider_name]


def _apply_match_fields(match: Match, rec: NormalizedMatch, warnings: list[str]) -> bool:
    """Update an existing match from a provider record. Returns True if anything changed."""
    label = f"{rec.home_team} v {rec.away_team}"
    changed = False

    # Never replace a precise kick-off time with a date-only one. A provider that has not caught up
    # with a result (still reports the match as unplayed) may also hold an outdated kick-off time,
    # so it must not move a match that is already finished.
    lagging = match.status == "finished" and rec.status != "finished"
    if not lagging and not (rec.kickoff_precision == "date" and match.kickoff_precision == "datetime"):
        if match.kickoff_utc != rec.kickoff_utc or match.kickoff_precision != rec.kickoff_precision:
            match.kickoff_utc, match.kickoff_precision = rec.kickoff_utc, rec.kickoff_precision
            changed = True

    if match.status == "finished" and rec.status != "finished":
        warnings.append(f"{label}: provider reports '{rec.status}' but a final score is stored; kept as finished")
    else:
        if match.status == "finished" and (match.home_goals, match.away_goals) != (rec.home_goals, rec.away_goals):
            warnings.append(
                f"{label}: final score changed from {match.home_goals}-{match.away_goals} "
                f"to {rec.home_goals}-{rec.away_goals}"
            )
        new = (rec.status, rec.home_goals, rec.away_goals)
        if (match.status, match.home_goals, match.away_goals) != new:
            match.status, match.home_goals, match.away_goals = new
            changed = True

    if rec.matchday is not None and match.matchday != rec.matchday:
        match.matchday = rec.matchday
        changed = True
    if changed:
        match.updated_at = utcnow()
    return changed


def import_records(
    session: Session, provider: str, records: list[NormalizedMatch], fetched_at: datetime | None = None
) -> ImportCounts:
    """Upsert normalised matches. Importing the same records twice changes nothing the second time."""
    fetched_at = fetched_at or utcnow()
    counts = ImportCounts(seen=len(records))
    resolver = _Resolver(session, provider)

    refs = {
        r.provider_match_ref: r
        for r in session.scalars(select(MatchProviderRef).where(MatchProviderRef.provider == provider))
    }
    matches_by_season: dict[int, dict[tuple[int, int, str], Match]] = {}
    seen_refs: set[str] = set()

    for rec in records:
        if rec.provider_match_ref in seen_refs:
            counts.warnings.append(f"duplicate record {rec.provider_match_ref} in provider data; ignored")
            continue
        seen_refs.add(rec.provider_match_ref)
        if rec.home_team == rec.away_team:
            counts.warnings.append(f"{rec.provider_match_ref}: home and away team are the same; skipped")
            continue

        competition = resolver.competition(rec.competition_code)
        season = resolver.season(competition, rec.season_start_year)
        home, away = resolver.team(rec.home_team), resolver.team(rec.away_team)
        if season.id not in matches_by_season:
            matches_by_season[season.id] = {
                (m.home_team_id, m.away_team_id, m.stage): m
                for m in session.scalars(select(Match).where(Match.season_id == season.id))
            }
        season_matches = matches_by_season[season.id]

        key = (home.id, away.id, rec.stage)
        match = season_matches.get(key)
        if match is None:
            match = Match(
                competition_id=competition.id,
                season_id=season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                stage=rec.stage,
                matchday=rec.matchday,
                kickoff_utc=rec.kickoff_utc,
                kickoff_precision=rec.kickoff_precision,
                status=rec.status,
                home_goals=rec.home_goals,
                away_goals=rec.away_goals,
            )
            session.add(match)
            session.flush()
            season_matches[key] = match
            counts.created += 1
        elif _apply_match_fields(match, rec, counts.warnings):
            counts.updated += 1
        else:
            counts.unchanged += 1

        ref = refs.get(rec.provider_match_ref)
        if ref is None:
            ref = MatchProviderRef(provider=provider, provider_match_ref=rec.provider_match_ref, match_id=match.id)
            session.add(ref)
            refs[rec.provider_match_ref] = ref
        ref.match_id = match.id
        ref.fetched_at = fetched_at
        ref.source_updated_at = rec.source_updated_at
        ref.transform_notes = rec.transform_notes
        ref.raw = rec.raw

        _upsert_stats(session, provider, match, rec, home.id, away.id, fetched_at)
        counts.odds_added += _add_changed_odds(session, provider, match, rec, fetched_at)

    session.flush()
    return counts


def _upsert_stats(session, provider, match, rec, home_id, away_id, fetched_at) -> None:
    incoming = [(home_id, rec.home_stats), (away_id, rec.away_stats)]
    if all(stats is None for _, stats in incoming):
        return
    existing = {
        s.team_id: s
        for s in session.scalars(
            select(MatchStatistics).where(
                MatchStatistics.match_id == match.id, MatchStatistics.provider == provider
            )
        )
    }
    for team_id, stats in incoming:
        if stats is None:
            continue
        row = existing.get(team_id)
        if row is None:
            row = MatchStatistics(match_id=match.id, team_id=team_id, provider=provider, fetched_at=fetched_at)
            session.add(row)
        values = vars(stats)
        if any(getattr(row, k) != v for k, v in values.items()):
            for k, v in values.items():
                setattr(row, k, v)
            row.fetched_at = fetched_at


def _add_changed_odds(session, provider, match, rec, fetched_at) -> int:
    if not rec.odds:
        return 0
    latest: dict[tuple, float] = {}
    rows = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.match_id == match.id, OddsSnapshot.provider == provider)
        .order_by(OddsSnapshot.fetched_at)
    )
    for row in rows:
        latest[(row.bookmaker, row.market, row.selection, row.timing)] = row.decimal_odds
    added = 0
    for odd in rec.odds:
        key = (odd.bookmaker, odd.market, odd.selection, odd.timing)
        if latest.get(key) == odd.decimal_odds:
            continue
        latest[key] = odd.decimal_odds
        session.add(
            OddsSnapshot(
                match_id=match.id,
                provider=provider,
                bookmaker=odd.bookmaker,
                market=odd.market,
                selection=odd.selection,
                decimal_odds=odd.decimal_odds,
                timing=odd.timing,
                captured_at=odd.captured_at,
                fetched_at=fetched_at,
            )
        )
        added += 1
    return added


def run_import(
    session: Session,
    provider: str,
    competition_code: str,
    season_start_year: int,
    adapter: ProviderAdapter | None = None,
) -> ImportRun:
    """Fetch one season from a provider and import it, recording the outcome in import_runs."""
    run = ImportRun(provider=provider, competition_code=competition_code, season_start_year=season_start_year)
    session.add(run)
    session.commit()
    try:
        adapter = adapter or get_adapter(provider)
        records = adapter.fetch_matches(competition_code, season_start_year)
        counts = import_records(session, provider, records)
        run.status = "success"
        run.records_seen = counts.seen
        run.matches_created = counts.created
        run.matches_updated = counts.updated
        run.matches_unchanged = counts.unchanged
        warnings = list(getattr(adapter, "warnings", [])) + counts.warnings
        run.warnings = warnings[:200] or None
        log.info(
            "import finished",
            extra={
                "provider": provider, "competition": competition_code, "season": season_start_year,
                "matches_created": counts.created, "matches_updated": counts.updated,
                "matches_unchanged": counts.unchanged,
                "odds_added": counts.odds_added, "warnings": len(warnings),
            },
        )
    except ProviderError as exc:
        session.rollback()
        run.status, run.error_message = "failed", str(exc)
        log.warning("import failed", extra={"provider": provider, "competition": competition_code,
                                            "season": season_start_year, "error": str(exc)})
    except Exception:
        session.rollback()
        run.status, run.error_message = "failed", "internal error during import (see server logs)"
        log.exception("import crashed", extra={"provider": provider, "competition": competition_code,
                                               "season": season_start_year})
    run.finished_at = utcnow()
    session.add(run)
    session.commit()
    return run
