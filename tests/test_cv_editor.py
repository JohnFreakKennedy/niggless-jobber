"""Tests for cv_editor/editor.py pure functions."""
from __future__ import annotations

from datetime import date

import pytest

from cv_editor.editor import (
    _display_years,
    _sanitize_tex,
    calculate_experience_years,
    extract_required_years,
    patch_years_in_tex,
)


# ---------------------------------------------------------------------------
# calculate_experience_years
# ---------------------------------------------------------------------------

class TestCalculateExperienceYears:
    def test_empty_returns_zero(self):
        assert calculate_experience_years([]) == 0.0

    def test_single_job_one_year(self):
        history = [{"start": "2025-01", "end": "2026-01"}]
        result = calculate_experience_years(history)
        assert abs(result - 1.0) < 0.1

    def test_multiple_non_overlapping(self):
        history = [
            {"start": "2021-01", "end": "2022-01"},
            {"start": "2023-01", "end": "2024-01"},
        ]
        result = calculate_experience_years(history)
        assert abs(result - 2.0) < 0.1

    def test_overlapping_intervals_counted_once(self):
        # Two simultaneous jobs should not double-count
        history = [
            {"start": "2024-01", "end": "2025-01"},
            {"start": "2024-06", "end": "2025-06"},
        ]
        result = calculate_experience_years(history)
        # Overlap: 2024-01 to 2025-06 = ~1.5 years, NOT 2 years
        assert result < 1.7

    def test_present_keyword(self):
        history = [{"start": "2024-01", "end": "present"}]
        result = calculate_experience_years(history)
        # Should be >= 1 year since 2024
        assert result >= 1.0

    def test_invalid_entry_skipped(self):
        history = [
            {"start": "2025-01", "end": "2026-01"},
            {"start": "bad-date", "end": "2026-01"},
        ]
        result = calculate_experience_years(history)
        assert result > 0

    def test_four_years_of_history(self):
        history = [
            {"start": "2021-02", "end": "2022-08"},  # 18 months
            {"start": "2022-10", "end": "2024-12"},  # 26 months
            {"start": "2025-02", "end": "2026-01"},  # 11 months
        ]
        result = calculate_experience_years(history)
        # Total ~55 months = ~4.6 years
        assert 4.0 <= result <= 5.0


# ---------------------------------------------------------------------------
# _display_years
# ---------------------------------------------------------------------------

class TestDisplayYears:
    def test_rounds_to_nearest_integer(self):
        assert _display_years(4.8) == "5"
        assert _display_years(4.2) == "4"
        assert _display_years(5.0) == "5"

    def test_zero(self):
        assert _display_years(0.0) == "0"


# ---------------------------------------------------------------------------
# extract_required_years
# ---------------------------------------------------------------------------

class TestExtractRequiredYears:
    def test_5_plus_years(self):
        jd = "We are looking for a candidate with 5+ years of experience in DevOps."
        assert extract_required_years(jd) == 5

    def test_at_least_3_years(self):
        jd = "At least 3 years experience with Kubernetes required."
        assert extract_required_years(jd) == 3

    def test_minimum_4_years(self):
        jd = "Minimum 4 years of relevant experience."
        assert extract_required_years(jd) == 4

    def test_returns_max_when_multiple(self):
        jd = "2+ years of Python; 5+ years of overall engineering experience."
        result = extract_required_years(jd)
        assert result == 5

    def test_no_years_mentioned_returns_none(self):
        jd = "Strong communication skills and attention to detail."
        assert extract_required_years(jd) is None

    def test_empty_string_returns_none(self):
        assert extract_required_years("") is None


# ---------------------------------------------------------------------------
# patch_years_in_tex
# ---------------------------------------------------------------------------

class TestPatchYearsInTex:
    _TEMPLATE = (
        "DevOps Engineer with 3 years of experience, having worked in "
        "multiple types of companies."
    )

    def test_replaces_stated_years(self):
        result = patch_years_in_tex(self._TEMPLATE, "5")
        assert "5 years of experience" in result
        assert "3 years of experience" not in result

    def test_no_match_returns_unchanged(self):
        tex = r"\section{SKILLS}"
        result = patch_years_in_tex(tex, "5")
        assert result == tex

    def test_replaces_multiple_occurrences(self):
        tex = "Engineer with 3 years of experience. Previous: 2 years of experience."
        result = patch_years_in_tex(tex, "5")
        assert result.count("5 years of experience") == 2

    def test_handles_over_prefix(self):
        tex = "Developer with over 3 years of experience."
        result = patch_years_in_tex(tex, "5")
        assert "5 years of experience" in result

    def test_handles_more_than_prefix(self):
        tex = "Developer with more than 2 years of experience."
        result = patch_years_in_tex(tex, "4")
        assert "4 years of experience" in result

    def test_handles_plus_suffix(self):
        tex = "Engineer with 4+ years of experience in cloud."
        result = patch_years_in_tex(tex, "6")
        assert "6 years of experience" in result


# ---------------------------------------------------------------------------
# _sanitize_tex
# ---------------------------------------------------------------------------

class TestSanitizeTex:
    def test_strips_everything_after_end_document(self):
        tex = r"""
\begin{document}
Hello world
\end{document}
\title{Stray Template}
\begin{document}
\maketitle
\end{document}
"""
        result = _sanitize_tex(tex)
        assert r"\end{document}" in result
        assert r"\title{Stray Template}" not in result
        assert r"\maketitle" not in result

    def test_only_one_end_document_in_output(self):
        tex = r"\begin{document}Content\end{document}Garbage\end{document}"
        result = _sanitize_tex(tex)
        assert result.count(r"\end{document}") == 1

    def test_removes_stray_hash_lines(self):
        tex = "Line one\n #\nLine two\n"
        result = _sanitize_tex(tex)
        assert " #" not in result
        assert "Line one" in result
        assert "Line two" in result

    def test_preserves_hash_in_latex_commands(self):
        # LaTeX uses # in macro parameters: \newcommand{\cmd}[2]{#1 and #2}
        tex = r"\newcommand{\foo}[2]{#1 and #2}" + "\n\\end{document}\n"
        result = _sanitize_tex(tex)
        assert r"#1" in result
        assert r"#2" in result

    def test_clean_tex_passes_through_unchanged(self):
        tex = r"\documentclass{article}\begin{document}Hello\end{document}" + "\n"
        result = _sanitize_tex(tex)
        assert r"\documentclass" in result
        assert r"\end{document}" in result

    def test_no_end_document_returns_content(self):
        tex = "Some content without the closing tag\n"
        result = _sanitize_tex(tex)
        assert "Some content" in result
