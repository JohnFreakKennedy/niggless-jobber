"""Application engine: ATS detection, Playwright context, orchestration."""
from __future__ import annotations

import asyncio
import json
import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from playwright.async_api import Browser

from applicator.ats.base import ApplicationResult, ApplicationStatus, PersonalInfo
from applicator.ats.greenhouse import GreenhouseAdapter
from applicator.ats.icims import ICIMSAdapter
from applicator.ats.lever import LeverAdapter
from applicator.ats.linkedin_easy_apply import LinkedInEasyApplyAdapter
from applicator.ats.workday import WorkdayAdapter
from applicator.captcha import detect_captcha, solve_captcha
from applicator.mfa import resolve_mfa
from parser.schema import JobListing
from scrapers.base import USER_AGENTS

log = logging.getLogger(__name__)

_PERSONAL_INFO = Path.home() / ".niggless-jobber" / "personal_info.json"

_ATS_PATTERNS: list[tuple[str, type]] = [
    ("greenhouse.io", GreenhouseAdapter),
    ("boards.greenhouse.io", GreenhouseAdapter),
    ("jobs.lever.co", LeverAdapter),
    ("myworkdayjobs.com", WorkdayAdapter),
    (".icims.com", ICIMSAdapter),
    ("icims.com", ICIMSAdapter),
]


def _detect_adapter(apply_url: str):
    host = urlparse(apply_url).netloc.lower()
    for pattern, adapter_cls in _ATS_PATTERNS:
        if pattern in host:
            return adapter_cls()
    return None


def _is_linkedin_easy_apply(listing: JobListing) -> bool:
    return (
        listing.source == "linkedin"
        and "linkedin.com/jobs/view" in listing.apply_url
        and "companyApplyUrl" not in listing.apply_url
    )


def _load_personal_info() -> PersonalInfo:
    if _PERSONAL_INFO.exists():
        return PersonalInfo(raw=json.loads(_PERSONAL_INFO.read_text()))
    return PersonalInfo(raw={})


async def apply(
    listing: JobListing,
    cv_pdf: Path | None,
    cover_letter_pdf: Path | None,
    browser: "Browser",
    dry_run: bool = False,
    prompt_version_cv: str = "",
    prompt_version_cl: str = "",
) -> ApplicationResult:
    """
    Main entry point: opens a fresh browser context and applies for *listing*.
    """
    if dry_run:
        log.info("[dry-run] Would apply to %s at %s", listing.title, listing.company)
        return ApplicationResult(
            job_id=listing.id,
            status=ApplicationStatus.DRY_RUN,
            cv_path=cv_pdf,
            cover_letter_path=cover_letter_pdf,
            prompt_version_cv=prompt_version_cv,
            prompt_version_cl=prompt_version_cl,
        )

    info = _load_personal_info()

    ctx = await browser.new_context(
        viewport={"width": random.randint(1280, 1920), "height": random.randint(720, 1080)},
        user_agent=random.choice(USER_AGENTS),
        locale="en-US",
        accept_downloads=True,
    )
    try:
        from playwright_stealth import stealth_async
        await stealth_async(ctx)
    except ImportError:
        pass

    page = await ctx.new_page()

    try:
        result = await _do_apply(page, listing, info, cv_pdf, cover_letter_pdf)
        result.prompt_version_cv = prompt_version_cv
        result.prompt_version_cl = prompt_version_cl

        if result.status == ApplicationStatus.APPLIED:
            screenshot_path = (cv_pdf.parent if cv_pdf else Path("/tmp")) / "confirmation.png"
            try:
                await page.screenshot(path=str(screenshot_path), full_page=False)
            except Exception:
                pass

    except Exception as exc:
        log.exception("Unexpected error applying to %s", listing.company)
        result = ApplicationResult.failure(listing.id, str(exc))
    finally:
        await ctx.close()

    return result


async def _do_apply(
    page,
    listing: JobListing,
    info: PersonalInfo,
    cv_pdf: Path | None,
    cover_letter_pdf: Path | None,
) -> ApplicationResult:
    hostname = urlparse(listing.apply_url).netloc

    # Select adapter
    if _is_linkedin_easy_apply(listing):
        adapter = LinkedInEasyApplyAdapter()
    else:
        adapter = _detect_adapter(listing.apply_url)

    if adapter:
        log.info("Applying via %s adapter to %s at %s", adapter.name, listing.title, listing.company)
        result = await adapter.apply(page, listing, info, cv_pdf, cover_letter_pdf)
    else:
        log.info("Applying via heuristic form filler to %s at %s", listing.title, listing.company)
        result = await _heuristic_apply(page, listing, info, cv_pdf, cover_letter_pdf)

    return result


async def _dismiss_cookie_banners(page) -> None:
    """Click the most common cookie / consent accept buttons."""
    selectors = [
        # cookiescript (what we saw in the logs)
        "#cookiescript_accept",
        "#cookiescript_injected button",
        # generic patterns
        "button:has-text('Accept all')",
        "button:has-text('Accept All')",
        "button:has-text('Accept cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I accept')",
        "button:has-text('Agree')",
        "button:has-text('OK')",
        "button:has-text('Got it')",
        "[id*='cookie'] button",
        "[class*='cookie'] button[class*='accept']",
        "[class*='consent'] button[class*='accept']",
        "[aria-label*='Accept']",
    ]
    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el and await el.is_visible():
                await el.click(timeout=3_000)
                await asyncio.sleep(0.5)
                log.debug("Dismissed cookie banner via: %s", sel)
                break
        except Exception:
            pass


async def _heuristic_apply(
    page,
    listing: JobListing,
    info: PersonalInfo,
    cv_pdf: Path | None,
    cover_letter_pdf: Path | None,
) -> ApplicationResult:
    from applicator.form_filler import fill_form
    import re

    try:
        await page.goto(listing.apply_url, wait_until="domcontentloaded", timeout=30_000)
        await asyncio.sleep(2)

        # Handle MFA / CAPTCHA before filling
        hostname = urlparse(listing.apply_url).netloc
        captcha_type = await detect_captcha(page)
        if captcha_type:
            await solve_captcha(page, captcha_type)

        await resolve_mfa(page, hostname)

        # Upload CV
        if cv_pdf and cv_pdf.exists():
            file_inp = await page.query_selector("input[type='file']")
            if file_inp:
                await file_inp.set_input_files(str(cv_pdf))
                await asyncio.sleep(1)

        cl_text = ""
        if cover_letter_pdf:
            cl_tex = cover_letter_pdf.with_suffix(".tex")
            if cl_tex.exists():
                raw = cl_tex.read_text(encoding="utf-8")
                cl_text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", raw)
                cl_text = re.sub(r"\\[a-zA-Z]+", " ", cl_text).strip()

        unmapped = await fill_form(
            page,
            cover_letter_text=cl_text,
            listing_description=listing.description,
        )
        if unmapped:
            log.debug("Unmapped fields: %s", unmapped)

        # Dismiss cookie / consent banners before submitting
        await _dismiss_cookie_banners(page)

        # Submit
        submitted = False
        for sel in ("button[type='submit']", "input[type='submit']", "button:has-text('Apply')", "button:has-text('Submit')"):
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.click(timeout=10_000)
                except Exception:
                    # Banner may still be present; try JS click as fallback
                    await page.evaluate("el => el.click()", el)
                submitted = True
                break

        if not submitted:
            return ApplicationResult(
                job_id=listing.id,
                status=ApplicationStatus.MANUAL_REVIEW,
                reason="Could not find submit button on unknown form",
            )

        await page.wait_for_load_state("networkidle", timeout=15_000)
        content = await page.content()
        if any(k in content.lower() for k in ("thank you", "submitted", "application received", "we received")):
            return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)

        return ApplicationResult.failure(listing.id, "Submission not confirmed on heuristic form")

    except Exception as exc:
        log.exception("Heuristic apply error")
        return ApplicationResult.failure(listing.id, str(exc))
