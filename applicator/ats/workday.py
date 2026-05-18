"""Workday ATS adapter (multi-step wizard)."""
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

_NEXT_SELECTORS = [
    "button[data-automation-id='bottom-navigation-next-button']",
    "button[aria-label='Next']",
    "button:has-text('Next')",
    "button:has-text('Continue')",
    "button[type='submit']:not([disabled])",
]

_SUBMIT_SELECTORS = [
    "button[data-automation-id='bottom-navigation-next-button'][aria-label*='Submit']",
    "button:has-text('Submit')",
    "button[data-automation-id='submit']",
]


class WorkdayAdapter(BaseATSAdapter):
    name = "workday"

    async def apply(
        self,
        page: "Page",
        listing: JobListing,
        info: PersonalInfo,
        cv_pdf: Path | None,
        cover_letter_pdf: Path | None,
    ) -> ApplicationResult:
        try:
            await page.goto(listing.apply_url, wait_until="networkidle", timeout=40_000)
            await asyncio.sleep(2)

            # Click "Apply" if we landed on a job description page
            apply_btn = await page.query_selector("a[data-automation-id='applyButton'], button:has-text('Apply')")
            if apply_btn:
                await apply_btn.click()
                await page.wait_for_load_state("networkidle", timeout=20_000)
                await asyncio.sleep(2)

            max_steps = 10
            for step in range(max_steps):
                log.debug("Workday step %d for %s", step + 1, listing.company)

                # Upload resume if input present
                if cv_pdf and cv_pdf.exists():
                    file_inp = await page.query_selector("input[type='file']")
                    if file_inp:
                        await file_inp.set_input_files(str(cv_pdf))
                        await asyncio.sleep(1)

                # Fill form fields
                await fill_form(page, listing_description=listing.description)

                # EEO self-identification: set all dropdowns to "prefer not to disclose"
                await self._handle_eeo(page)

                # Check if we're on the final review / submit step
                for sel in _SUBMIT_SELECTORS:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                        content = await page.content()
                        if any(k in content.lower() for k in ("submitted", "thank you", "confirmation")):
                            return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)
                        return ApplicationResult.failure(listing.id, "Final submit not confirmed")

                # Otherwise click Next
                clicked = False
                for sel in _NEXT_SELECTORS:
                    el = await page.query_selector(sel)
                    if el:
                        is_disabled = await el.get_attribute("disabled")
                        if is_disabled:
                            continue
                        await el.click()
                        await page.wait_for_load_state("networkidle", timeout=15_000)
                        await asyncio.sleep(1.5)
                        clicked = True
                        break
                if not clicked:
                    return ApplicationResult.failure(listing.id, "Could not advance Workday wizard")

            return ApplicationResult.failure(listing.id, "Workday: max steps reached")

        except Exception as exc:
            log.exception("Workday apply error for %s", listing.company)
            return ApplicationResult.failure(listing.id, str(exc))

    async def _handle_eeo(self, page: "Page") -> None:
        selects = await page.query_selector_all("select[data-automation-id]")
        for sel in selects:
            label = await sel.evaluate("e => e.closest('[data-automation-id]')?.getAttribute('data-automation-id') || ''")
            if any(k in label.lower() for k in ("gender", "race", "ethnicity", "veteran", "disability")):
                opts: list[str] = await sel.evaluate("e => Array.from(e.options).map(o => o.text)")
                for opt in opts:
                    if any(k in opt.lower() for k in ("prefer not", "decline", "not disclose")):
                        try:
                            await sel.select_option(label=opt)
                        except Exception:
                            pass
                        break
