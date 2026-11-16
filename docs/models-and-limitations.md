# Models, evaluation and known limitations

## Models

**Poisson goals model (`poisson-goals`, primary).** Goals for each side are modelled as Poisson:

    log(expected goals) = intercept + home advantage + attack(team) + defence(opponent)

Fitted with scikit-learn's `PoissonRegressor` on past results of the same competition from the
last four years. Recent matches count more (weight halves every 365 days) and team strengths are
shrunk towards the league average (L2 penalty). The two goal counts are treated as independent;
the score grid (0 to 10 goals each) gives the probabilities for 1X2, over/under 2.5 and both
teams to score. Each market's probabilities are checked to be within [0, 1] and to sum to 1
before they are stored.

**Base-rate reference (`base-rate`).** The smoothed historical frequency of each outcome, the
same for every match. It exists so the primary model has something to beat.

Low-data behaviour: with fewer than 150 past matches (50 for the base rate) the model stores an
"unavailable" prediction with the reason, instead of a number. A team with no matches in the
window (for example newly promoted) gets average strength, and the match page says so.

## Leakage prevention

- Every model input passes through `features.known_before(frame, cutoff)`. A result counts as
  known 3 hours after kick-off, or at the end of the day when only the date is known.
- Live predictions use cutoff = generation time and are frozen at kick-off.
- Backtest predictions use cutoff = 00:00 UTC on the match day, so same-day results are never used.
- Post-match statistics and bookmaker odds are not model inputs.
- Tests: rewriting every result after a cutoff leaves predictions bit-for-bit identical
  (`test_future_results_cannot_change_a_prediction`).

## Evaluation

Walk-forward over the most recent `EVALUATION_SEASONS` seasons (default 3): for each match day the
models are refitted on what was known before that day, then scored on that day's matches. There
is no random splitting. Reported per market and per model: sample size, log loss, multi-class
Brier score, accuracy, calibration by probability decile, and a per-season breakdown.

A third row, **bookmaker-implied**, is the probability implied by pre-match market-average odds
with the margin removed. It is a benchmark, not one of this application's models.

Results on real EPL data, run 16 Nov 2026 (860 matches, Aug 2024 to Nov 2026). Lower is better
for log loss and Brier:

| Market | Model | Log loss | Brier | Accuracy |
|---|---|---|---|---|
| Match result | Poisson goals | 1.0089 | 0.6039 | 50.1% |
| | Base rate | 1.0827 | 0.6558 | 41.7% |
| | Bookmaker-implied | 0.9951 | 0.5957 | 51.4% |
| Over/under 2.5 | Poisson goals | 0.6885 | 0.4949 | 58.6% |
| | Base rate | 0.6905 | 0.4973 | 54.8% |
| | Bookmaker-implied | 0.6842 | 0.4911 | 55.6% |
| Both teams to score | Poisson goals | 0.6888 | 0.4955 | 55.2% |
| | Base rate | 0.6881 | 0.4950 | 55.8% |

What this says, plainly:

- For match result the model is clearly better than the base rate and **worse than the bookmakers**.
- For over/under 2.5 it is only marginally better than the base rate.
- For both teams to score it is **no better than the base rate**. Treat those numbers as near
  uninformative.
- **The model has no demonstrated betting edge.** The flat-stake simulation (bet when model
  probability x odds - 1 >= 0.05, market-average pre-match odds) placed 582 bets and lost 93.3
  units (-16.0% return, largest drawdown 99.2 units).

Hyperparameters (half-life, shrinkage) were sanity-checked on the 2022-23 and 2023-24 seasons,
which are outside the reported period, and were not tuned further. The betting threshold was
fixed in advance.

## Known limitations

Model
- Uses only past scores. No injuries, line-ups, transfers, managers, rest days or expected goals.
- Independent Poisson understates draws and low-scoring correlations (no Dixon-Coles correction).
- Promoted teams are assumed average, which is usually too generous.
- No probability calibration step is fitted (calibration is measured, not corrected).
- One competition at a time; no cross-league strength.

Data
- The fixture list comes from openfootball, a volunteer-maintained public dataset. Rearranged
  kick-off times can be out of date until its maintainers update it; a football-data.org key
  gives an official alternative. Odds for upcoming matches only appear a few days before
  kick-off (from Football-Data.co.uk's fixtures file).
- Odds have no quote timestamps and there are no both-teams-to-score odds.
- The betting simulation uses market-average odds, which is not a price anyone could actually
  have taken on every bet; it ignores stake limits and account restrictions.
- Postponed or rearranged matches are only as correct as the provider's files.
- Team-name matching across providers relies on a hand-maintained list of short names.

Operations
- The rate limiter is in-memory and per process; behind several workers or a proxy use a shared
  limiter (and pass the real client IP).
- The scheduler is a simple loop in its own container. Run only one instance.
- No user accounts and no personal data are stored. The API logs request path, status and
  duration, not IP addresses.
- Database backups are not configured. For PostgreSQL use `pg_dump` on a schedule or your host's
  managed backups.
- Monitoring hooks: `/health` (returns 503 when the database is unreachable), JSON logs with
  `import finished` / `import failed` / `prediction generation failed` events, and the
  `import_runs` table. No metrics endpoint or alerting is included.
- Docker Compose and PostgreSQL have not been run by the author (see README, "Verification").
