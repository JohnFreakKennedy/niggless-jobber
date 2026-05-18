"""Lever ATS adapter."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page

from applicator.ats.base import ApplicationResult, BaseATSAdapter, PersonalInfo
from applicator.form_filler import fill_form
from parser.schema import JobListing

log = logging.getLogger(__name__)


def _strip_latex(text: str) -> str:
    text = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", text)
    return re.sub(r"\\[a-zA-Z]+", " ", text).strip()


class LeverAdapter(BaseATSAdapter):
    name = "lever"

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

            # Standard fields
            field_map = {
                "input[name='name']": info.get("name", "full"),
                "input[name='email']": info.get("email"),
                "input[name='phone']": info.get("phone"),
                "input[name='org']": (info.raw.get("work_history") or [{}])[0].get("company", ""),
                "input[name='urls[LinkedIn]'], input[placeholder*='LinkedIn']": info.get("linkedin_url"),
                "input[name='urls[GitHub]'], input[placeholder*='GitHub']": info.get("github_url"),
                "input[name='urls[Portfolio]'], input[placeholder*='website']": info.get("website_url"),
            }
            for selector, value in field_map.items():
                if not value:
                    continue
                el = await page.query_selector(selector)
                if el:
                    await el.fill(value)
                    await asyncio.sleep(0.15)

            # Upload CV
            if cv_pdf and cv_pdf.exists():
                await self._upload_file(page, "input[type='file']", cv_pdf)

            # Cover letter textarea
            cl_tex = None
            if cover_letter_pdf:
                cl_tex_path = cover_letter_pdf.with_suffix(".tex")
                if cl_tex_path.exists():
                    cl_tex = _strip_latex(cl_tex_path.read_text(encoding="utf-8"))
            cl_area = await page.query_selector("textarea[name='comments'], textarea[placeholder*='cover']")
            if cl_area and cl_tex:
                await cl_area.fill(cl_tex)

            # Heuristic fill for any remaining fields
            await fill_form(page, listing_description=listing.description)

            # Source dropdown
            src_select = await page.query_selector("select[name='source']")
            if src_select:
                try:
                    await src_select.select_option(label=listing.source.capitalize())
                except Exception:
                    pass

            # Submit
            submitted = await self._click(page, "button[type='submit'], input[type='submit']")
            if not submitted:
                return ApplicationResult.failure(listing.id, "Submit button not found")

            await page.wait_for_load_state("networkidle", timeout=15_000)
            content = await page.content()
            if any(k in content.lower() for k in ("thank you", "application received", "submitted")):
                return ApplicationResult.success(listing.id, cv_pdf, cover_letter_pdf)
            return ApplicationResult.failure(listing.id, "Submission not confirmed")

        except Exception as exc:
            log.exception("Lever apply error for %s", listing.company)
            return ApplicationResult.failure(listing.id, str(exc))
