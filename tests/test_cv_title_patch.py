"""Tests for CV job-title patching in cv_editor/editor.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace

import pytest

from cv_editor.editor import patch_job_title_in_tex


_HEADER = r"""
\begin{tabular*}{\textwidth}{l@{\extracolsep{\fill}}r}
  \textbf{\huge Artem Dankov} & Email: foo@bar.com\\
    Cloud Software Engineer & { +48797011929} Linkedin
    \smallskip
\end{tabular*}
"""


class TestPatchJobTitleInTex:
    def test_replaces_subtitle(self):
        result = patch_job_title_in_tex(_HEADER, "DevOps Engineer")
        assert "DevOps Engineer" in result
        assert "Cloud Software Engineer" not in result

    def test_other_content_unchanged(self):
        result = patch_job_title_in_tex(_HEADER, "SRE")
        assert r"\textbf{\huge Artem Dankov}" in result
        assert "foo@bar.com" in result
        assert "+48797011929" in result

    def test_no_match_returns_unchanged(self):
        tex = r"\documentclass{article}\begin{document}Hello\end{document}"
        assert patch_job_title_in_tex(tex, "SRE") == tex

    def test_various_titles(self):
        for title in ["Platform Engineer", "Site Reliability Engineer", "Cloud Engineer"]:
            result = patch_job_title_in_tex(_HEADER, title)
            assert title in result

    def test_title_with_special_latex_chars_safe(self):
        # Title containing characters that appear in JD titles
        result = patch_job_title_in_tex(_HEADER, "Senior DevOps / SRE")
        assert "Senior DevOps / SRE" in result

    def test_idempotent_on_same_title(self):
        once = patch_job_title_in_tex(_HEADER, "DevOps Engineer")
        twice = patch_job_title_in_tex(once, "DevOps Engineer")
        assert once == twice
