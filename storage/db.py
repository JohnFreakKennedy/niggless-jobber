"""SQLite database wrapper using sqlite-utils."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlite_utils

log = logging.getLogger(__name__)

_db: sqlite_utils.Database | None = None


def _data_dir() -> Path:
    import os
    path = Path(os.environ.get("NIGGLESS_DATA_DIR", "") or Path.home() / ".niggless-jobber")
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db() -> sqlite_utils.Database:
    global _db
    if _db is None:
        db_path = _data_dir() / "jobs.db"
        _db = sqlite_utils.Database(db_path)
        # Enable column-name access on raw cursor results (sqlite3.Row).
        # sqlite-utils does not set row_factory by default.
        _db.conn.row_factory = sqlite3.Row
        _apply_schema(_db)
        log.debug("Opened database at %s", db_path)
    return _db


def _apply_schema(db: sqlite_utils.Database) -> None:
    schema_path = Path(__file__).parent / "schema.sql"
    with schema_path.open() as fh:
        db.conn.executescript(fh.read())
    db.conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# jobs table
# ---------------------------------------------------------------------------

def job_exists(job_id: str) -> bool:
    db = get_db()
    return db.execute("SELECT 1 FROM jobs WHERE id = ?", [job_id]).fetchone() is not None


def insert_job(listing: dict) -> None:
    db = get_db()
    row = dict(listing)
    if isinstance(row.get("skills"), list):
        row["skills"] = json.dumps(row["skills"])
    if isinstance(row.get("posted_at"), datetime):
        row["posted_at"] = row["posted_at"].isoformat()
    row.setdefault("scraped_at", now_iso())
    db["jobs"].insert(row, ignore=True)


def get_job(job_id: str) -> dict | None:
    db = get_db()
    row = db.execute("SELECT * FROM jobs WHERE id = ?", [job_id]).fetchone()
    if row is None:
        return None
    # sqlite3.Row does not support dict() directly in Python < 3.12
    d = {k: row[k] for k in row.keys()}
    d["skills"] = json.loads(d["skills"])
    return d


# ---------------------------------------------------------------------------
# applications table
# ---------------------------------------------------------------------------

def already_applied(job_id: str) -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM applications WHERE job_id = ? AND status IN ('applied', 'dry_run')",
        [job_id],
    ).fetchone()
    return row is not None


def insert_application(
    job_id: str,
    status: str,
    *,
    reason: str | None = None,
    applied_at: datetime | None = None,
    retry_count: int = 0,
    cv_path: str | None = None,
    cover_letter_path: str | None = None,
    prompt_version_cv: str | None = None,
    prompt_version_cl: str | None = None,
) -> int:
    db = get_db()
    row = {
        "job_id": job_id,
        "status": status,
        "reason": reason,
        "applied_at": applied_at.isoformat() if applied_at else None,
        "retry_count": retry_count,
        "cv_path": cv_path,
        "cover_letter_path": cover_letter_path,
        "prompt_version_cv": prompt_version_cv,
        "prompt_version_cl": prompt_version_cl,
        "created_at": now_iso(),
    }
    return db["applications"].insert(row).last_pk  # type: ignore[return-value]


def get_pending_retries(max_retries: int = 3, limit: int = 10) -> list[dict]:
    db = get_db()
    rows = db.execute(
        """
        SELECT a.*, j.title, j.company, j.apply_url, j.source
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.status = 'failed' AND a.retry_count < ?
        ORDER BY a.created_at ASC
        LIMIT ?
        """,
        [max_retries, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def update_application_status(
    app_id: int,
    status: str,
    *,
    reason: str | None = None,
    applied_at: datetime | None = None,
    retry_count: int | None = None,
) -> None:
    db = get_db()
    updates: dict = {"status": status}
    if reason is not None:
        updates["reason"] = reason
    if applied_at is not None:
        updates["applied_at"] = applied_at.isoformat()
    if retry_count is not None:
        updates["retry_count"] = retry_count
    db["applications"].update(app_id, updates)


# ---------------------------------------------------------------------------
# run_log table
# ---------------------------------------------------------------------------

def start_run() -> int:
    db = get_db()
    return db["run_log"].insert(
        {"started_at": now_iso(), "status": "running"}
    ).last_pk  # type: ignore[return-value]


def finish_run(
    run_id: int,
    *,
    scraped: int = 0,
    new: int = 0,
    applied: int = 0,
    failed: int = 0,
    skipped: int = 0,
    error: str | None = None,
) -> None:
    db = get_db()
    db["run_log"].update(
        run_id,
        {
            "finished_at": now_iso(),
            "status": "error" if error else "completed",
            "scraped_count": scraped,
            "new_count": new,
            "applied_count": applied,
            "failed_count": failed,
            "skipped_count": skipped,
            "error_message": error,
        },
    )


def is_run_in_progress() -> bool:
    db = get_db()
    row = db.execute(
        "SELECT 1 FROM run_log WHERE status = 'running' AND started_at > datetime('now', '-2 hours')"
    ).fetchone()
    return row is not None
