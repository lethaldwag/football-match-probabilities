# Adding a competition or a data provider

## Add a competition

No database change is needed: competitions and seasons are rows, not columns.

1. **Register it** in `backend/app/competitions.py` (code, name, country, `league` or `cup`).
   La Liga (`PD`), Serie A (`SA`), Bundesliga (`BL1`), Ligue 1 (`FL1`) and the Champions League
   (`CL`) are already registered.
2. **Check a provider supports the code.** Football-Data.co.uk covers the five leagues
   (`DIVISIONS` in `providers/football_data_uk.py`); the Champions League needs football-data.org.
3. **Add team short names if needed.** Providers name clubs differently ("Man United" vs
   "Manchester United FC"). `_SHORT_NAMES` in `backend/app/ingest.py` maps short names to full
   names; `FC`/`AFC` suffixes are handled automatically. After the first import, look at the
   `teams` table for duplicates and add any missing mappings.
4. **Import, predict, evaluate:**
   ```
   python -m app.cli import --competition SA --seasons 2022 2023 2024 2025 2026
   python -m app.cli predict --competition SA
   python -m app.cli evaluate --competition SA
   ```
5. **Schedule it:** `SCHEDULE_COMPETITIONS=PL,SA`.
6. **Front end:** the pages currently request `PL` (`COMPETITION` in `pages/Dashboard.tsx` and
   `pages/Performance.tsx`). Add a competition selector fed by `GET /api/v1/competitions`.

Things to know for cups (Champions League):

- A pair of teams can meet more than once at the same ground in one season, so matches are unique
  on season + home + away + **stage**. The football-data.org adapter fills `stage`.
- Models are fitted per competition. A cup alone has too few matches per team for the Poisson
  model, which will then report "unavailable" rather than guess. A cross-competition team-strength
  model is future work.

## Add a data provider

1. Create `backend/app/providers/<name>.py` with a class that has:
   - `name: str`
   - `supports(competition_code) -> bool`
   - `fetch_matches(competition_code, season_start_year) -> list[NormalizedMatch]`

   Convert everything to the normalised records in `providers/base.py`: UTC kick-off with
   `kickoff_precision`, internal status values, the provider's own record id, the raw record, and
   a `transform_notes` string describing what was changed. Raise `ProviderError` for failures and
   `RateLimitError` for rate limits. Collect row-level problems in `self.warnings` instead of
   dropping rows silently. Use `tls_context()` from `providers/http.py` for HTTPS.
2. Register it in `backend/app/providers/__init__.py` (`PROVIDER_NAMES` and `get_adapter`), and
   add any credentials to `app/config.py` and `.env.example`. Never log or return credentials.
3. Odds: fill `NormalizedOdds.captured_at` if the provider gives quote times, and set `timing`
   honestly. The import pipeline keeps price history (a new row only when a price changes).
4. Document the provider in `docs/data-providers.md`: coverage, limits, delays, gaps, attribution
   and storage/display rights.
5. Add tests like `backend/tests/test_providers.py` using a clearly labelled synthetic payload.

The import pipeline (`app/ingest.py`) needs no change: it handles get-or-create for competitions,
seasons and teams, idempotent upserts, provenance and import-run logging for any adapter.

## Add or change a model

- Implement `fit(known, cutoff)` and `predict(home_id, away_id)` like the classes in
  `app/modeling/models.py`, and add the class to `MODEL_CLASSES` in `app/modeling/predict.py`.
- `fit` receives only matches already filtered by `features.known_before()`. Do not query the
  database from inside a model; that is how leakage gets in.
- Changing a model's parameters creates a new `model_versions` row automatically, so old
  predictions stay traceable.
- Run `python -m app.cli evaluate` and compare with the base-rate reference and the bookmaker
  benchmark on the same walk-forward period before adopting it. Choose hyperparameters on seasons
  before the reported evaluation period.
