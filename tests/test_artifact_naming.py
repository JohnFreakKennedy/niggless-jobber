"""Tests for artifact file naming (Company_cv.tex, Company_cover_letter.tex)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils import artifact_stem, sanitize_name


# ---------------------------------------------------------------------------
# sanitize_name
# ---------------------------------------------------------------------------

class TestSanitizeName:
    def test_spaces_become_underscores(self):
        assert sanitize_name("Acme Corp") == "Acme_Corp"

    def test_special_chars_removed(self):
        assert sanitize_name("TENTENS Tech [SKELAR]") == "TENTENS_Tech_SKELAR"

    def test_leading_trailing_stripped(self):
        assert not sanitize_name("  Foo  ").startswith("_")

    def test_truncated_to_max_len(self):
        assert len(sanitize_name("A" * 50, max_len=20)) <= 20

    def test_empty_returns_empty(self):
        assert sanitize_name("") == ""

    def test_only_special_chars_returns_empty(self):
        assert sanitize_name("[---]") == ""

    def test_consecutive_underscores_collapsed(self):
        assert "__" not in sanitize_name("A  B")

    def test_hyphens_become_underscores(self):
        assert sanitize_name("some-company") == "some_company"


# ---------------------------------------------------------------------------
# artifact_stem
# ---------------------------------------------------------------------------

class TestArtifactStem:
    def test_cv_stem(self):
        assert artifact_stem("Google", "cv") == "Google_cv"

    def test_cover_letter_stem(self):
        assert artifact_stem("Google", "cover_letter") == "Google_cover_letter"

    def test_company_sanitized(self):
        stem = artifact_stem("Acme Corp", "cv")
        assert stem == "Acme_Corp_cv"

    def test_special_chars_in_company(self):
        stem = artifact_stem("TENTENS Tech [SKELAR]", "cv")
        assert "[" not in stem
        assert "TENTENS_Tech_SKELAR_cv" == stem

    def test_empty_company_falls_back(self):
        stem = artifact_stem("", "cv")
        assert stem == "Unknown_cv"

    def test_only_special_chars_falls_back(self):
        stem = artifact_stem("[[[---]]]", "cover_letter")
        assert stem == "Unknown_cover_letter"


# ---------------------------------------------------------------------------
# tailor_cv writes Company_cv.tex
# ---------------------------------------------------------------------------

class TestTailorCvFilename:
    async def test_output_file_named_with_company(self, tmp_path):
        from types import SimpleNamespace
        from cv_editor.editor import tailor_cv

        listing = SimpleNamespace(
            id="abc123def456789",
            source="test",
            title="DevOps Engineer",
            company="Globex",
            description="We need Kubernetes and Terraform.",
            skills=["kubernetes"],
            location="Remote",
        )

        mock_client = MagicMock()
        mock_client.config = MagicMock(cv_max_tokens=4096)
        mock_client.complete = AsyncMock(
            return_value=(
                r"\documentclass{article}\begin{document}CV content\end{document}"
            )
        )

        result = await tailor_cv(listing, tmp_path, mock_client)

        assert result.name == "Globex_cv.tex"
        assert result.exists()

    async def test_output_file_company_sanitized(self, tmp_path):
        from types import SimpleNamespace
        from cv_editor.editor import tailor_cv

        listing = SimpleNamespace(
            id="abc123def456789",
            source="test",
            title="Engineer",
            company="Acme Corp",
            description="",
            skills=[],
            location="",
        )

        mock_client = MagicMock()
        mock_client.config = MagicMock(cv_max_tokens=4096)
        mock_client.complete = AsyncMock(
            return_value=r"\documentclass{article}\begin{document}CV\end{document}"
        )

        result = await tailor_cv(listing, tmp_path, mock_client)
        assert result.name == "Acme_Corp_cv.tex"


# ---------------------------------------------------------------------------
# generate_cover_letter writes Company_cover_letter.tex
# ---------------------------------------------------------------------------

class TestCoverLetterFilename:
    async def test_output_file_named_with_company(self, tmp_path):
        from types import SimpleNamespace
        from cover_letter.generator import generate_cover_letter

        listing = SimpleNamespace(
            id="abc123def456789",
            source="test",
            title="DevOps Engineer",
            company="Globex",
            description="Join our team.",
            skills=["docker"],
            location="Remote",
        )

        mock_client = MagicMock()
        mock_client.config = MagicMock(cover_letter_max_tokens=1024)
        mock_client.complete = AsyncMock(
            return_value="Dear Hiring Manager, I am excited to apply."
        )

        result = await generate_cover_letter(listing, tmp_path, mock_client)
        assert result.name == "Globex_cover_letter.tex"
        assert result.exists()
