"""Tests for utils.looks_like_location (company field vs location string)."""
from __future__ import annotations

import pytest

from utils import looks_like_location


@pytest.mark.parametrize("value", [
    "Poland (Remote)", "Warsaw, Poland", "On-site, Germany", "Remote", "Hybrid",
    "Germany", "Kraków, Poland (Hybrid)", "Gdynia, Poland", "Remote / EU",
    "Fully remote", "Anytown (On-site)", "United Kingdom", "Remote - Europe",
])
def test_locations_detected(value):
    assert looks_like_location(value)


@pytest.mark.parametrize("value", [
    "Berlin Brands Group", "London Stock Exchange Group", "Allegro Poland",
    "Acme Corp", "Google", "", "   ", "EPAM Systems", "Remote Technology Inc",
    "Sample Co Poland", "Spain Holdings Ltd",
])
def test_companies_not_flagged(value):
    assert not looks_like_location(value)


@pytest.mark.parametrize("value", ["Berlin-Chemie", "Paris-Saclay", "EU-Startups"])
def test_hyphenated_company_names_not_flagged(value):
    assert not looks_like_location(value)
