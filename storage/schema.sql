-- niggless-jobber SQLite schema
-- Applied automatically by storage/db.py on first run.

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,       -- SHA-256 fingerprint
    source      TEXT NOT NULL,          -- linkedin | justjoinit | dou | djinni | indeed
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    url         TEXT NOT NULL,
    apply_url   TEXT NOT NULL,
    description TEXT NOT NULL,
    skills      TEXT NOT NULL,          -- JSON array of strings
    location    TEXT,
    salary_raw  TEXT,
    posted_at   TEXT NOT NULL,          -- ISO 8601
    scraped_at  TEXT NOT NULL           -- ISO 8601
);

CREATE TABLE IF NOT EXISTS applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL REFERENCES jobs(id),
    status              TEXT NOT NULL,  -- applied | failed | skipped | manual_review_needed | dry_run
    reason              TEXT,
    applied_at          TEXT,           -- ISO 8601; NULL until submitted
    retry_count         INTEGER NOT NULL DEFAULT 0,
    cv_path             TEXT,
    cover_letter_path   TEXT,
    prompt_version_cv   TEXT,
    prompt_version_cl   TEXT,
    created_at          TEXT NOT NULL   -- ISO 8601
);

CREATE TABLE IF NOT EXISTS run_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,      -- ISO 8601
    finished_at     TEXT,
    status          TEXT NOT NULL,      -- running | completed | error
    pid             INTEGER,            -- OS PID of the run.py process
    scraped_count   INTEGER DEFAULT 0,
    new_count       INTEGER DEFAULT 0,
    applied_count   INTEGER DEFAULT 0,
    failed_count    INTEGER DEFAULT 0,
    skipped_count   INTEGER DEFAULT 0,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_applications_job_id   ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_applications_status   ON applications(status);
CREATE INDEX IF NOT EXISTS idx_applications_applied  ON applications(applied_at);
