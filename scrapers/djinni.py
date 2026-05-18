"""Djinni.co scraper using Playwright with session cookie auth."""
from __future__ import annotations

import asyncio
import json
import logging
import random
import urllib.parse
from pathlib import Path

from bs4 import BeautifulSoup

from scrapers.base import BaseScraper

log = logging.getLogger(__name__)

_SESSION_FILE = Path.home() / ".niggless-jobber" / "djinni_session.json"
_BASE = "https://djinni.co"
_JOBS_URL = "https://djinni.co/jobs/"
_DELAY_MIN = 4.0
_DELAY_MAX = 7.0


class DjinniScraper(BaseScraper):
    source = "djinni"

    def __init__(self, keywords: list[str], locations: list[str], max_results: int = 40):
        super().__init__(keywords, locations, max_results)
        self._cookies: list[dict] = []

    def _load_session(self) -> bool:
        if _SESSION_FILE.exists():
            try:
                self._cookies = json.loads(_SESSION_FILE.read_text())
                return bool(self._cookies)
            except Exception:
                pass
        return False

    def _save_session(self, cookies: list[dict]) -> None:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps(cookies))
        self._cookies = cookies

    async def search(self) -> list[dict]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth as _Stealth
        except ImportError:
            log.error("playwright not installed; skipping Djinni scraper")
            return []

        results: list[dict] = []
        async with async_playwright() as pw:
            from scrapers.google_auth import make_context
            ctx, browser = await make_context(
                pw,
                headless=True,
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            )
            await _Stealth().apply_stealth_async(ctx)

            if self._load_session():
                await ctx.add_cookies(self._cookies)

            page = await ctx.new_page()

            # Validate session
            await page.goto("https://djinni.co/", wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(2)
            if "login" in page.url or not await page.query_selector("a.profile-link, .navbar-user, .userpic-wrapper"):
                await self._login(page, ctx)

            for keyword in self.keywords:
                for location in self.locations:
                    params: dict = {"primary_keyword": keyword.split()[0]}
                    if location.lower() not in ("remote", ""):
                        params["region"] = location
                    else:
                        params["employment"] = "remote"
                    search_url = _JOBS_URL + "?" + urllib.parse.urlencode(params)
                    page_num = 1

                    while len(results) < self.max_results:
                        url = search_url + (f"&page={page_num}" if page_num > 1 else "")
                        try:
                            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                            html = await page.content()
                            listings, has_next = self._parse_list(html)
                            for listing in listings:
                                if len(results) >= self.max_results:
                                    break
                                detail = await self._fetch_detail(page, _BASE + listing["url"])
                                listing["description"] = detail["description"]
                                listing["apply_url"] = detail["apply_url"] or (_BASE + listing["url"])
                                listing["url"] = _BASE + listing["url"]
                                results.append(listing)
                                await asyncio.sleep(random.uniform(_DELAY_MIN, _DELAY_MAX))
                            if not has_next:
                                break
                            page_num += 1
                        except Exception as exc:
                            log.error("Djinni search error: %s", exc)
                            break

            await ctx.close()
            if browser:
                await browser.close()
        return results[:self.max_results]

    async def _login(self, page, ctx) -> None:
        from scrapers.google_auth import click_google_signin, profile_exists

        await page.goto("https://djinni.co/login?from=frontpage_main", wait_until="domcontentloaded")

        # Prefer Google sign-in when the profile is available
        if profile_exists():
            signed_in = await click_google_signin(page)
            if signed_in:
                await page.wait_for_load_state("networkidle")
                cookies = await ctx.cookies()
                self._save_session(cookies)
                log.info("Djinni: signed in via Google account")
                return

        # Fall back to email/password
        from storage.vault import get_secret
        email = get_secret("DJINNI_EMAIL")
        password = get_secret("DJINNI_PASSWORD")
        if not email or not password:
            log.warning(
                "Djinni: no credentials in vault and no Google profile. Scraping as anonymous."
            )
            return
        try:
            await asyncio.sleep(2)
            # Some Djinni layouts show a "Sign in with email" button before the form
            email_toggle = await page.query_selector(
                "a[href*='email'], button:has-text('email'), a:has-text('email')"
            )
            if email_toggle:
                await email_toggle.click()
                await asyncio.sleep(1)

            await page.wait_for_selector(
                "input[name='email'], input[type='email'], input[id='id_username']",
                timeout=20_000,
            )
            email_field = await page.query_selector(
                "input[name='email'], input[type='email'], input[id='id_username']"
            )
            if email_field:
                await email_field.fill(email)
            await asyncio.sleep(random.uniform(0.4, 0.9))

            pwd_field = await page.query_selector(
                "input[name='password'], input[type='password'], input[id='id_password']"
            )
            if pwd_field:
                await pwd_field.fill(password)
            await asyncio.sleep(random.uniform(0.3, 0.7))

            submit = await page.query_selector("button[type='submit']")
            if submit:
                await submit.click()
            await page.wait_for_load_state("networkidle", timeout=25_000)
            cookies = await ctx.cookies()
            self._save_session(cookies)
            log.info("Djinni login successful.")
        except Exception as exc:
            log.warning("Djinni login failed: %s — scraping as anonymous", exc)

    def _parse_list(self, html: str) -> tuple[list[dict], bool]:
        soup = BeautifulSoup(html, "lxml")
        items = []
        for li in soup.select("li.list-jobs__item"):
            try:
                a = li.select_one(".job-list-item__title a")
                if not a:
                    continue
                href = a.get("href", "")
                title = a.get_text(strip=True)
                company_tag = li.select_one(".job-list-item__company")
                company = company_tag.get_text(strip=True) if company_tag else ""
                location_tag = li.select_one(".location-text")
                location = location_tag.get_text(strip=True) if location_tag else ""
                salary_tag = li.select_one(".public-salary-item")
                salary = salary_tag.get_text(strip=True) if salary_tag else ""
                date_tag = li.select_one(".text-muted")
                date_str = date_tag.get_text(strip=True) if date_tag else ""
                items.append({
                    "title": title,
                    "company": company,
                    "url": href,
                    "apply_url": "",
                    "description": "",
                    "location": location,
                    "salary_raw": salary,
                    "posted_at": date_str,
                })
            except Exception as exc:
                log.warning("Djinni list parse error: %s", exc)

        has_next = bool(soup.select_one("a[rel='next'], .pagination .next:not(.disabled)"))
        return items, has_next

    async def _fetch_detail(self, page, url: str) -> dict:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            desc_tag = soup.select_one(".job-description")
            description = desc_tag.get_text(separator="\n", strip=True) if desc_tag else ""
            apply_tag = soup.select_one("a.btn-apply")
            apply_url = apply_tag.get("href", "") if apply_tag else ""
            return {"description": description, "apply_url": apply_url}
        except Exception as exc:
            log.warning("Djinni detail error for %r: %s", url, exc)
            return {"description": "", "apply_url": ""}
