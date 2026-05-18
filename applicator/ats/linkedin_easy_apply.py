"""LinkedIn Easy Apply adapter."""
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


class LinkedInEasyApplyAdapter(BaseATSAdapter):
    name = "linkedin_easy_apply"

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

            # Click "Easy Apply"
            easy_apply = await page.query_selector("button.jobs-apply-button, button:has-text('Easy Apply')")
            if not easy_apply:
                return ApplicationResult.failure(listing.id, "Easy Apply button not found")
            await easy_apply.click()
            await asyncio.sleep(1.5)

            max_steps = 8
            for step in range(max_steps):
                log.debug("LinkedIn Easy Apply step %d for %s", step + 1, listing.company)

                # Upload resume if visible
                if cv_pdf and cv_pdf.exists():
                    file_inp = await page.query_selector("input[type='file'][name*='file'], input[type='file']")
                    if file_inp:
                        is_visible = await file_inp.is_visible()
                        if is_visible:
                            await file_inp.set_input_files(str(cv_pdf))
                            await asyncio.sleep(1)

                await fill_form(page, listing_description=listing.description)

                # "Review" step -> Submit
                review_btn = await page.query_selector("button[aria-label*='Submit application'], button:has-text('Submit application')")
                if review_btn:
                    await review_btn.click()
                    await asyncio.sleep(2)
                    content = await page.content()
                    if any(k in content.lower() for k in ("application sent", "you applied", "successfully applied")):
                        return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)
                    return ApplicationResult.failure(listing.id, "Easy Apply submit not confirmed")

                # Next / Continue
                next_btn = await page.query_selector(
                    "button[aria-label='Continue to next step'], "
                    "button[aria-label='Next'], "
                    "button:has-text('Next'), "
                    "button:has-text('Continue')"
                )
                if not next_btn:
                    return ApplicationResult.failure(listing.id, "Easy Apply: no Next button found")
                await next_btn.click()
                await asyncio.sleep(1.5)

            return ApplicationResult.failure(listing.id, "Easy Apply: max steps reached")

        except Exception as exc:
            log.exception("LinkedIn Easy Apply error for %s", listing.company)
            return ApplicationResult.failure(listing.id, str(exc))
