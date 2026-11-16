# Data providers and licensing

Three adapters ship with the project. All use official files or APIs; nothing is scraped and
provider rate limits are never bypassed (a rate-limit response fails the import run, which is
retried on the next scheduled cycle).

> **Licensing status: assumptions, not legal clearance.** The notes below record what the
> providers state publicly as far as the author knows. **Before any public or commercial
> deployment, read each provider's current terms and get permission where needed.** In particular,
> displaying bookmaker odds publicly may carry obligations beyond the data licence (gambling
> advertising rules differ by country).

## Football-Data.co.uk (`football-data-uk`) — default

| | |
|---|---|
| Access | Public CSV downloads; no key |
| Files used | `mmz4281/<season>/<division>.csv` (one season), `fixtures.csv` (next few days) |
| Competitions | Top divisions of England, Spain, Italy, Germany, France (and more). **No Champions League.** |
| Seasons | 1990s onward; column sets change over time |
| Fields used | Date, kick-off time (recent seasons), teams, full-time score, shots, shots on target, corners, fouls, cards, 1X2 and over/under 2.5 odds (pre-match and closing) |
| Not available | Both-teams-to-score odds, line-ups, injuries, match ids, odds quote times |
| Update delay | Results typically appear within a day or two of the match; fixtures only a few days ahead |
| Request limits | None published. The scheduler downloads two small files per cycle (default every 6 hours) |
| Verified here | Yes: eight EPL seasons imported on 16 Nov 2026 |

Known gaps and how they are handled:

- **Fixtures only a few days ahead**, and none during international breaks. The full fixture
  list therefore comes from openfootball (below); this file adds odds for upcoming matches once
  they are listed.
- **No odds timestamps.** Each price is stored with `timing` (`pre_match` or `closing`, as labelled
  by the file's columns), a null `captured_at`, and the time we downloaded it. The provider's notes
  describe pre-match odds as collected ahead of the match (Friday afternoons for weekend games,
  Tuesday afternoons for midweek games); this is the provider's statement, not something the
  application can verify. Only `pre_match` prices are used by the benchmark and betting simulation.
- **Kick-off times** are treated as UK local time and converted to UTC (assumption based on the
  provider's notes). Older files have no time; those matches are stored with date precision.
- **No match ids.** A reference is synthesised from division, season and team names.
- **Bookmaker codes** (`B365`, `PS`, `Avg`, `Max`, ...) are stored exactly as in the file. Which
  ones are kept is set by `FDUK_BOOKMAKERS`.

Licensing: the site offers the files as free downloads. The author is not aware of a formal
licence document covering redistribution or commercial use, so **assume personal/research use
only until you have confirmed otherwise with the provider**, and credit Football-Data.co.uk (the
web app footer does). Raw rows are stored in `match_provider_refs.raw` for debugging; remove that
if storage of raw data turns out not to be permitted.

## openfootball (`openfootball`) — default, full fixture list

| | |
|---|---|
| Access | Public JSON files on GitHub (`openfootball/football.json`); no key |
| File used | `<season>/<league>.json`, e.g. `2026-27/en.1.json` |
| Competitions | Top divisions of England, Spain, Italy, Germany, France are mapped in the adapter |
| Fields used | Date, kick-off time, round (matchday), teams, full-time score when present |
| Not available | Odds, statistics, match ids, update timestamps |
| Update delay | Volunteer-maintained. On 16 Nov 2026 it held results for 50 of the 100 matches played, so it is used for **fixtures**, not as the source of results |
| Request limits | One small file per import; GitHub's normal raw-file limits apply |
| Verified here | Yes for the Premier League: 380 fixtures imported on 16 Nov 2026, merged with Football-Data.co.uk without duplicate teams or matches |

Licence: the repository's `LICENSE.md` is CC0 1.0 (public domain dedication), read on
16 Nov 2026. No attribution is required; the web app credits it anyway.

Known gaps and how they are handled:

- **Results lag.** A record without a score only ever means "scheduled as far as this file
  knows". It can never reopen a match that another provider has reported as finished, nor move
  its kick-off time (`_apply_match_fields` in `app/ingest.py`).
- **Rearranged matches** may keep an outdated date until the maintainers update the file. If a
  listed kick-off passes without a result arriving, the match is flagged in the UI.
- **Kick-off times** are assumed to be local to the league's country and converted to UTC. This
  was checked against Football-Data.co.uk for the Premier League only; the other four leagues'
  time zones are an untested assumption.

## football-data.org (`football-data-org`) — optional, needs a key

| | |
|---|---|
| Access | REST API v4, `X-Auth-Token` header. Set `FOOTBALL_DATA_ORG_API_KEY` |
| Endpoint used | `GET /v4/competitions/{code}/matches?season={year}` |
| Competitions | Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Champions League (codes `PL`, `PD`, `SA`, `BL1`, `FL1`, `CL`) on the free tier, to the author's knowledge |
| Fields used | Match id, UTC kick-off, status, matchday, stage, teams, full-time score, last-updated time |
| Not available on the free tier | Odds, detailed statistics, line-ups (paid plans) |
| Request limits | Free tier is limited to a small number of requests per minute (10 at the time of writing, to the author's knowledge). The adapter makes one request per import |
| Verified here | **No.** No API key was available. The parser is tested against a hand-written payload in the documented v4 format |

Licensing: the free tier asks for attribution and has its own terms for commercial use; check
them before deploying. To enable: set the key and add the provider to the schedule:

```
FOOTBALL_DATA_ORG_API_KEY=...
SCHEDULE_PROVIDERS=football-data-uk,football-data-org
```

Both providers can run together. Matches are matched on season, home team, away team and stage,
so the same fixture from two providers becomes one row with two provenance records.

## API-Football and other paid providers — not implemented

Needed for line-ups, injuries, richer statistics and time-stamped odds. Evaluate fields, limits,
storage/display rights and price before writing an adapter.

## Provenance stored for every imported match

`match_provider_refs`: provider name, provider's record id, time fetched, provider's own update
time (when given), a note on the normalisation applied, and the raw record. Every import attempt
is recorded in `import_runs` with counts, warnings and any error message.
