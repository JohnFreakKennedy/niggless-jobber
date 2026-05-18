"""Greenhouse ATS adapter."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from applicator.ats.base import ApplicationResult, BaseATSAdapter, PersonalInfo
from applicator.captcha import detect_captcha, solve_captcha
from applicator.form_filler import fill_form
from parser.schema import JobListing

log = logging.getLogger(__name__)


class GreenhouseAdapter(BaseATSAdapter):
    name = "greenhouse"

    async def apply(
        self,
        page: "Page",
        listing: JobListing,
        info: PersonalInfo,
        cv_pdf: Path | None,
        cover_letter_pdf: Path | None,
    ) -> ApplicationResult:
        try:
            await page.goto(listing.apply_url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1.5)

            # Fill standard fields
            await self._fill_standard(page, info)

            # Upload CV
            if cv_pdf and cv_pdf.exists():
                uploaded = await self._upload_file(page, "input[type='file'][id*='resume'], input[type='file'][name*='resume']", cv_pdf)
                if not uploaded:
                    await self._upload_file(page, "input[type='file']", cv_pdf)

            # Upload cover letter if a file input is present
            if cover_letter_pdf and cover_letter_pdf.exists():
                await self._upload_file(page, "input[type='file'][id*='cover'], input[type='file'][name*='cover']", cover_letter_pdf)

            # Fill custom questions using heuristic filler
            await fill_form(page, listing_description=listing.description)

            # CAPTCHA
            captcha_type = await detect_captcha(page)
            if captcha_type:
                await solve_captcha(page, captcha_type)
                await asyncio.sleep(1)

            # Submit
            submitted = await self._click(page, "input[type='submit'], button[type='submit']")
            if not submitted:
                return ApplicationResult.failure(listing.id, "Could not find submit button")

            await page.wait_for_load_state("networkidle", timeout=15_000)

            # Verify
            content = await page.content()
            if any(k in content.lower() for k in ("application submitted", "thank you", "we received", "confirmation")):
                return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)

            return ApplicationResult.failure(listing.id, "Submission not confirmed")

        except Exception as exc:
            log.exception("Greenhouse apply error for %s", listing.company)
            return ApplicationResult.failure(listing.id, str(exc))

    async def _fill_standard(self, page: "Page", info: PersonalInfo) -> None:
        fields = {
            "input#first_name, input[name*='first_name']": info.get("name", "first"),
            "input#last_name, input[name*='last_name']": info.get("name", "last"),
            "input#email, input[type='email']": info.get("email"),
            "input#phone, input[type='tel']": info.get("phone"),
            "input#linkedin_profile, input[name*='linkedin']": info.get("linkedin_url"),
            "input#website, input[name*='website']": info.get("website_url"),
        }
        for selector, value in fields.items():
            if not value:
                continue
            el = await page.query_selector(selector)
            if el:
                await el.fill(value)
                await asyncio.sleep(0.2)
