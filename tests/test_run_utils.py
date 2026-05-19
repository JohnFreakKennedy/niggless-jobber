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
        id="2e211509efe7abcdef123456",
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

class TestJobDirname:
    def test_basic_format(self):
        name = _job_dirname(_listing())
        assert name == "LinkedIn_Google_2e211509efe7"

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
        name = _job_dirname(_listing(company="TENTENS Tech [SKELAR]"))
        assert "[" not in name
        assert "]" not in name
        assert "TENTENS_Tech_SKELAR" in name

    def test_company_leading_trailing_underscores_stripped(self):
        name = _job_dirname(_listing(company="  Acme  "))
        assert not name.split("_")[1].startswith("_")

    def test_company_truncated_to_30_chars(self):
        long = "A" * 50
        name = _job_dirname(_listing(company=long))
        company_part = name.split("_")[1]
        assert len(company_part) <= 30

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

    def test_three_parts_separated_by_underscore(self):
        name = _job_dirname(_listing())
        parts = name.split("_")
        # At minimum 3 segments: source, company, id
        assert len(parts) >= 3
        # Last part is the id prefix
        assert parts[-1] == "2e211509efe7"
