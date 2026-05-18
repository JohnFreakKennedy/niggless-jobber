"""Tests for applicator/captcha.py detection logic."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from applicator.captcha import CaptchaType, _is_invisible_recaptcha, detect_captcha

# Exact selector strings used by detect_captcha and _is_invisible_recaptcha
_SEL_HCAPTCHA = "iframe[src*='hcaptcha.com']"
_SEL_TURNSTILE = ".cf-turnstile, iframe[src*='challenges.cloudflare.com']"
_SEL_RECAPTCHA_V2 = "div.g-recaptcha:not([data-size='invisible']), iframe[src*='recaptcha/api2/anchor']"
_SEL_RECAPTCHA_V3 = "iframe[src*='recaptcha'], div.g-recaptcha[data-size='invisible'], script[src*='recaptcha']"
_SEL_IMAGE = "img.captcha-image, img[src*='captcha']"
_SEL_INVISIBLE_DIV = "div.g-recaptcha[data-size='invisible']"
_SEL_ANCHOR = "iframe[src*='recaptcha/api2/anchor']"
_SEL_BFRAME = "iframe[src*='recaptcha/api2/bframe']"


def _mock_page(*matching_selectors: str) -> AsyncMock:
    """
    Build a mock Page that returns a truthy element only when query_selector
    is called with one of the exact selector strings listed in
    *matching_selectors*.
    """
    page = AsyncMock()

    async def _query_selector(selector: str):
        return MagicMock() if selector in matching_selectors else None

    page.query_selector = _query_selector
    return page


# ---------------------------------------------------------------------------
# detect_captcha
# ---------------------------------------------------------------------------

class TestDetectCaptcha:
    async def test_no_captcha(self):
        page = _mock_page()
        result = await detect_captcha(page)
        assert result is None

    async def test_hcaptcha_detected(self):
        page = _mock_page(_SEL_HCAPTCHA)
        result = await detect_captcha(page)
        assert result == CaptchaType.HCAPTCHA

    async def test_hcaptcha_takes_priority_over_recaptcha(self):
        page = _mock_page(_SEL_HCAPTCHA, _SEL_RECAPTCHA_V2)
        result = await detect_captcha(page)
        assert result == CaptchaType.HCAPTCHA

    async def test_recaptcha_v2_visible(self):
        page = _mock_page(_SEL_RECAPTCHA_V2)
        result = await detect_captcha(page)
        assert result == CaptchaType.RECAPTCHA_V2

    async def test_recaptcha_v3_invisible(self):
        # Only the v3/invisible selector matches (not the v2 anchor selector)
        page = _mock_page(_SEL_RECAPTCHA_V3)
        result = await detect_captcha(page)
        assert result == CaptchaType.RECAPTCHA_V3

    async def test_turnstile_cf_iframe(self):
        page = _mock_page(_SEL_TURNSTILE)
        result = await detect_captcha(page)
        assert result == CaptchaType.TURNSTILE

    async def test_image_captcha(self):
        page = _mock_page(_SEL_IMAGE)
        result = await detect_captcha(page)
        assert result == CaptchaType.IMAGE


# ---------------------------------------------------------------------------
# _is_invisible_recaptcha
# ---------------------------------------------------------------------------

class TestIsInvisibleRecaptcha:
    async def test_invisible_div_attribute(self):
        page = _mock_page(_SEL_INVISIBLE_DIV)
        result = await _is_invisible_recaptcha(page)
        assert result is True

    async def test_anchor_iframe_means_visible(self):
        # div invisible not present, anchor iframe present -> visible widget
        page = _mock_page(_SEL_ANCHOR)
        result = await _is_invisible_recaptcha(page)
        assert result is False

    async def test_bframe_only_means_invisible(self):
        # No invisible div, no anchor, but bframe present -> invisible
        page = _mock_page(_SEL_BFRAME)
        result = await _is_invisible_recaptcha(page)
        assert result is True

    async def test_no_elements_not_invisible(self):
        page = _mock_page()
        result = await _is_invisible_recaptcha(page)
        assert result is False
