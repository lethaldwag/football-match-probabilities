"""Application configuration, read from environment variables (and an optional .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # SQLite is supported for quick local development; PostgreSQL is the intended production database.
    database_url: str = "sqlite:///./dev.db"

    # Admin endpoints are disabled unless a token is configured.
    admin_api_token: str = ""
    # Optional: football-data.org API key (free tier available). Never sent to the browser.
    football_data_org_api_key: str = ""

    cors_origins: str = "http://localhost:5173"
    rate_limit_per_minute: int = 120  # per client IP on public API routes; 0 disables

    # A live prediction older than this is reported as "stale".
    prediction_stale_hours: int = 48
    # Imported data older than this is reported as "stale".
    data_stale_hours: int = 48

    # Scheduled job (python -m app.cli scheduler)
    schedule_interval_minutes: int = 360
    schedule_competitions: str = "PL"
    schedule_providers: str = "football-data-uk,openfootball"
    schedule_run_evaluation: bool = True

    # Odds columns to keep from football-data.co.uk files (bookmaker codes as used in the files).
    fduk_bookmakers: str = "B365,PS,Avg,Max,BbAv,BbMx"

    # Number of most recent seasons evaluated by the walk-forward backtest.
    evaluation_seasons: int = 3

    log_level: str = "INFO"
    log_json: bool = True

    def csv(self, value: str) -> list[str]:
        return [v.strip() for v in value.split(",") if v.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
