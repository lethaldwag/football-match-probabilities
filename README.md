# Football statistics and match probability platform

An analysis tool that imports football results and estimates pre-match probabilities for three
markets: match result (1X2), over/under 2.5 goals, and both teams to score. First release covers
the English Premier League; nothing in the database or model is specific to it.

**Probabilities are uncertain statistical estimates. They are not betting or financial advice.**
There is no bet placement, no payments and no bookmaker account integration.

## What is in the box

| Part | Technology | Folder |
|---|---|---|
| API | Python, FastAPI, SQLAlchemy, Alembic, pandas, scikit-learn | `backend/` |
| Web app | React, TypeScript, Vite, Tailwind CSS, Recharts | `frontend/` |
| Database | PostgreSQL (SQLite also works for local development) | |
| Local stack | Docker Compose: database, API, scheduled job, web | `docker-compose.yml` |

More detail: [data providers and licensing](docs/data-providers.md),
[adding a competition or provider](docs/extending.md),
[models, evaluation and known limitations](docs/models-and-limitations.md).

## Quick start without Docker (SQLite)

Needs Python 3.11+ and Node 20+. Commands are for Windows PowerShell; on macOS/Linux use
`.venv/bin/python` instead of `.venv\Scripts\python.exe`.

```powershell
# 1. Backend
cd backend
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m alembic upgrade head            # creates dev.db

# 2. Load real EPL history (downloads CSV files from Football-Data.co.uk)
.venv\Scripts\python.exe -m app.cli import --competition PL --seasons 2019 2020 2021 2022 2023 2024 2025 2026
#    ...and the full fixture list for the current season (openfootball, public domain)
.venv\Scripts\python.exe -m app.cli import --provider openfootball --competition PL --seasons 2026
.venv\Scripts\python.exe -m app.cli predict --competition PL    # predictions for upcoming fixtures
.venv\Scripts\python.exe -m app.cli evaluate --competition PL   # walk-forward backtest (~30 s)

# 3. Run the API  ->  http://localhost:8000/docs
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# 4. In a second terminal: run the web app  ->  http://localhost:5173
cd frontend
npm install
npm run dev
```

With no `DATABASE_URL` set the backend uses `backend/dev.db` (SQLite).

## Quick start with Docker Compose (PostgreSQL)

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD (and ADMIN_API_TOKEN if wanted)
docker compose up --build     # web: http://localhost:8080   API docs: http://localhost:8000/docs
docker compose exec api python -m app.cli import --competition PL --seasons 2019 2020 2021 2022 2023 2024 2025 2026
docker compose exec api python -m app.cli import --provider openfootball --competition PL --seasons 2026
docker compose exec api python -m app.cli predict --competition PL
docker compose exec api python -m app.cli evaluate --competition PL
```

The `api` container applies database migrations on start. The `scheduler` container then
re-imports the current season, regenerates predictions and (when results changed) re-runs the
evaluation every `SCHEDULE_INTERVAL_MINUTES`.

> **Not verified:** Docker and PostgreSQL were not installed on the machine this was built on, so
> the Compose stack and the PostgreSQL code path have not been run. Everything was verified on
> SQLite. See "Verification" below.

## Hosting on GitHub Pages (static, no server)

GitHub Pages serves files only; it cannot run the API or a database. The workflow in
[.github/workflows/deploy.yml](.github/workflows/deploy.yml) therefore does the scheduler's job
inside GitHub Actions: on every push to `main` and every six hours it refreshes the data,
regenerates predictions, writes the API responses to JSON files
(`python -m app.cli export --out ../frontend/public/data`) and builds the web app with
`VITE_STATIC_DATA=true` so that it reads those files instead of calling `/api`.

What differs from the full stack:

- The site is a snapshot from the last workflow run. The status panel shows when that was.
  "Stale" flags are also worked out at that moment, not when the page is opened.
- There is no live API, no `/docs` page and no admin API.
- The SQLite database is kept between runs in the Actions cache. If that cache is lost, history is
  re-imported and earlier pre-match predictions for played matches are replaced by backtest ones.
- GitHub pauses scheduled workflows after 60 days without repository activity; re-enable it on
  the Actions tab if that happens.

To try the static build locally: run the export command above from `backend/`, then in
`frontend/` run `npm run build` with `VITE_STATIC_DATA=true` set and `npm run preview`.

## Configuration

All settings are environment variables; see [.env.example](.env.example) for the full list.
The backend also reads a `.env` file in `backend/` or the project root.

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | e.g. `postgresql+psycopg://user:pass@host:5432/db` or `sqlite:///./dev.db` |
| `ADMIN_API_TOKEN` | Enables `/api/v1/admin/*`; empty disables it |
| `FOOTBALL_DATA_ORG_API_KEY` | Optional key for the football-data.org adapter |
| `SCHEDULE_*` | Interval, competitions and providers for the scheduled job |
| `PREDICTION_STALE_HOURS`, `DATA_STALE_HOURS` | When predictions / data are flagged as stale |
| `RATE_LIMIT_PER_MINUTE` | Per-IP limit on `/api/*` (0 disables) |

## Everyday commands

```powershell
# from backend/
.venv\Scripts\python.exe -m app.cli refresh       # one scheduler cycle: import + predict (+ evaluate)
.venv\Scripts\python.exe -m app.cli scheduler     # run that cycle forever
.venv\Scripts\python.exe -m alembic upgrade head  # apply migrations
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"   # new migration
.venv\Scripts\python.exe -m pytest                # tests
.venv\Scripts\python.exe -m ruff check .          # lint

# from frontend/
npm test          # component tests
npm run build     # type-check and production build
```

Trigger an import over HTTP (needs `ADMIN_API_TOKEN`):

```bash
curl -X POST http://localhost:8000/api/v1/admin/imports \
  -H "X-Admin-Token: <your token>" -H "Content-Type: application/json" \
  -d '{"provider": "football-data-uk", "competition": "PL", "season": 2026}'
```

## API

Interactive documentation is generated at `/docs` (OpenAPI at `/openapi.json`).

| Route | Purpose |
|---|---|
| `GET /health` | Liveness and database check |
| `GET /api/v1/status` | Last import, last prediction run, data freshness |
| `GET /api/v1/competitions` | Competitions and seasons with data |
| `GET /api/v1/matches` | Filter by `competition`, `season`, `status`, `date_from`, `date_to`; paginated |
| `GET /api/v1/matches/{id}` | Predictions, odds, statistics, form, provenance |
| `GET /api/v1/matches/{id}/predictions` | Every stored prediction, optional `market` |
| `GET /api/v1/teams/{id}/form` | Recent results, optional `before` |
| `GET /api/v1/evaluation/latest` | Latest walk-forward evaluation |
| `GET/POST /api/v1/admin/imports` | Inspect / trigger imports (token required) |

Errors always have the shape `{"error": {"code", "message", "details"}}`.

## How honesty about data is enforced

- A prediction is one of: **ok**, **stale** (older than `PREDICTION_STALE_HOURS`), **unavailable**
  (model had too little data; the reason is stored) or **missing** (none generated). The UI shows
  each state in words.
- Every prediction stores its model version, generation time and data cutoff. Predictions for a
  match are never regenerated after kick-off.
- Predictions for completed matches come from the backtest and are labelled "Backtest".
- Odds keep the provider, bookmaker, pre-match/closing label and download time. The provider does
  not publish quote times, and the UI says so. No odds exist for both-teams-to-score, and the UI
  says that too.
- If no upcoming fixtures have been imported, the dashboard says so instead of showing anything.
- Results come from Football-Data.co.uk and the fixture list from openfootball. When the fixture
  source lags behind (still lists a played match as upcoming), the stored result always wins.

## Verification

What was actually run on the development machine (Windows, Python 3.14, Node 25, SQLite):

- `pytest`: 85 backend tests passed. They cover provider normalisation, idempotent imports,
  database constraints, migration/schema agreement, probability validity, cutoff and leakage
  behaviour, chronological evaluation, and API validation and errors.
- `vitest`: 13 front-end tests passed (loading, empty, error and populated states for all pages).
- `ruff check`, TypeScript type-check and `vite build` passed.
- A real import of eight EPL seasons from Football-Data.co.uk (2,760 matches) and the 2026-27
  fixture list from openfootball (280 upcoming matches), repeat imports from both (0 created,
  0 updated), prediction generation for the upcoming matches, the evaluation, and the three
  pages in a browser.

Not run: Docker Compose, PostgreSQL, and the football-data.org adapter against the live API (no
API key was available; it is tested against a hand-written payload in the documented format).

All test data is synthetic and labelled as such (`backend/tests/`). No sample or mock data is
loaded into the application database.

## Version control note

This project has its own Git repository in this folder. On the machine it was built on there is
also an unrelated, empty repository rooted at the user home folder; it is not used by this project.
