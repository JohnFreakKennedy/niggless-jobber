"""Tests for the job output-directory naming helper."""
from __future__ import annotations

import pytest

from run import _job_dirname


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _listing(**kwargs):
    """Return a minimal namespace object that mimics JobListing."""
    from types import SimpleNamespace
    defaults = dict(
        source="linkedin",
        company="Google",
        location="Remote",
        id="2e211509efe7abcdef123456",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

class TestJobDirname:
    def test_basic_format_with_location(self):
        name = _job_dirname(_listing())
        assert name == "LinkedIn_Google_Remote_2e211509efe7"

    def test_format_without_location(self):
        name = _job_dirname(_listing(location=""))
        assert name == "LinkedIn_Google_2e211509efe7"

    def test_location_included(self):
        name = _job_dirname(_listing(location="Warsaw"))
        assert "Warsaw" in name

    def test_location_sanitized(self):
        name = _job_dirname(_listing(location="New York, NY"))
        assert "," not in name

    def test_id_prefix_is_12_chars(self):
        name = _job_dirname(_listing(id="abcdef123456789"))
        assert name.endswith("_abcdef123456")

    def test_source_display_names(self):
        assert _job_dirname(_listing(source="linkedin")).startswith("LinkedIn_")
        assert _job_dirname(_listing(source="justjoinit")).startswith("JustJoinIT_")
        assert _job_dirname(_listing(source="djinni")).startswith("Djinni_")
        assert _job_dirname(_listing(source="indeed")).startswith("Indeed_")
        assert _job_dirname(_listing(source="dou")).startswith("Dou_")

    def test_unknown_source_title_cased(self):
        name = _job_dirname(_listing(source="myboard"))
        assert name.startswith("Myboard_")

    def test_company_spaces_replaced(self):
        name = _job_dirname(_listing(company="Acme Corp"))
        assert "Acme_Corp" in name

    def test_company_special_chars_removed(self):
        name = _job_dirname(_listing(company="Example Tech [GROUP]"))
        assert "[" not in name
        assert "]" not in name
        assert "Example_Tech_GROUP" in name

    def test_empty_company_falls_back(self):
        name = _job_dirname(_listing(company=""))
        assert "Unknown" in name

    def test_company_only_special_chars_falls_back(self):
        name = _job_dirname(_listing(company="[[[---]]]"))
        assert "Unknown" in name

    def test_no_filesystem_unsafe_chars(self):
        name = _job_dirname(_listing(company="Google/Alphabet & Co."))
        for ch in r'<>:"/\\|?*':
            assert ch not in name

    def test_consecutive_underscores_collapsed(self):
        name = _job_dirname(_listing(company="A  B   C"))
        assert "__" not in name

    def test_parts_separated_by_underscore(self):
        name = _job_dirname(_listing())
        parts = name.split("_")
        # source_company_location_id = at least 4 segments
        assert len(parts) >= 4
        assert parts[-1] == "2e211509efe7"
