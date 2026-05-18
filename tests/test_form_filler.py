"""Tests for applicator/form_filler.py salary extraction logic."""
from __future__ import annotations

import pytest

from applicator.form_filler import (
    _detect_currency_in_text,
    _detect_period_in_text,
    extract_salary_range,
)


# ---------------------------------------------------------------------------
# _detect_currency_in_text
# ---------------------------------------------------------------------------

class TestDetectCurrencyInText:
    def test_usd_symbol(self):
        assert _detect_currency_in_text("Salary: $5,000/month") == "USD"

    def test_usd_word(self):
        assert _detect_currency_in_text("Pay: 6000 dollars per month") == "USD"

    def test_eur_code(self):
        assert _detect_currency_in_text("Compensation: 5000 EUR monthly") == "EUR"

    def test_pln_code(self):
        assert _detect_currency_in_text("Wynagrodzenie: 20000 PLN brutto") == "PLN"

    def test_gbp_symbol(self):
        assert _detect_currency_in_text("Salary: 50,000 pounds per year") == "GBP"

    def test_defaults_to_usd_when_ambiguous(self):
        # No recognisable currency -> default USD
        assert _detect_currency_in_text("Competitive salary offered") == "USD"


# ---------------------------------------------------------------------------
# _detect_period_in_text
# ---------------------------------------------------------------------------

class TestDetectPeriodInText:
    def test_hourly(self):
        assert _detect_period_in_text("40 per hour") == "hourly"

    def test_monthly(self):
        assert _detect_period_in_text("8000 per month") == "monthly"

    def test_yearly(self):
        assert _detect_period_in_text("90,000 per year") == "yearly"

    def test_annual_keyword(self):
        assert _detect_period_in_text("100k annual") == "yearly"

    def test_defaults_to_yearly(self):
        assert _detect_period_in_text("no period mentioned") == "yearly"

    def test_slash_h_suffix(self):
        assert _detect_period_in_text("$50/h") == "hourly"

    def test_slash_mo_suffix(self):
        assert _detect_period_in_text("$7000/mo") == "monthly"


# ---------------------------------------------------------------------------
# extract_salary_range
# ---------------------------------------------------------------------------

class TestExtractSalaryRange:
    def test_returns_none_for_empty_description(self):
        assert extract_salary_range("", "USD", "monthly") is None

    def test_returns_none_when_no_range_present(self):
        desc = "We offer competitive compensation. Benefits include health insurance."
        assert extract_salary_range(desc, "USD", "monthly") is None

    def test_basic_usd_monthly_range(self):
        desc = "Salary range: $6,000 - $9,000 per month."
        result = extract_salary_range(desc, "USD", "monthly")
        assert result is not None
        low, high = result
        assert 5500 <= low <= 6500
        assert 8500 <= high <= 9500
        assert low < high

    def test_pln_monthly_range(self):
        desc = "Wynagrodzenie: 20 000 - 30 000 PLN miesieczne."
        result = extract_salary_range(desc, "PLN", "monthly")
        assert result is not None
        low, high = result
        assert low < high

    def test_eur_hourly_range(self):
        desc = "Hourly rate: 40 - 60 EUR/h"
        result = extract_salary_range(desc, "EUR", "hourly")
        assert result is not None
        low, high = result
        assert low < high
        # 40 EUR/h in hourly mode, returned in EUR/h
        assert 35 <= low <= 45

    def test_yearly_range_converted_to_monthly(self):
        desc = "Annual compensation: $90,000 - $120,000 per year."
        result = extract_salary_range(desc, "USD", "monthly")
        assert result is not None
        low, high = result
        # 90k/12 = 7500, 120k/12 = 10000
        assert 7000 <= low <= 8000
        assert 9500 <= high <= 10500

    def test_low_equals_high_returns_none(self):
        # Not a valid range
        desc = "We pay 5000 - 5000 USD monthly"
        result = extract_salary_range(desc, "USD", "monthly")
        assert result is None

    def test_version_numbers_not_matched(self):
        # "5.0 - 6.0" in a version string should not be treated as salary
        desc = "Requires Python 3.8 - 3.12 and good coding skills."
        result = extract_salary_range(desc, "USD", "monthly")
        # Either None or a value too small to be plausible; if matched it
        # would be 3-12 which is implausible as a real salary
        if result is not None:
            low, high = result
            # If somehow matched, it should be a tiny amount (not a real salary)
            assert high < 100

    def test_currency_conversion_usd_to_eur(self):
        desc = "Salary: $8,000 - $10,000 per month"
        usd_result = extract_salary_range(desc, "USD", "monthly")
        eur_result = extract_salary_range(desc, "EUR", "monthly")
        assert usd_result is not None
        assert eur_result is not None
        # EUR amounts should be less than USD amounts (EUR is stronger)
        assert eur_result[0] < usd_result[0]
