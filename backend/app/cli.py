"""Command line: imports, predictions, evaluation and the scheduled job.

    python -m app.cli import --competition PL --seasons 2022 2023 2024 2025 2026
    python -m app.cli predict --competition PL
    python -m app.cli evaluate --competition PL
    python -m app.cli refresh          # one scheduler cycle
    python -m app.cli scheduler        # run refresh forever at the configured interval
    python -m app.cli export --out ../frontend/public/data   # JSON files for static hosting
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from app.config import get_settings
from app.db import get_sessionmaker, utcnow
from app.ingest import run_import
from app.logging_config import configure_logging
from app.modeling.evaluate import run_evaluation
from app.modeling.predict import generate_live_predictions
from app.providers import PROVIDER_NAMES
from app.providers.football_data_uk import season_of
from app.static_export import export_static

log = logging.getLogger("app.cli")


def refresh() -> bool:
    """One scheduled cycle: import the current season, regenerate predictions, re-evaluate if needed.

    Returns False if any import failed. Failures are logged and never stop the other steps.
    """
    settings = get_settings()
    season = season_of(utcnow())
    ok = True
    with get_sessionmaker()() as session:
        for competition in settings.csv(settings.schedule_competitions):
            changed = False
            for provider in settings.csv(settings.schedule_providers):
                run = run_import(session, provider, competition, season)
                ok &= run.status == "success"
                changed |= bool(run.matches_created or run.matches_updated)
            try:
                generate_live_predictions(session, competition)
                if changed and settings.schedule_run_evaluation:
                    run_evaluation(session, competition, settings.evaluation_seasons)
            except Exception:
                session.rollback()
                ok = False
                log.exception("prediction/evaluation step failed", extra={"competition": competition})
    return ok


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="import seasons from a provider")
    imp.add_argument("--provider", default="football-data-uk", choices=PROVIDER_NAMES)
    imp.add_argument("--competition", default="PL")
    imp.add_argument("--seasons", type=int, nargs="+", required=True, help="season start years")

    for name, text in (("predict", "generate predictions for upcoming matches"),
                       ("evaluate", "run the walk-forward evaluation")):
        cmd = sub.add_parser(name, help=text)
        cmd.add_argument("--competition", default="PL")

    exp = sub.add_parser("export", help="write the public API responses to JSON files for static hosting")
    exp.add_argument("--competition", default="PL")
    exp.add_argument("--out", type=Path, required=True, help="output directory")

    sub.add_parser("refresh", help="run one scheduled cycle")
    sub.add_parser("scheduler", help="run the scheduled cycle forever")
    args = parser.parse_args(argv)

    if args.command == "refresh":
        return 0 if refresh() else 1
    if args.command == "scheduler":
        interval = max(1, settings.schedule_interval_minutes) * 60
        log.info("scheduler started", extra={"interval_minutes": settings.schedule_interval_minutes})
        while True:
            try:
                refresh()
            except Exception:
                log.exception("scheduled cycle crashed")
            time.sleep(interval)

    with get_sessionmaker()() as session:
        if args.command == "import":
            failed = 0
            for season in args.seasons:
                run = run_import(session, args.provider, args.competition, season)
                print(f"{season}: {run.status} seen={run.records_seen} created={run.matches_created} "
                      f"updated={run.matches_updated} unchanged={run.matches_unchanged}"
                      + (f" error={run.error_message}" if run.error_message else ""))
                failed += run.status != "success"
            return 1 if failed else 0
        if args.command == "predict":
            print(f"predicted {generate_live_predictions(session, args.competition)} upcoming matches")
        elif args.command == "evaluate":
            run = run_evaluation(session, args.competition, settings.evaluation_seasons)
            print("no finished matches to evaluate" if run is None else f"evaluated {run.n_matches} matches")
        elif args.command == "export":
            print(f"exported {export_static(session, args.competition, args.out)} matches to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
