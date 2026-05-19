"""Tests for scrapers/justjoinit.py _parse_card_text."""
from __future__ import annotations

import pytest

from scrapers.justjoinit import _parse_card_text


# ---------------------------------------------------------------------------
# Helper: build card text in the format JustJoinIT produces
# ---------------------------------------------------------------------------

def _card(
    title: str,
    salary: str = "Undisclosed Salary",
    company: str = "",
    city: str = "Warsaw",
    remote: bool = False,
    skills: list[str] | None = None,
    badges: list[str] | None = None,
) -> str:
    """Assemble a realistic JustJoinIT card innerText."""
    parts: list[str] = []
    if badges:
        parts.extend(badges)
    parts.append(title)
    parts.append(salary)
    # Blank line separates header from company block (when present)
    if company:
        parts.append("")
        parts.append(company)
    # Blank line separates company from location block
    parts.append("")
    parts.append(city)
    if remote:
        parts.append("Remote")
    parts.append("3d left")
    if skills:
        parts.extend(skills)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Basic extraction
# ---------------------------------------------------------------------------

class TestParseCardTextCompany:
    def test_extracts_company_name(self):
        text = _card("DevOps Engineer", company="Acme Corp", city="Warsaw")
        result = _parse_card_text(text)
        assert result["company"] == "Acme Corp"
        assert result["title"] == "DevOps Engineer"
        assert result["location"] == "Warsaw"

    def test_extracts_salary(self):
        text = _card("SRE", salary="20 000 - 30 000 PLN", company="Globex", city="Krakow")
        result = _parse_card_text(text)
        assert "20 000" in result["salary_raw"] or result["salary_raw"] == "20 000 - 30 000 PLN"

    def test_extracts_skills(self):
        text = _card("DevOps Engineer", company="Foo", city="Warsaw", skills=["kubernetes", "docker"])
        result = _parse_card_text(text)
        assert "kubernetes" in result["skills"]
        assert "docker" in result["skills"]

    def test_badge_stripped(self):
        text = _card("Platform Engineer", company="Bar", city="Gdansk", badges=["1-Click Apply"])
        result = _parse_card_text(text)
        assert result["title"] == "Platform Engineer"
        assert "1-Click Apply" not in result["company"]


# ---------------------------------------------------------------------------
# Anonymous company (city must NOT end up as company)
# ---------------------------------------------------------------------------

class TestParseCardTextAnonymous:
    def test_city_not_captured_as_company(self):
        """When the company section is absent, company must be empty, not the city."""
        text = _card("DevOps Engineer", salary="Undisclosed Salary", company="", city="Copenhagen")
        result = _parse_card_text(text)
        # City must NOT appear as company
        assert result["company"] != "Copenhagen"
        assert result["location"] == "Copenhagen"

    def test_gdansk_not_captured_as_company(self):
        text = _card("Cloud Engineer", company="", city="Gdansk")
        result = _parse_card_text(text)
        assert result["company"] != "Gdansk"

    def test_krakow_not_captured_as_company(self):
        text = _card("SRE", company="", city="Krakow")
        result = _parse_card_text(text)
        assert result["company"] != "Krakow"


# ---------------------------------------------------------------------------
# Remote jobs
# ---------------------------------------------------------------------------

class TestParseCardTextRemote:
    def test_remote_flag_set(self):
        text = _card("DevOps Engineer", company="Acme", city="Warsaw", remote=True)
        result = _parse_card_text(text)
        assert result["is_remote"] is True
        assert result["location"] == "Remote"

    def test_remote_only_no_city(self):
        """Remote-only listings may have no city."""
        text = "\n".join([
            "DevOps Engineer",
            "15 000 - 25 000 PLN",
            "",
            "Remote Corp",
            "",
            "Remote",
            "5d left",
            "aws",
        ])
        result = _parse_card_text(text)
        assert result["is_remote"] is True
        assert result["company"] == "Remote Corp"
