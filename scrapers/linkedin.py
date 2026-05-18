"""
LinkedIn scraper -- Jobs Guest API + optional Playwright auth.

Strategy
--------
1. Use the public /jobs-guest/jobs/api endpoint (no session required) for the
   bulk of data collection.  This avoids the login wall that the full
   /jobs/search/ page shows to anonymous Playwright browsers.
2. Playwright (with saved Google profile or email/password) is used only to
   fetch richer job descriptions for the top results, and only when a session
   is available.
3. Early-exit: stop iterating keyword/location combos once max_results reached.
4. Consecutive-empty guard: if a location returns nothing twice in a row for
   the same keyword, skip remaining locations for that keyword.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

_GUEST_API = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
_DETAIL_BASE = "https://www.linkedin.com/jobs/view/"
_PAGE_LOAD_TIMEOUT = 30_000
# Stop trying more locations for a keyword after this many empties in a row
_MAX_CONSECUTIVE_EMPTY = 2
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _guest_params(keyword: str, location: str, start: int = 0) -> dict:
    params: dict = {
        "keywords": keyword,
        "start": start,
        "count": 25,
        "sortBy": "DD",
    }
    if location.lower() == "remote":
        params["f_WT"] = "2"
    elif location:
        params["location"] = location
    return params


def _parse_guest_html(html: str) -> list[dict]:
    """Parse the HTML fragment returned by the guest API."""
    soup = BeautifulSoup(html, "lxml")
    results: list[dict] = []
    for card in soup.find_all("div", class_=re.compile(r"base-search-card")):
        try:
            # Job ID from data-entity-urn="urn:li:jobPosting:123456"
            urn = card.get("data-entity-urn", "")
            job_id = urn.split(":")[-1] if urn else ""

            # Full link anchor
            a = card.find("a", class_=re.compile(r"base-card__full-link"))
            if not a:
                a = card.find("a", href=re.compile(r"/jobs/view/"))
            href = ""
            if a:
                href = a.get("href", "").split("?")[0]
                if not job_id:
                    m = re.search(r"-(\d+)$", href)
                    job_id = m.group(1) if m else ""
            if not job_id:
                continue

            clean_url = f"https://www.linkedin.com/jobs/view/{job_id}/"

            title_el = card.find(class_=re.compile(r"base-search-card__title"))
            title = title_el.get_text(strip=True) if title_el else ""

            company_el = card.find(class_=re.compile(r"base-search-card__subtitle"))
            company = company_el.get_text(strip=True) if company_el else ""

            loc_el = card.find(class_=re.compile(r"job-search-card__location"))
            location = loc_el.get_text(strip=True) if loc_el else ""

            salary_el = card.find(class_=re.compile(r"salary|compensation"))
            salary = salary_el.get_text(strip=True) if salary_el else ""

            date_el = card.find("time")
            posted = date_el.get("datetime", "") if date_el else ""

            if not title:
                continue

            results.append({
                "id": f"li-{job_id}",
                "title": title,
                "company": company,
                "url": clean_url,
                "apply_url": clean_url,
                "description": "",
                "location": location,
                "salary_raw": salary,
                "posted_at": posted,
                "skills": [],
            })
        except Exception as exc:
            log.debug("LinkedIn: card parse error: %s", exc)
    return results


class LinkedInScraper(BaseScraper):
    source = "linkedin"

    def __init__(self, keywords: list[str], locations: list[str], max_results: int = 50):
        super().__init__(keywords, locations, max_results)

    # ------------------------------------------------------------------
    # Guest API (no auth required)
    # ------------------------------------------------------------------

    async def _fetch_guest_page(self, keyword: str, location: str, start: int) -> list[dict]:
        params = _guest_params(keyword, location, start)
        url = _GUEST_API + "?" + urllib.parse.urlencode(params)
        try:
            resp = await self._get(url, headers=_HEADERS)
            if resp.status_code != 200:
                log.debug("LinkedIn guest API %d for %r/%r", resp.status_code, keyword, location)
                return []
            return _parse_guest_html(resp.text)
        except Exception as exc:
            log.warning("LinkedIn guest API error: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Playwright detail fetch (optional, best-effort)
    # ------------------------------------------------------------------

    async def _fetch_descriptions(self, results: list[dict]) -> None:
        """Fill in descriptions via Playwright if a session is available."""
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth as _Stealth
        except ImportError:
            return

        from scrapers.google_auth import make_context, profile_exists
        from storage.vault import get_secret

        has_creds = profile_exists() or bool(get_secret("LINKEDIN_EMAIL"))
        if not has_creds:
            log.debug("LinkedIn: no session available, skipping description fetch")
            return

        top = results[:10]
        async with async_playwright() as pw:
            ctx, browser = await make_context(
                pw,
                headless=True,
                viewport={"width": 1440, "height": 900},
                user_agent=_HEADERS["User-Agent"],
                locale="en-US",
            )
            await _Stealth().apply_stealth_async(ctx)
            page = await ctx.new_page()

            # Quick login attempt
            try:
                await page.goto("https://www.linkedin.com/login",
                                wait_until="domcontentloaded", timeout=_PAGE_LOAD_TIMEOUT)
                await asyncio.sleep(1)
                if any(p in page.url for p in ("/feed", "/mynetwork", "/jobs")):
                    log.info("LinkedIn: signed in via Google profile for description fetch")
                else:
                    email = get_secret("LINKEDIN_EMAIL")
                    password = get_secret("LINKEDIN_PASSWORD")
                    if email and password:
                        await page.wait_for_selector(
                            "#username, input[name='session_key']", timeout=10_000
                        )
                        await page.fill("#username, input[name='session_key']", email)
                        await page.fill("#password, input[name='session_password']", password)
                        await page.click("button[type='submit']")
                        await page.wait_for_load_state("networkidle", timeout=15_000)
            except Exception as exc:
                log.debug("LinkedIn: login skipped for descriptions: %s", exc)

            for item in top:
                try:
                    await page.goto(item["url"], wait_until="domcontentloaded",
                                    timeout=_PAGE_LOAD_TIMEOUT)
                    await asyncio.sleep(1)
                    desc = await page.evaluate("""() => {
                        const el = document.querySelector(
                            '.jobs-description__content, .description__text, ' +
                            '.show-more-less-html__markup, [class*="description"]'
                        );
                        return el ? el.innerText.slice(0, 3000) : '';
                    }""")
                    item["description"] = desc or ""
                except Exception as exc:
                    log.debug("LinkedIn: detail fetch failed: %s", exc)

            await ctx.close()
            if browser:
                await browser.close()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def search(self) -> list[dict]:
        seen_ids: set[str] = set()
        results: list[dict] = []

        for keyword in self.keywords:
            if len(results) >= self.max_results:
                break
            consecutive_empty = 0

            for location in self.locations:
                if len(results) >= self.max_results:
                    break
                if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY:
                    log.debug(
                        "LinkedIn: %d consecutive empty locations for %r, skipping rest",
                        consecutive_empty, keyword,
                    )
                    break

                page_start = 0
                location_count = 0
                while len(results) < self.max_results:
                    batch = await self._fetch_guest_page(keyword, location, page_start)
                    if not batch:
                        break
                    added = 0
                    for item in batch:
                        if item["id"] in seen_ids:
                            continue
                        seen_ids.add(item["id"])
                        results.append(item)
                        added += 1
                        location_count += 1
                        if len(results) >= self.max_results:
                            break
                    if len(batch) < 25 or added == 0:
                        break
                    page_start += 25
                    await asyncio.sleep(1.5)

                log.info(
                    "LinkedIn: %d listings from keyword=%r location=%r",
                    location_count, keyword, location,
                )
                if location_count == 0:
                    consecutive_empty += 1
                else:
                    consecutive_empty = 0

        if results:
            await self._fetch_descriptions(results)

        log.info("LinkedIn: %d total listings collected", len(results))
        return results[:self.max_results]
