"""Indeed scraper using Playwright with stealth mode."""
from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

_BASE = "https://www.indeed.com"
_SEARCH = "https://www.indeed.com/jobs"
_DELAY_MIN = 5.0
_DELAY_MAX = 10.0


class IndeedScraper(BaseScraper):
    source = "indeed"

    async def search(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth as _Stealth
        except ImportError:
            log.error("playwright not installed; skipping Indeed scraper")
            return []

        from scrapers.google_auth import make_context, click_google_signin, profile_exists

        results: list[dict] = []
        async with async_playwright() as pw:
            ctx, browser = await make_context(
                pw,
                headless=True,
                viewport={"width": random.randint(1280, 1920), "height": random.randint(720, 1080)},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="en-US",
            )
            await _Stealth().apply_stealth_async(ctx)
            page = await ctx.new_page()

            # If the Google profile is available, sign in via Google on Indeed
            if profile_exists():
                try:
                    await page.goto("https://www.indeed.com/account/login", wait_until="domcontentloaded", timeout=20_000)
                    signed_in = await click_google_signin(page)
                    if signed_in:
                        log.info("Indeed: signed in via Google account")
                except Exception as exc:
                    log.debug("Indeed Google sign-in skipped: %s", exc)

            for keyword in self.keywords:
                for location in self.locations:
                    params = {
                        "q": keyword,
                        "l": location if location.lower() != "remote" else "remote",
                        "fromage": "1",  # posted within last day
                        "sort": "date",
                    }
                    url = _SEARCH + "?" + urllib.parse.urlencode(params)
                    page_offset = 0

                    while len(results) < self.max_results:
                        full_url = url + (f"&start={page_offset}" if page_offset > 0 else "")
                        try:
                            await page.goto(full_url, wait_until="domcontentloaded", timeout=30_000)
                            await asyncio.sleep(random.uniform(2, 4))

                            # Check for CAPTCHA
                            if await page.query_selector("#captcha-box, iframe[src*='captcha']"):
                                log.warning("Indeed CAPTCHA detected; attempting solve")
                                await self._solve_captcha(page)

                            html = await page.content()
                            listings, has_more = self._parse_list(html)
                            for listing in listings:
                                if len(results) >= self.max_results:
                                    break
                                detail = await self._fetch_detail(page, listing["url"])
                                listing["description"] = detail
                                results.append(listing)
                                await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
                            if not has_more or not listings:
                                break
                            page_offset += 10
                            await asyncio.sleep(random.uniform(8, 15))
                        except Exception as exc:
                            log.error("Indeed search error: %s", exc)
                            break

            await browser.close()
        return results[:self.max_results]

    def _parse_list(self, html: str) -> tuple[list[dict], bool]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        for card in soup.select("div.job_seen_beacon, div[data-jk]"):
            try:
                title_tag = card.select_one("h2.jobTitle span[title], h2.jobTitle a span")
                title = title_tag.get("title") or title_tag.get_text(strip=True) if title_tag else ""
                a_tag = card.select_one("h2.jobTitle a")
                href = a_tag.get("href", "") if a_tag else ""
                if href and not href.startswith("http"):
                    href = _BASE + href
                company_tag = card.select_one("span.companyName, [data-testid='company-name']")
                company = company_tag.get_text(strip=True) if company_tag else ""
                location_tag = card.select_one("div.companyLocation, [data-testid='text-location']")
                location = location_tag.get_text(strip=True) if location_tag else ""
                salary_tag = card.select_one("div.salary-snippet-container, [data-testid='attribute_snippet_testid']")
                salary = salary_tag.get_text(strip=True) if salary_tag else ""
                date_tag = card.select_one("span.date")
                date_str = date_tag.get_text(strip=True) if date_tag else ""
                if not title or not href:
                    continue
                items.append({
                    "title": title,
                    "company": company,
                    "url": href,
                    "apply_url": href,
                    "description": "",
                    "location": location,
                    "salary_raw": salary,
                    "posted_at": date_str,
                })
            except Exception as exc:
                log.warning("Indeed list parse error: %s", exc)

        has_more = bool(soup.select_one('a[aria-label="Next Page"], [data-testid="pagination-page-next"]'))
        return items, has_more

    async def _fetch_detail(self, page, url: str) -> str:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await asyncio.sleep(random.uniform(1, 2))
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            desc = soup.select_one("div#jobDescriptionText, div.jobsearch-JobComponent-description")
            if desc:
                return desc.get_text(separator="\n", strip=True)
        except Exception as exc:
            log.warning("Indeed detail error for %r: %s", url, exc)
        return ""

    async def _solve_captcha(self, page) -> None:
        from applicator.captcha import solve_captcha, CaptchaType
        try:
            await solve_captcha(page, CaptchaType.RECAPTCHA_V2)
        except Exception as exc:
            log.warning("CAPTCHA solve failed on Indeed: %s", exc)
