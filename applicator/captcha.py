"""CAPTCHA detection and solver integration."""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

log = logging.getLogger(__name__)


class CaptchaType(str, Enum):
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    TURNSTILE = "turnstile"
    IMAGE = "image"


async def detect_captcha(page: "Page") -> CaptchaType | None:
    # hCaptcha
    if await page.query_selector("iframe[src*='hcaptcha.com']"):
        return CaptchaType.HCAPTCHA
    # Cloudflare Turnstile
    if await page.query_selector(".cf-turnstile, iframe[src*='challenges.cloudflare.com']"):
        return CaptchaType.TURNSTILE
    # reCAPTCHA v2 visible (has a checkbox)
    if await page.query_selector("div.g-recaptcha:not([data-size='invisible']), iframe[src*='recaptcha/api2/anchor']"):
        return CaptchaType.RECAPTCHA_V2
    # reCAPTCHA v2 invisible or v3 (no visible widget)
    if await page.query_selector("iframe[src*='recaptcha'], div.g-recaptcha[data-size='invisible'], script[src*='recaptcha']"):
        return CaptchaType.RECAPTCHA_V3
    # Image captcha
    if await page.query_selector("img.captcha-image, img[src*='captcha']"):
        return CaptchaType.IMAGE
    return None


async def solve_captcha(page: "Page", captcha_type: CaptchaType | None = None) -> None:
    """Detect (if not provided) and solve the CAPTCHA on the current page."""
    if captcha_type is None:
        captcha_type = await detect_captcha(page)
    if captcha_type is None:
        return

    from storage.vault import get_secret

    # Auto-detect: use whichever solver key is present in the vault.
    # If both are set, prefer the one configured in config.yaml; otherwise
    # fall back to whichever is available.
    has_capsolver = bool(get_secret("CAPSOLVER_API_KEY"))
    has_2captcha = bool(get_secret("2CAPTCHA_API_KEY"))

    provider = "none"
    if has_capsolver and has_2captcha:
        # Both present - read config preference
        try:
            import yaml, os
            config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
            with open(config_path) as f:
                _cfg = yaml.safe_load(f)
            provider = _cfg.get("captcha", {}).get("preferred", "capsolver")
        except Exception:
            provider = "capsolver"
    elif has_capsolver:
        provider = "capsolver"
    elif has_2captcha:
        provider = "2captcha"
    else:
        log.error("No CAPTCHA solver key in vault. Set CAPSOLVER_API_KEY or 2CAPTCHA_API_KEY.")
        return

    log.info("Solving %s CAPTCHA via %s", captcha_type.value, provider)

    if provider == "2captcha":
        token = await _solve_2captcha(page, captcha_type)
    else:
        token = await _solve_capsolver(page, captcha_type)

    if token:
        await _inject_token(page, captcha_type, token)


async def _get_site_key(page: "Page", captcha_type: CaptchaType) -> str:
    if captcha_type in (CaptchaType.RECAPTCHA_V2, CaptchaType.RECAPTCHA_V3):
        el = await page.query_selector("div.g-recaptcha, [data-sitekey]")
        if el:
            return await el.get_attribute("data-sitekey") or ""
        # Try extracting from iframe src
        iframe = await page.query_selector("iframe[src*='recaptcha']")
        if iframe:
            src = await iframe.get_attribute("src") or ""
            import re
            m = re.search(r"k=([A-Za-z0-9_-]+)", src)
            return m.group(1) if m else ""
    if captcha_type == CaptchaType.HCAPTCHA:
        el = await page.query_selector("[data-sitekey], .h-captcha")
        if el:
            return await el.get_attribute("data-sitekey") or ""
    return ""


async def _solve_2captcha(page: "Page", captcha_type: CaptchaType) -> str | None:
    from storage.vault import get_secret
    api_key = get_secret("2CAPTCHA_API_KEY")
    if not api_key:
        log.error("2CAPTCHA_API_KEY not in vault")
        return None
    try:
        from twocaptcha import TwoCaptcha
        solver = TwoCaptcha(api_key)
        site_key = await _get_site_key(page, captcha_type)
        page_url = page.url
        if captcha_type == CaptchaType.RECAPTCHA_V2:
            result = solver.recaptcha(sitekey=site_key, url=page_url)
        elif captcha_type == CaptchaType.HCAPTCHA:
            result = solver.hcaptcha(sitekey=site_key, url=page_url)
        else:
            log.warning("2captcha: unsupported type %s", captcha_type)
            return None
        return result.get("code")
    except Exception as exc:
        log.error("2captcha solve failed: %s", exc)
        return None


async def _is_invisible_recaptcha(page: "Page") -> bool:
    """Return True when the reCAPTCHA on the page is invisible/v3 mode."""
    # Check the div for data-size="invisible"
    el = await page.query_selector("div.g-recaptcha[data-size='invisible']")
    if el:
        return True
    # Check the anchor iframe URL - invisible widgets use /bframe, not /anchor
    anchor = await page.query_selector("iframe[src*='recaptcha/api2/anchor']")
    if anchor:
        return False  # visible checkbox
    # Fall back: if only bframe or no anchor iframe exists, treat as invisible
    bframe = await page.query_selector("iframe[src*='recaptcha/api2/bframe']")
    return bool(bframe)


async def _solve_capsolver(page: "Page", captcha_type: CaptchaType) -> str | None:
    from storage.vault import get_secret
    api_key = get_secret("CAPSOLVER_API_KEY")
    if not api_key:
        log.error("CAPSOLVER_API_KEY not in vault")
        return None
    try:
        import capsolver
        capsolver.api_key = api_key
        site_key = await _get_site_key(page, captcha_type)
        page_url = page.url

        if captcha_type == CaptchaType.RECAPTCHA_V2:
            # Check whether the widget is actually configured as invisible
            is_invisible = await _is_invisible_recaptcha(page)
            if is_invisible:
                log.debug("reCAPTCHA v2 detected as invisible mode; using v3 task type")
                task = {
                    "type": "ReCaptchaV3TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key or "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI",
                    "pageAction": "submit",
                    "minScore": 0.5,
                }
            else:
                task = {
                    "type": "ReCaptchaV2TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key,
                }
        elif captcha_type == CaptchaType.RECAPTCHA_V3:
            task = {
                "type": "ReCaptchaV3TaskProxyless",
                "websiteURL": page_url,
                "websiteKey": site_key or "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI",
                "pageAction": "submit",
                "minScore": 0.5,
            }
        elif captcha_type == CaptchaType.HCAPTCHA:
            task = {"type": "HCaptchaTaskProxyless", "websiteURL": page_url, "websiteKey": site_key}
        elif captcha_type == CaptchaType.TURNSTILE:
            task = {"type": "AntiTurnstileTaskProxyLess", "websiteURL": page_url, "websiteKey": site_key}
        else:
            log.warning("capsolver: unsupported type %s", captcha_type)
            return None

        try:
            solution = capsolver.solve(task)
        except Exception as first_exc:
            err_str = str(first_exc).lower()
            # If CapSolver reports the widget is invisible, retry with v3 task
            if "invisible" in err_str and captcha_type == CaptchaType.RECAPTCHA_V2:
                log.info("CapSolver invisible error on v2; retrying as v3")
                task = {
                    "type": "ReCaptchaV3TaskProxyless",
                    "websiteURL": page_url,
                    "websiteKey": site_key or "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI",
                    "pageAction": "submit",
                    "minScore": 0.5,
                }
                solution = capsolver.solve(task)
            else:
                raise

        return (
            solution.get("solution", {}).get("gRecaptchaResponse")
            or solution.get("solution", {}).get("token")
        )
    except Exception as exc:
        log.error("capsolver solve failed: %s", exc)
        return None


async def _inject_token(page: "Page", captcha_type: CaptchaType, token: str) -> None:
    try:
        await page.evaluate(
            """(token) => {
                const el = document.getElementById('g-recaptcha-response');
                if (el) { el.innerHTML = token; }
                const el2 = document.querySelector('textarea[name="h-captcha-response"]');
                if (el2) { el2.innerHTML = token; }
                // Trigger callbacks
                if (typeof ___grecaptcha_cfg !== 'undefined') {
                    try {
                        const clients = Object.values(___grecaptcha_cfg.clients || {});
                        clients.forEach(c => {
                            const cb = c?.aa?.l?.callback || c?.l?.callback;
                            if (typeof cb === 'function') cb(token);
                        });
                    } catch(e) {}
                }
                if (typeof hcaptcha !== 'undefined') {
                    try { hcaptcha.setResponse(token); } catch(e) {}
                }
            }""",
            token,
        )
        log.info("CAPTCHA token injected successfully")
    except Exception as exc:
        log.warning("Token injection failed: %s", exc)
