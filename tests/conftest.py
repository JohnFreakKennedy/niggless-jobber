"""Shared pytest fixtures."""
from __future__ import annotations

import os
import pytest


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Point NIGGLESS_DATA_DIR at a throwaway temp directory and reset the
    db singleton so each test gets a fresh database.
    """
    monkeypatch.setenv("NIGGLESS_DATA_DIR", str(tmp_path))
    import storage.db as _db_mod
    _db_mod._db = None
    yield tmp_path
    _db_mod._db = None


@pytest.fixture()
def vault_env(tmp_data_dir, monkeypatch):
    """
    Configure vault to use passphrase mode (avoids touching OS keyring)
    and point it at the temp data dir.
    """
    monkeypatch.setenv("NIGGLESS_USE_PASSPHRASE", "1")
    monkeypatch.setenv("NIGGLESS_PASSPHRASE", "test-passphrase")
    return tmp_data_dir


@pytest.fixture()
def sample_raw_listing():
    return {
        "title": "DevOps Engineer",
        "company": "Acme Corp",
        "url": "https://example.com/jobs/1",
        "apply_url": "https://example.com/jobs/1/apply",
        "description": "We use Kubernetes, Terraform, and AWS. Python scripting required.",
        "location": "Remote",
        "salary_raw": "$6,000 - $9,000/month",
        "posted_at": "2026-05-01",
        "skills": [],
    }


@pytest.fixture()
def sample_job_listing(sample_raw_listing):
    from parser.extractor import normalise
    return normalise(sample_raw_listing, "test")
