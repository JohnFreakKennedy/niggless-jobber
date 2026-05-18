"""Tests for parser/schema.py and parser/extractor.py."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from parser.extractor import _extract_skills, _parse_date, _strip_html, normalise, normalise_many
from parser.schema import JobListing


# ---------------------------------------------------------------------------
# JobListing fingerprint
# ---------------------------------------------------------------------------

class TestJobListingFingerprint:
    def _make(self, **overrides):
        defaults = dict(
            source="test",
            title="DevOps Engineer",
            company="Acme",
            url="https://example.com/1",
            apply_url="https://example.com/1/apply",
            description="",
            posted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        )
        defaults.update(overrides)
        return JobListing(**defaults)

    def test_id_is_deterministic(self):
        a = self._make()
        b = self._make()
        assert a.id == b.id

    def test_id_changes_on_different_company(self):
        a = self._make(company="Acme")
        b = self._make(company="Globex")
        assert a.id != b.id

    def test_id_changes_on_different_title(self):
        a = self._make(title="DevOps Engineer")
        b = self._make(title="SRE")
        assert a.id != b.id

    def test_id_changes_on_different_source(self):
        a = self._make(source="linkedin")
        b = self._make(source="justjoinit")
        assert a.id != b.id

    def test_id_changes_on_different_date(self):
        a = self._make(posted_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
        b = self._make(posted_at=datetime(2026, 5, 2, tzinfo=timezone.utc))
        assert a.id != b.id

    def test_id_case_insensitive_title(self):
        a = self._make(title="devops engineer")
        b = self._make(title="DevOps Engineer")
        assert a.id == b.id

    def test_id_case_insensitive_company(self):
        a = self._make(company="acme")
        b = self._make(company="ACME")
        assert a.id == b.id

    def test_to_db_dict_contains_id(self):
        listing = self._make()
        d = listing.to_db_dict()
        assert d["id"] == listing.id

    def test_to_db_dict_skills_serialised_as_string(self):
        listing = self._make()
        listing.skills = ["kubernetes", "terraform"]
        d = listing.to_db_dict()
        assert isinstance(d["skills"], str)
        import json
        assert json.loads(d["skills"]) == ["kubernetes", "terraform"]


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_date_string(self):
        dt = _parse_date("2026-05-01")
        assert dt.year == 2026
        assert dt.month == 5
        assert dt.day == 1

    def test_iso_datetime_string(self):
        dt = _parse_date("2026-05-01T12:00:00Z")
        assert dt.year == 2026

    def test_relative_days_ago(self):
        import datetime as dt_mod
        now = datetime.now(timezone.utc)
        result = _parse_date("3 days ago")
        delta = now - result
        assert 2 <= delta.days <= 4

    def test_relative_weeks_ago(self):
        result = _parse_date("2 weeks ago")
        delta = datetime.now(timezone.utc) - result
        assert 13 <= delta.days <= 15

    def test_empty_string_returns_now(self):
        before = datetime.now(timezone.utc)
        result = _parse_date("")
        after = datetime.now(timezone.utc)
        assert before <= result <= after

    def test_none_returns_now(self):
        result = _parse_date(None)
        delta = datetime.now(timezone.utc) - result
        assert delta.total_seconds() < 5

    def test_unix_ms_timestamp(self):
        # 2026-01-01 00:00:00 UTC in ms
        ts_ms = 1767225600000
        dt = _parse_date(ts_ms)
        assert dt.year == 2026

    def test_datetime_passthrough(self):
        original = datetime(2026, 3, 15, tzinfo=timezone.utc)
        assert _parse_date(original) is original


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_empty_string(self):
        assert _strip_html("") == ""

    def test_plain_text_unchanged(self):
        text = "No HTML here"
        assert _strip_html(text) == text

    def test_nested_tags(self):
        html = "<ul><li>Kubernetes</li><li>Docker</li></ul>"
        result = _strip_html(html)
        assert "Kubernetes" in result
        assert "Docker" in result
        assert "<li>" not in result


# ---------------------------------------------------------------------------
# _extract_skills
# ---------------------------------------------------------------------------

class TestExtractSkills:
    def test_extracts_known_skills(self):
        text = "Experience with Kubernetes, Terraform, and AWS required."
        skills = _extract_skills(text)
        assert "kubernetes" in skills
        assert "terraform" in skills
        assert "aws" in skills

    def test_empty_text_returns_empty(self):
        assert _extract_skills("") == []

    def test_case_insensitive(self):
        skills = _extract_skills("Proficient in PYTHON and DOCKER.")
        assert "python" in skills
        assert "docker" in skills

    def test_alias_normalisation_k8s(self):
        skills = _extract_skills("Deploy to k8s clusters")
        assert "kubernetes" in skills
        assert "k8s" not in skills

    def test_alias_normalisation_golang(self):
        skills = _extract_skills("Written in Golang")
        assert "go" in skills
        assert "golang" not in skills

    def test_alias_normalisation_postgres(self):
        skills = _extract_skills("Uses Postgres database")
        assert "postgresql" in skills

    def test_returns_sorted(self):
        skills = _extract_skills("Python, AWS, Docker")
        assert skills == sorted(skills)

    def test_no_false_positives(self):
        skills = _extract_skills("We value hard work and dedication.")
        assert skills == []


# ---------------------------------------------------------------------------
# normalise
# ---------------------------------------------------------------------------

class TestNormalise:
    def test_basic_normalisation(self, sample_raw_listing):
        from parser.extractor import normalise
        listing = normalise(sample_raw_listing, "test")
        assert listing is not None
        assert listing.title == "DevOps Engineer"
        assert listing.company == "Acme Corp"
        assert listing.source == "test"

    def test_skills_extracted_from_description(self, sample_raw_listing):
        listing = normalise(sample_raw_listing, "test")
        assert listing is not None
        assert "kubernetes" in listing.skills
        assert "terraform" in listing.skills
        assert "aws" in listing.skills

    def test_missing_title_returns_none(self, sample_raw_listing):
        sample_raw_listing["title"] = ""
        assert normalise(sample_raw_listing, "test") is None

    def test_missing_company_returns_none(self, sample_raw_listing):
        sample_raw_listing["company"] = ""
        assert normalise(sample_raw_listing, "test") is None

    def test_html_stripped_from_description(self):
        raw = {
            "title": "SRE",
            "company": "Globex",
            "url": "https://example.com/2",
            "apply_url": "https://example.com/2",
            "description": "<p>We need <b>Kubernetes</b> expertise.</p>",
            "posted_at": "2026-05-01",
        }
        listing = normalise(raw, "test")
        assert listing is not None
        assert "<p>" not in listing.description
        assert "Kubernetes" in listing.description

    def test_falls_back_apply_url_to_url(self):
        raw = {
            "title": "SRE",
            "company": "Globex",
            "url": "https://example.com/3",
            "description": "",
            "posted_at": "2026-05-01",
        }
        listing = normalise(raw, "test")
        assert listing is not None
        assert listing.apply_url == "https://example.com/3"

    def test_location_transliterated(self):
        raw = {
            "title": "Engineer",
            "company": "Co",
            "url": "https://x.com",
            "description": "",
            "location": "Krak\u00f3w",
            "posted_at": "2026-05-01",
        }
        listing = normalise(raw, "test")
        assert listing is not None
        assert listing.location == "Krakow"


class TestNormaliseMany:
    def test_filters_out_invalid(self):
        raws = [
            {"title": "SRE", "company": "A", "url": "https://a.com", "description": ""},
            {"title": "", "company": "B", "url": "https://b.com", "description": ""},
        ]
        results = normalise_many(raws, "test")
        assert len(results) == 1
        assert results[0].company == "A"

    def test_empty_list(self):
        assert normalise_many([], "test") == []
