"""Dou.ua scraper using Playwright + BeautifulSoup."""
from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

_BASE_URL = "https://jobs.dou.ua/vacancies/"
_DELAY_MIN = 3.0
_DELAY_MAX = 5.0


class DouScraper(BaseScraper):
    source = "dou"

    async def search(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth as _Stealth
        except ImportError:
            log.error("playwright not installed; skipping Dou scraper")
            return []

        results: list[dict] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="uk-UA",
            )
            await _Stealth().apply_stealth_async(ctx)
            page = await ctx.new_page()

            for keyword in self.keywords:
                for location in self.locations:
                    params: dict = {"search": keyword}
                    if location.lower() != "remote":
                        params["city"] = location
                    else:
                        params["remote"] = "remote"
                    url = _BASE_URL + "?" + urllib.parse.urlencode(params)
                    try:
                        await page.goto(url, wait_until="networkidle", timeout=30_000)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(2)
                        html = await page.content()
                        listings = self._parse_list(html, keyword)
                        for listing in listings:
                            if len(results) >= self.max_results:
                                break
                            detail = await self._fetch_detail(page, listing["url"])
                            listing["description"] = detail
                            results.append(listing)
                            await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
                    except Exception as exc:
                        log.error("Dou scrape error for %r: %s", url, exc)

            await browser.close()
        return results[:self.max_results]

    def _parse_list(self, html: str, keyword: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        for li in soup.select("li.l-vacancy"):
            try:
                a = li.select_one(".vt a")
                if not a:
                    continue
                title = a.get_text(strip=True)
                href = a.get("href", "")
                company_tag = li.select_one(".company a") or li.select_one(".company")
                company = company_tag.get_text(strip=True) if company_tag else ""
                location_tag = li.select_one(".cities")
                location = location_tag.get_text(strip=True) if location_tag else ""
                salary_tag = li.select_one(".salary")
                salary = salary_tag.get_text(strip=True) if salary_tag else ""
                date_tag = li.select_one(".date")
                date_str = date_tag.get_text(strip=True) if date_tag else ""
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
                log.warning("Dou list parse error: %s", exc)
        return items

    async def _fetch_detail(self, page, url: str) -> str:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            desc = soup.select_one("div.b-typo.vacancy-section")
            if desc:
                return desc.get_text(separator="\n", strip=True)
        except Exception as exc:
            log.warning("Dou detail fetch error for %r: %s", url, exc)
        return ""
