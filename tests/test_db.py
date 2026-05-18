"""Tests for storage/db.py using a temporary SQLite database."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import storage.db as db_mod
from storage.db import (
    already_applied,
    finish_run,
    get_job,
    insert_application,
    insert_job,
    is_run_in_progress,
    job_exists,
    start_run,
    update_application_status,
)


# All tests use the tmp_data_dir fixture (see conftest.py) which sets
# NIGGLESS_DATA_DIR to a temp dir and resets the db singleton.

pytestmark = pytest.mark.usefixtures("tmp_data_dir")


def _job_row(**overrides):
    base = {
        "id": "test-job-001",
        "source": "test",
        "title": "DevOps Engineer",
        "company": "Acme",
        "url": "https://example.com/1",
        "apply_url": "https://example.com/1",
        "description": "Kubernetes and Terraform.",
        "skills": '["kubernetes", "terraform"]',
        "location": "Remote",
        "salary_raw": "",
        "posted_at": "2026-05-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# jobs table
# ---------------------------------------------------------------------------

class TestJobExists:
    def test_returns_false_when_empty(self):
        assert job_exists("nonexistent-id") is False

    def test_returns_true_after_insert(self):
        insert_job(_job_row())
        assert job_exists("test-job-001") is True

    def test_false_for_different_id(self):
        insert_job(_job_row())
        assert job_exists("other-id") is False


class TestInsertJob:
    def test_duplicate_is_silently_ignored(self):
        row = _job_row()
        insert_job(row)
        insert_job(row)  # should not raise
        assert job_exists("test-job-001") is True

    def test_skills_list_auto_serialised(self):
        row = dict(_job_row())
        row["skills"] = ["kubernetes", "terraform"]  # list, not JSON string
        insert_job(row)
        fetched = get_job("test-job-001")
        assert fetched is not None
        assert isinstance(fetched["skills"], list)
        assert "kubernetes" in fetched["skills"]

    def test_datetime_posted_at_auto_serialised(self):
        row = dict(_job_row())
        row["posted_at"] = datetime(2026, 5, 1, tzinfo=timezone.utc)
        insert_job(row)
        assert job_exists("test-job-001") is True


class TestGetJob:
    def test_returns_none_for_missing(self):
        assert get_job("missing") is None

    def test_returns_dict_after_insert(self):
        insert_job(_job_row())
        fetched = get_job("test-job-001")
        assert fetched is not None
        assert fetched["title"] == "DevOps Engineer"
        assert fetched["company"] == "Acme"

    def test_skills_deserialised_as_list(self):
        insert_job(_job_row())
        fetched = get_job("test-job-001")
        assert isinstance(fetched["skills"], list)


# ---------------------------------------------------------------------------
# applications table
# ---------------------------------------------------------------------------

class TestAlreadyApplied:
    def test_false_when_no_application(self):
        insert_job(_job_row())
        assert already_applied("test-job-001") is False

    def test_true_after_applied_status(self):
        insert_job(_job_row())
        insert_application("test-job-001", "applied")
        assert already_applied("test-job-001") is True

    def test_true_for_dry_run_status(self):
        insert_job(_job_row())
        insert_application("test-job-001", "dry_run")
        assert already_applied("test-job-001") is True

    def test_false_for_failed_status(self):
        insert_job(_job_row())
        insert_application("test-job-001", "failed")
        assert already_applied("test-job-001") is False


class TestInsertApplication:
    def test_returns_integer_pk(self):
        insert_job(_job_row())
        pk = insert_application("test-job-001", "applied")
        assert isinstance(pk, int)
        assert pk > 0

    def test_optional_fields_stored(self):
        insert_job(_job_row())
        pk = insert_application(
            "test-job-001",
            "applied",
            reason="Easy Apply",
            cv_path="/tmp/cv.pdf",
            cover_letter_path="/tmp/cl.pdf",
            prompt_version_cv="v1",
        )
        db = db_mod.get_db()
        row = db.execute(
            "SELECT * FROM applications WHERE id = ?", [pk]
        ).fetchone()
        assert row is not None
        assert row["reason"] == "Easy Apply"
        assert row["cv_path"] == "/tmp/cv.pdf"
        assert row["prompt_version_cv"] == "v1"


class TestUpdateApplicationStatus:
    def test_updates_status(self):
        insert_job(_job_row())
        pk = insert_application("test-job-001", "pending")
        update_application_status(pk, "applied")
        db = db_mod.get_db()
        row = db.execute("SELECT status FROM applications WHERE id = ?", [pk]).fetchone()
        assert row["status"] == "applied"

    def test_updates_reason(self):
        insert_job(_job_row())
        pk = insert_application("test-job-001", "failed")
        update_application_status(pk, "failed", reason="CAPTCHA timeout")
        db = db_mod.get_db()
        row = db.execute("SELECT reason FROM applications WHERE id = ?", [pk]).fetchone()
        assert row["reason"] == "CAPTCHA timeout"


# ---------------------------------------------------------------------------
# run_log table
# ---------------------------------------------------------------------------

class TestRunLocking:
    def test_not_in_progress_initially(self):
        assert is_run_in_progress() is False

    def test_in_progress_after_start(self):
        start_run()
        assert is_run_in_progress() is True

    def test_not_in_progress_after_finish(self):
        run_id = start_run()
        finish_run(run_id, scraped=10, applied=3)
        assert is_run_in_progress() is False

    def test_start_returns_int_pk(self):
        pk = start_run()
        assert isinstance(pk, int)
        assert pk > 0

    def test_finish_stores_stats(self):
        run_id = start_run()
        finish_run(run_id, scraped=20, new=10, applied=5, failed=1, skipped=4)
        db = db_mod.get_db()
        row = db.execute("SELECT * FROM run_log WHERE id = ?", [run_id]).fetchone()
        assert row["scraped_count"] == 20
        assert row["applied_count"] == 5
        assert row["status"] == "completed"

    def test_finish_with_error_sets_error_status(self):
        run_id = start_run()
        finish_run(run_id, error="Something went wrong")
        db = db_mod.get_db()
        row = db.execute(
            "SELECT status, error_message FROM run_log WHERE id = ?", [run_id]
        ).fetchone()
        assert row["status"] == "error"
        assert row["error_message"] == "Something went wrong"
