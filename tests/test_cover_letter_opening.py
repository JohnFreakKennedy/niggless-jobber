"""Tests that cover letter company/title in the opening are programmatic, not AI."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cover_letter.generator import _assemble
from cover_letter.prompts import CL_SYSTEM_PROMPT, CL_USER_TEMPLATE


def _listing(**kw):
    defaults = dict(
        id="abc123", source="linkedin",
        title="DevOps Engineer", company="Globex",
        description="", skills=[], location="Remote",
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def _info():
    return {
        "name": {"full": "Artem Dankov"},
        "email": "test@example.com",
        "phone": "+48123456789",
        "address": {"city": "Wroclaw", "country": "Poland"},
    }


# ---------------------------------------------------------------------------
# Programmatic opening sentence
# ---------------------------------------------------------------------------

class TestProgrammaticOpening:
    def test_company_appears_before_ai_body(self):
        """Company name must be present in the template area before the AI body."""
        ai_body = "SENTINEL_BODY_START"
        tex = _assemble(ai_body, _listing(company="MegaCorp"), _info())
        sentinel_pos = tex.index(ai_body)
        pre_body = tex[:sentinel_pos]
        assert "MegaCorp" in pre_body, "Company must appear before the AI body"

    def test_job_title_appears_before_ai_body(self):
        """Job title must be present in the template area before the AI body."""
        ai_body = "SENTINEL_BODY_START"
        tex = _assemble(ai_body, _listing(title="Platform Engineer"), _info())
        sentinel_pos = tex.index(ai_body)
        pre_body = tex[:sentinel_pos]
        assert "Platform Engineer" in pre_body, "Job title must appear before AI body"

    def test_opening_sentence_contains_both_company_and_title(self):
        """The programmatic opening sentence must reference both company and job title."""
        ai_body = "SENTINEL_BODY_START"
        tex = _assemble(ai_body, _listing(company="Acme", title="SRE"), _info())
        sentinel_pos = tex.index(ai_body)
        pre_body = tex[:sentinel_pos]
        assert "Acme" in pre_body
        assert "SRE" in pre_body

    def test_different_company_name_reflected(self):
        """A different company name is always reflected in the opening."""
        sentinel = "XUNIQUE_SENTINEL_BODY_CONTENT_X"
        for company in ["Google", "Amazon", "Startup XYZ"]:
            tex = _assemble(sentinel, _listing(company=company), _info())
            opening_area = tex[:tex.index(sentinel)]
            assert company in opening_area, f"{company} must appear before body"


# ---------------------------------------------------------------------------
# AI prompt does not tell AI to write the opening
# ---------------------------------------------------------------------------

class TestPromptDoesNotDelegateOpening:
    def test_system_prompt_instructs_skip_opening(self):
        """System prompt must tell AI the opening is already provided."""
        # The AI must NOT be asked to write "State the role" or
        # "mention the company" as the first paragraph task -- that is
        # now handled programmatically.
        lower = CL_SYSTEM_PROMPT.lower()
        # Prompt must acknowledge the opening is pre-filled
        assert any(
            phrase in lower
            for phrase in ["opening is provided", "opening sentence", "already provided", "do not write"]
        ), "System prompt must instruct AI to skip the opening sentence"

    def test_prompt_does_not_ask_for_role_and_company_in_para1(self):
        """AI should not be told to state the role+company in paragraph 1."""
        # Old rule 2 said 'State the role, where you found it' -- this is
        # now programmatic; the prompt should NOT contain this instruction.
        assert "State the role" not in CL_SYSTEM_PROMPT
