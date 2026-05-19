"""Tests for cover_letter template and assembly."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from cover_letter.generator import _assemble


def _listing(**kw):
    defaults = dict(
        id="abc123",
        source="linkedin",
        title="DevOps Engineer",
        company="Globex",
        description="",
        skills=[],
        location="Remote",
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
# Single page / no blank first page
# ---------------------------------------------------------------------------

class TestCoverLetterTemplate:
    def test_uses_article_not_letter_class(self):
        tex = _assemble("Body text here.", _listing(), _info())
        assert r"\documentclass" in tex
        # Must NOT use the 'letter' document class (causes blank first page)
        assert "{letter}" not in tex

    def test_exactly_one_begin_document(self):
        tex = _assemble("Body.", _listing(), _info())
        assert tex.count(r"\begin{document}") == 1

    def test_exactly_one_end_document(self):
        tex = _assemble("Body.", _listing(), _info())
        assert tex.count(r"\end{document}") == 1

    def test_no_begin_letter_command(self):
        tex = _assemble("Body.", _listing(), _info())
        assert r"\begin{letter}" not in tex

    def test_pagestyle_empty_suppresses_page_number(self):
        tex = _assemble("Body.", _listing(), _info())
        assert r"\pagestyle{empty}" in tex


# ---------------------------------------------------------------------------
# Placeholder substitution
# ---------------------------------------------------------------------------

class TestCoverLetterAssembly:
    def test_name_substituted(self):
        tex = _assemble("Body.", _listing(), _info())
        assert "Artem Dankov" in tex

    def test_email_substituted(self):
        tex = _assemble("Body.", _listing(), _info())
        assert "test@example.com" in tex

    def test_phone_substituted(self):
        tex = _assemble("Body.", _listing(), _info())
        assert "+48123456789" in tex

    def test_company_substituted(self):
        tex = _assemble("Body.", _listing(company="Google"), _info())
        assert "Google" in tex

    def test_job_title_substituted(self):
        tex = _assemble("Body.", _listing(title="Platform Engineer"), _info())
        assert "Platform Engineer" in tex

    def test_body_substituted(self):
        tex = _assemble("Dear Hiring Team, I am excited to apply.", _listing(), _info())
        assert "I am excited to apply." in tex

    def test_no_unreplaced_placeholders(self):
        tex = _assemble("Body.", _listing(), _info())
        assert "%%" not in tex

    def test_address_city_and_country(self):
        tex = _assemble("Body.", _listing(), _info())
        assert "Wroclaw" in tex
        assert "Poland" in tex
