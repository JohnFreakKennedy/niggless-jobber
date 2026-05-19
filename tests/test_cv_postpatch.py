"""Tests that tailor_cv enforces job title and years after AI output."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from cv_editor.editor import patch_job_title_in_tex, patch_years_in_tex

# Read the real base CV template so AI-mock output has the same structure
# (small fraction of lines differ => diff guard does NOT fire, which lets us
# verify the post-patch rather than the revert-to-pre-patched fallback).
_REAL_BASE = (Path(__file__).parent.parent / "templates" / "cv.tex").read_text(encoding="utf-8")


def _make_listing(title: str = "DevOps Engineer", company: str = "Acme"):
    return SimpleNamespace(
        id="abc123456789",
        title=title,
        company=company,
        description="5 years of experience required",
        skills=["kubernetes", "terraform"],
        source="linkedin",
    )


def _ai_output_with_subtitle(subtitle: str) -> str:
    """Return the real base CV with just the subtitle line swapped out."""
    return patch_job_title_in_tex(_REAL_BASE, subtitle)


# ---------------------------------------------------------------------------
# Post-AI enforcement: job title
# ---------------------------------------------------------------------------

class TestPostPatchJobTitle:
    """
    The AI is allowed to return any subtitle.
    tailor_cv must overwrite it with listing.title regardless.
    """

    @pytest.mark.asyncio
    async def test_ai_company_name_overridden_with_job_title(self, tmp_path):
        """AI puts company name as subtitle -- must be replaced with job title."""
        # AI output: real template with "Jit Team" as subtitle (only ~1 line diff
        # from pre-patched, so the diff guard does NOT revert to pre-patched)
        ai_output = _ai_output_with_subtitle("Jit Team")

        from cv_editor.client import AIClient, AIConfig
        mock_client = AsyncMock(spec=AIClient)
        mock_client.complete = AsyncMock(return_value=ai_output)
        mock_client.config = AIConfig()

        from cv_editor.editor import tailor_cv
        with patch("cv_editor.editor._load_personal_info", return_value={"work_history": [
            {"start": "2021-01", "end": "2026-01"}
        ]}):
            tex_path = await tailor_cv(_make_listing(title="Platform Engineer"), tmp_path, mock_client)

        content = tex_path.read_text(encoding="utf-8")
        assert "Platform Engineer" in content, "Job title must appear in final CV"
        assert "Jit Team" not in content, "Company name must not appear as subtitle"

    @pytest.mark.asyncio
    async def test_ai_arbitrary_subtitle_overridden(self, tmp_path):
        """AI writes random text as subtitle -- overridden with listing.title."""
        ai_output = _ai_output_with_subtitle("Senior Anything")

        from cv_editor.client import AIClient, AIConfig
        mock_client = AsyncMock(spec=AIClient)
        mock_client.complete = AsyncMock(return_value=ai_output)
        mock_client.config = AIConfig()

        from cv_editor.editor import tailor_cv
        with patch("cv_editor.editor._load_personal_info", return_value={"work_history": [
            {"start": "2021-01", "end": "2026-01"}
        ]}):
            tex_path = await tailor_cv(_make_listing(title="SRE"), tmp_path, mock_client)

        content = tex_path.read_text(encoding="utf-8")
        assert "SRE" in content
        assert "Senior Anything" not in content


# ---------------------------------------------------------------------------
# Post-AI enforcement: years of experience
# ---------------------------------------------------------------------------

class TestPostPatchYears:
    @pytest.mark.asyncio
    async def test_ai_wrong_years_corrected(self, tmp_path):
        """AI writes wrong years in summary -- post-patch corrects it."""
        # Produce a realistic AI output: same as real template but with wrong years
        ai_output = patch_years_in_tex(_REAL_BASE, "10")

        from cv_editor.client import AIClient, AIConfig
        mock_client = AsyncMock(spec=AIClient)
        mock_client.complete = AsyncMock(return_value=ai_output)
        mock_client.config = AIConfig()

        from cv_editor.editor import tailor_cv
        with patch("cv_editor.editor._load_personal_info", return_value={"work_history": [
            {"start": "2021-01", "end": "2026-01"}
        ]}):
            tex_path = await tailor_cv(_make_listing(), tmp_path, mock_client)

        content = tex_path.read_text(encoding="utf-8")
        # "10 years" must be gone; actual ~5 years should be present
        assert "10 years of experience" not in content
        assert "5 years of experience" in content
