"""Write the public read-only API responses to JSON files.

Used for hosting without a running API (for example GitHub Pages): a scheduled job refreshes the
database, calls this, and publishes the files next to the web app. The files hold exactly what the
API routes return, so the web app shows the same data either way.

    <out>/status-PL.json               GET /api/v1/status
    <out>/matches-PL-scheduled.json    GET /api/v1/matches?status=scheduled  (every match, kick-off ascending)
    <out>/matches-PL-finished.json     GET /api/v1/matches?status=finished
    <out>/match/<id>.json              GET /api/v1/matches/<id>
    <out>/evaluation-PL.json           GET /api/v1/evaluation/latest  (absent if none has been run)
"""

import shutil
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import api
from app.config import get_settings

EXPORTED_STATUSES = ("scheduled", "finished")  # the two lists the web app shows


def _write(path: Path, model: BaseModel) -> None:
    path.write_text(model.model_dump_json(), encoding="utf-8")


def export_static(db: Session, competition: str, out_dir: Path) -> int:
    """Export one competition. Returns the number of match detail files written."""
    config = get_settings()
    code = competition.upper()
    match_dir = out_dir / "match"
    if match_dir.exists():
        shutil.rmtree(match_dir)  # match ids from an older database must not linger
    match_dir.mkdir(parents=True)

    _write(out_dir / f"status-{code}.json", api.data_status(db, config, code))

    written = 0
    for status in EXPORTED_STATUSES:
        page = api.list_matches(db, config, competition=code, status=status, limit=1_000_000)
        _write(out_dir / f"matches-{code}-{status}.json", page)
        for item in page.items:
            _write(match_dir / f"{item.id}.json", api.get_match(item.id, db, config))
            written += 1

    evaluation_file = out_dir / f"evaluation-{code}.json"
    try:
        _write(evaluation_file, api.latest_evaluation(db, code))
    except HTTPException:
        evaluation_file.unlink(missing_ok=True)
    return written
