"""Tests for scrapers/linkedin.py -- guest API parsing and URL building."""
from __future__ import annotations

import pytest

from scrapers.linkedin import _guest_params, _parse_guest_html


# ---------------------------------------------------------------------------
# Fixture HTML
# Reproduces the structure returned by LinkedIn's /jobs-guest API.
# ---------------------------------------------------------------------------

_GUEST_HTML_TWO_JOBS = """<!DOCTYPE html>
<li>
  <div class="base-card relative w-full base-search-card base-search-card--link job-search-card"
       data-entity-urn="urn:li:jobPosting:1001">
    <a class="base-card__full-link absolute top-0"
       href="https://www.linkedin.com/jobs/view/devops-engineer-at-acme-1001?position=1">
      <span class="sr-only">DevOps Engineer</span>
    </a>
    <h3 class="base-search-card__title">DevOps Engineer</h3>
    <h4 class="base-search-card__subtitle">
      <a href="https://www.linkedin.com/company/acme">Acme Corp</a>
    </h4>
    <span class="job-search-card__location">Remote</span>
    <time class="job-search-card__listdate" datetime="2026-05-01">1 month ago</time>
  </div>
</li>
<li>
  <div class="base-card relative w-full base-search-card base-search-card--link job-search-card"
       data-entity-urn="urn:li:jobPosting:1002">
    <a class="base-card__full-link absolute top-0"
       href="https://www.linkedin.com/jobs/view/sre-at-globex-1002?position=2">
      <span class="sr-only">Site Reliability Engineer</span>
    </a>
    <h3 class="base-search-card__title">Site Reliability Engineer</h3>
    <h4 class="base-search-card__subtitle">
      <a href="https://www.linkedin.com/company/globex">Globex Inc</a>
    </h4>
    <span class="job-search-card__location">Warsaw, Poland</span>
    <time class="job-search-card__listdate" datetime="2026-04-20">3 weeks ago</time>
  </div>
</li>
"""

_GUEST_HTML_EMPTY = "<!DOCTYPE html>"


# ---------------------------------------------------------------------------
# _parse_guest_html
# ---------------------------------------------------------------------------

class TestParseGuestHtml:
    def test_parses_two_jobs(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        assert len(results) == 2

    def test_first_job_fields(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        first = results[0]
        assert first["id"] == "li-1001"
        assert first["title"] == "DevOps Engineer"
        assert first["company"] == "Acme Corp"
        assert first["location"] == "Remote"
        assert first["posted_at"] == "2026-05-01"

    def test_second_job_fields(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        second = results[1]
        assert second["id"] == "li-1002"
        assert second["title"] == "Site Reliability Engineer"
        assert second["company"] == "Globex Inc"
        assert second["location"] == "Warsaw, Poland"

    def test_url_is_clean(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        for r in results:
            assert "?" not in r["url"]
            assert r["url"].startswith("https://www.linkedin.com/jobs/view/")

    def test_apply_url_matches_url(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        for r in results:
            assert r["url"] == r["apply_url"]

    def test_skills_initialised_empty(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        for r in results:
            assert r["skills"] == []

    def test_description_initialised_empty(self):
        results = _parse_guest_html(_GUEST_HTML_TWO_JOBS)
        for r in results:
            assert r["description"] == ""

    def test_empty_html_returns_empty_list(self):
        results = _parse_guest_html(_GUEST_HTML_EMPTY)
        assert results == []

    def test_malformed_card_skipped_gracefully(self):
        html = """<div class="base-search-card" data-entity-urn="urn:li:jobPosting:999">
        <!-- no title, no link -->
        </div>"""
        results = _parse_guest_html(html)
        assert results == []


# ---------------------------------------------------------------------------
# _guest_params
# ---------------------------------------------------------------------------

class TestGuestParams:
    def test_remote_adds_f_wt(self):
        params = _guest_params("DevOps Engineer", "remote")
        assert params.get("f_WT") == "2"
        assert "location" not in params

    def test_city_location_adds_location(self):
        params = _guest_params("SRE", "Warsaw")
        assert params.get("location") == "Warsaw"
        assert "f_WT" not in params

    def test_start_parameter_passed(self):
        params = _guest_params("DevOps", "remote", start=25)
        assert params["start"] == 25

    def test_keyword_is_included(self):
        params = _guest_params("Platform Engineer", "remote")
        assert params["keywords"] == "Platform Engineer"

    def test_sort_by_date_descending(self):
        params = _guest_params("SRE", "remote")
        assert params.get("sortBy") == "DD"

    def test_empty_location_excludes_both_filters(self):
        params = _guest_params("DevOps", "")
        assert "f_WT" not in params
        assert "location" not in params
