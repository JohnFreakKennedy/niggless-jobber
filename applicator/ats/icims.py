"""iCIMS ATS adapter."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from applicator.ats.base import ApplicationResult, BaseATSAdapter, PersonalInfo
from applicator.form_filler import fill_form
from parser.schema import JobListing

log = logging.getLogger(__name__)


class ICIMSAdapter(BaseATSAdapter):
    name = "icims"

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
            await asyncio.sleep(2)

            # iCIMS may show a login/register gate
            if await page.query_selector("input[name='email'], input[type='email']"):
                email = info.get("email")
                pw_el = await page.query_selector("input[type='password']")
                if pw_el and email:
                    await page.fill("input[type='email']", email)
                    from storage.vault import get_secret
                    icims_pass = get_secret("ICIMS_PASSWORD") or ""
                    if icims_pass:
                        await pw_el.fill(icims_pass)
                        await self._click(page, "button[type='submit'], input[type='submit']")
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                        await asyncio.sleep(1)

            max_pages = 8
            for page_num in range(max_pages):
                log.debug("iCIMS page %d for %s", page_num + 1, listing.company)

                # File upload
                if cv_pdf and cv_pdf.exists():
                    file_inp = await page.query_selector("input[type='file']")
                    if file_inp:
                        await file_inp.set_input_files(str(cv_pdf))
                        await asyncio.sleep(1)

                await fill_form(page, listing_description=listing.description)

                # Look for final submit
                for sel in ("input[type='submit'][value*='Submit']", "button:has-text('Submit Application')", "a:has-text('Submit')"):
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                        content = await page.content()
                        if any(k in content.lower() for k in ("thank you", "submitted", "received")):
                            return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)
                        return ApplicationResult.failure(listing.id, "iCIMS submit not confirmed")

                # Next
                clicked = await self._click(page, "input[type='submit'][value='Next'], button:has-text('Next'), a.iCIMS_PrimaryButton:has-text('Next')")
                if not clicked:
                    return ApplicationResult.failure(listing.id, "Could not advance iCIMS form")
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
                await asyncio.sleep(1.5)

            return ApplicationResult.failure(listing.id, "iCIMS: max pages reached")

        except Exception as exc:
            log.exception("iCIMS apply error for %s", listing.company)
            return ApplicationResult.failure(listing.id, str(exc))
